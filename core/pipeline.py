"""Job orchestration: files in -> cleaned workbooks, analysis workbook, HTML.

The GUI, a CLI or a scheduled script all call :func:`run_job`.  Progress and
cancellation go through plain callables, so nothing here depends on Qt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import pandas as pd

from . import analysis as an
from .chart_png import plot_daily_bars, plot_metric_pair, plot_unit_ranking
from .custom_charts import build_charts
from .dictionary import ColumnDictionary
from .excel_writer import (
    write_analysis_workbook,
    write_cleaned_workbook,
    write_entity_workbook,
)
from .html_report import build_payload, write_html_report
from .loader import LoadedTable, load_table
from .diagnostics import guess_roles, run_diagnostics
from .models import (
    DIAGNOSTIC_FEATURES,
    APP_NAME,
    APP_VERSION,
    ColumnSpec,
    ContentScope,
    Profile,
)
from .rules import collect_flags
from .wide import (
    WideLayout,
    alarm_summary,
    analyse_layout,
    guess_alarm_pattern,
    guess_entity_pattern,
    metric_specs,
    split_tables,
    suggest_wide_metrics,
)

Progress = Callable[[int, str], None]
Cancelled = Callable[[], bool]


def _noop(pct: int, msg: str) -> None:      # pragma: no cover
    pass


def _never() -> bool:                        # pragma: no cover
    return False


@dataclass
class ScanResult:
    tables: list[LoadedTable] = field(default_factory=list)
    specs: dict[str, ColumnSpec] = field(default_factory=dict)
    dictionary: Optional[ColumnDictionary] = None
    messages: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)

    # wide files (all units in one file)
    layout: Optional[WideLayout] = None
    layouts: dict[str, WideLayout] = field(default_factory=dict)
    entity_tables: list[LoadedTable] = field(default_factory=list)
    mspecs: dict[str, ColumnSpec] = field(default_factory=dict)

    @property
    def is_wide(self) -> bool:
        return self.layout is not None and self.layout.is_wide

    @property
    def analysis_tables(self) -> list[LoadedTable]:
        """What the cross-unit comparison should run on."""
        return self.entity_tables or self.tables

    @property
    def analysis_specs(self) -> dict[str, ColumnSpec]:
        return self.mspecs or self.specs

    @property
    def display_specs(self) -> dict[str, ColumnSpec]:
        """What the Columns tab shows: metrics (not 689 raw columns) + plant."""
        if not self.is_wide:
            return self.specs
        out = dict(self.mspecs)
        for c in self.layout.plant_columns:
            if c in self.specs:
                out[c] = self.specs[c]
        return out

    @property
    def entities(self) -> list[str]:
        out = []
        for t in self.analysis_tables:
            if t.entity not in out:
                out.append(t.entity)
        return out

    @property
    def numeric_keys(self) -> list[str]:
        if self.is_wide:
            return [m for m in self.layout.metrics
                    if self.mspecs.get(m) is None or self.mspecs[m].is_numeric]
        return an.common_numeric_columns(self.tables)


@dataclass
class JobResult:
    outputs: list[Path] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    analysis: Optional[an.AnalysisResult] = None
    scan: Optional[ScanResult] = None
    cancelled: bool = False


# --------------------------------------------------------------------------- #
def build_specs(
    tables: list[LoadedTable],
    dictionary: ColumnDictionary,
    profile: Profile,
) -> tuple[dict[str, ColumnSpec], list[str]]:
    """One ColumnSpec per distinct raw column across all files."""
    numeric_flags: dict[str, bool] = {}
    order: list[str] = []
    for t in tables:
        for c in t.df.columns:
            key = str(c)
            if key not in numeric_flags:
                order.append(key)
            numeric_flags[key] = numeric_flags.get(key, True) and (
                key in t.numeric_columns or key == t.time_column
            )
    specs: dict[str, ColumnSpec] = {}
    for key in order:
        entry, matched = dictionary.find(key)
        if entry is None:
            from .dictionary import split_unit

            nm, un = split_unit(key)
            name, unit, note, kind, shared = (nm or key), un, "", "", False
        else:
            name, unit, note, kind, shared = (entry.name, entry.unit, entry.note,
                                              entry.kind, entry.shared)
        # a unit row in the file itself beats the dictionary if the latter has none
        if not unit:
            for t in tables:
                candidate = t.unit_row.get(key, "").strip()
                if candidate and candidate not in {"-", "--", "なし"}:
                    unit = candidate
                    break
        spec = ColumnSpec(raw=key, name=name, unit=unit, note=note, kind=kind,
                          shared=shared, is_numeric=numeric_flags.get(key, True),
                          matched=matched)
        override = profile.columns.get(key)
        if override:
            for f in ("name", "unit", "note", "include", "decimals"):
                if f in override and override[f] is not None:
                    setattr(spec, f, override[f])
        specs[key] = spec
    return specs, dictionary.unmatched()


def apply_column_filter(specs: dict[str, ColumnSpec], profile: Profile,
                        tables: list[LoadedTable]) -> int:
    """Tick / untick columns according to the profile's name filter.

    Returns how many columns ended up hidden.  The time column (and, when
    asked, the first column of the file) is always kept - it is the label
    every other value is read against.
    """
    flt = profile.column_filter
    if not flt.enabled:
        return 0
    protected: set[str] = set()
    for t in tables:
        if t.time_column:
            protected.add(str(t.time_column))
        if flt.keep_first_column and len(t.df.columns):
            protected.add(str(t.df.columns[0]))
    hidden = 0
    for key, spec in specs.items():
        if key in protected:
            spec.include = True
            continue
        verdict = flt.matches(spec.raw, spec.name)
        if verdict is None:
            continue
        spec.include = bool(verdict)
        if not spec.include:
            hidden += 1
    return hidden


def apply_time_filter(tables: list[LoadedTable], opts: Any) -> tuple[int, int]:
    """Trim every table to the analysis period.  Returns (kept, dropped) rows.

    Applied straight after reading, so *every* output - cleaned workbooks,
    analysis workbook, HTML, charts - covers exactly the chosen window.
    """
    if not getattr(opts, "time_filter", False):
        return (sum(len(t.df) for t in tables), 0)
    lo = str(opts.time_from or "").strip()
    hi = str(opts.time_to or "").strip()
    if not lo and not hi:
        return (sum(len(t.df) for t in tables), 0)
    kept = dropped = 0
    for t in tables:
        if t.time_column is None or t.time_column not in t.df.columns:
            kept += len(t.df)
            continue
        ts = pd.to_datetime(t.df[t.time_column], errors="coerce")
        mask = ts.notna()
        if lo:
            mask &= ts >= pd.Timestamp(lo)
        if hi:
            mask &= ts <= pd.Timestamp(hi)
        before = len(t.df)
        t.df = t.df.loc[mask].reset_index(drop=True)
        kept += len(t.df)
        dropped += before - len(t.df)
    return (kept, dropped)


def scan(
    files: list[Path],
    profile: Profile,
    progress: Progress = _noop,
    cancelled: Cancelled = _never,
    limit: Optional[int] = None,
) -> ScanResult:
    """Read the files and translate their headers - no output written."""
    res = ScanResult()
    try:
        res.dictionary = ColumnDictionary.load(profile.dictionary)
        if res.dictionary.source:
            res.messages.append(
                f"Dictionary: {res.dictionary.source.name} "
                f"({len(res.dictionary)} entries, columns detected: "
                f"{res.dictionary.detected})"
            )
    except (FileNotFoundError, ValueError) as exc:
        res.dictionary = ColumnDictionary()
        res.messages.append(f"Dictionary not loaded: {exc}")

    # Never treat our own output, or the dictionary itself, as input data.
    dict_path = None
    if profile.dictionary.path:
        try:
            dict_path = Path(profile.dictionary.path).resolve()
        except OSError:
            dict_path = None
    suffix = (profile.output.suffix or "").strip()
    keep: list[Path] = []
    skipped: list[str] = []
    seen_stems: dict[str, Path] = {}
    for f in files:
        f = Path(f)
        try:
            same_as_dict = dict_path is not None and f.resolve() == dict_path
        except OSError:
            same_as_dict = False
        if same_as_dict:
            skipped.append(f"{f.name} (this is the dictionary)")
            continue
        if suffix and f.stem.endswith(suffix):
            skipped.append(f"{f.name} (previous output)")
            continue
        # the same day exported twice (.csv and .xlsx) is the same data
        if f.stem in seen_stems:
            skipped.append(f"{f.name} (same data as {seen_stems[f.stem].name})")
            continue
        seen_stems[f.stem] = f
        keep.append(f)
    if skipped:
        res.messages.append("Skipped as input: " + ", ".join(skipped))
    files = keep

    todo = files[:limit] if limit else files
    for i, f in enumerate(todo, start=1):
        if cancelled():
            break
        progress(int(100 * i / max(1, len(todo))), f"Reading {Path(f).name}")
        try:
            t = load_table(Path(f), profile.reader, profile.identity)
            res.tables.append(t)
            note = f" ({'; '.join(t.notes)})" if t.notes else ""
            res.messages.append(
                f"{Path(f).name}: {len(t.df)} rows, {t.df.shape[1]} columns, "
                f"entity={t.entity}, time={t.time_column or 'none'}{note}"
            )
        except Exception as exc:                       # one bad file must not stop the batch
            res.messages.append(f"FAILED {Path(f).name}: {type(exc).__name__}: {exc}")

    res.specs, res.unmatched = build_specs(res.tables, res.dictionary, profile)
    apply_column_filter(res.specs, profile, res.tables)

    # ---- wide files: one file holds every unit --------------------------- #
    if res.tables and not profile.identity.entity_from_column:
        guess = guess_entity_pattern(res.tables[0].df.columns)
        if guess:
            profile.identity.entity_from_column = guess
            res.messages.append(
                f"Detected a per-unit column pattern: {guess}  "
                "(set it in the profile to make this explicit)")
    if res.tables and not profile.identity.alarm_pattern:
        guess = guess_alarm_pattern(res.tables[0].df, res.tables[0].df.columns)
        if guess:
            profile.identity.alarm_pattern = guess
            res.messages.append(f"Detected binary status-bit columns: {guess}")
    if profile.identity.entity_from_column:
        for t in res.tables:
            lay = analyse_layout(t.df.columns, profile.identity, t.time_column)
            res.layouts[str(t.path)] = lay
            if lay.is_wide and res.layout is None:
                res.layout = lay
            if lay.is_wide:
                res.entity_tables.extend(split_tables(t, lay))
        if res.layout is not None and res.layout.is_wide:
            res.mspecs = metric_specs(res.layout, res.specs,
                                      profile.identity.strip_entity_in_name)
            apply_column_filter(res.mspecs, profile, res.tables)
            for key, spec in res.mspecs.items():
                override = profile.columns.get(key)
                if override:
                    for f in ("name", "unit", "note", "include", "decimals"):
                        if f in override and override[f] is not None:
                            setattr(spec, f, override[f])
            for e in res.entity_tables:
                e.notes.append(f"entity {e.entity}")
            res.messages.append(f"Wide layout: {res.layout.describe()}")
            shared = [m for m, s in res.mspecs.items() if s.shared]
            if shared:
                names = ", ".join(f"{res.mspecs[m].name}" for m in shared)
                res.messages.append(
                    f"Shared sensors (identical for every unit, not comparable): {names}")

    alarm_cols = set(res.layout.alarm_columns) if res.layout else set()
    time_cols = {t.time_column for t in res.tables if t.time_column}
    real_misses = [c for c in res.unmatched if c not in alarm_cols and c not in time_cols]
    if alarm_cols:
        res.messages.append(
            f"{len(alarm_cols)} status/alarm bit(s) have no dictionary entry - "
            "summarised on the STATUS_ALARMS sheet instead")
    if real_misses:
        res.messages.append(
            f"{len(real_misses)} column(s) not in the dictionary: "
            + ", ".join(real_misses[:12]) + ("..." if len(real_misses) > 12 else "")
        )
    res.unmatched = real_misses
    return res


# --------------------------------------------------------------------------- #
def run_job(
    files: list[Path],
    profile: Profile,
    progress: Progress = _noop,
    cancelled: Cancelled = _never,
    scan_result: Optional[ScanResult] = None,
) -> JobResult:
    out = JobResult()
    stamp = datetime.now().strftime("%Y%m%d_%H%M")

    # Fail fast: reading a 689-column day costs ~25 s, so do not do it only to
    # discover that no output type was ticked.
    if not profile.output.any_output():
        out.messages.append(
            "Nothing was produced: no output type is ticked on the Output tab.")
        return out

    sr = scan_result or scan(files, profile, lambda p, m: progress(int(p * 0.3), m), cancelled)
    out.scan = sr
    out.messages.extend(sr.messages)
    if cancelled():
        out.cancelled = True
        return out
    if not sr.tables:
        out.messages.append("Nothing to do: no file could be read.")
        return out

    if profile.analysis.time_filter:
        kept, dropped = apply_time_filter(sr.tables, profile.analysis)
        if sr.entity_tables:
            apply_time_filter(sr.entity_tables, profile.analysis)
        window = " → ".join(x for x in (profile.analysis.time_from,
                                        profile.analysis.time_to) if x)
        out.messages.append(
            f"Period filter {window}: {kept} row(s) kept, {dropped} dropped")
        if not kept:
            out.messages.append(
                "Nothing to do: no row falls inside the selected period.")
            return out

    specs = sr.specs
    opts = profile.output
    base_dir = Path(opts.out_dir) if opts.out_dir else Path(files[0]).parent
    base_dir.mkdir(parents=True, exist_ok=True)
    png_dir = base_dir / "charts"

    aspecs = sr.analysis_specs
    if sr.is_wide:
        metrics = [m for m in profile.analysis.metrics if m in sr.layout.metrics]
        if not metrics:
            metrics = suggest_wide_metrics(sr.layout, sr.mspecs, sr.entity_tables,
                                           profile.analysis.skip_shared_metrics)
    else:
        metrics = [m for m in profile.analysis.metrics if m] or an.suggest_metrics(sr.tables)
    # a hidden column is not analysed either
    metrics = [m for m in metrics if aspecs.get(m) is None or aspecs[m].include]

    # what the user picked on the Columns tab, before any per-output override
    base_include = {k: bool(sp.include) for k, sp in specs.items()}
    base_include.update({k: bool(sp.include) for k, sp in aspecs.items()})

    def channels_for(scope: ContentScope) -> list[str]:
        """The analysable channels one output should contain."""
        if scope is ContentScope.SELECTED:
            return list(metrics)
        pool = sr.numeric_keys
        if scope is ContentScope.VISIBLE:
            pool = [c for c in pool if base_include.get(c, True)]
        # keep the user's own order first, then the rest
        return list(metrics) + [c for c in pool if c not in metrics]

    def set_include(scope: ContentScope, keep: Iterable[str] = ()) -> None:
        """Point ``spec.include`` at what this output is supposed to show.

        The specs are shared objects (the Columns tab edits the same
        instances), so :func:`restore_include` must always follow.
        """
        keep = set(keep)
        wanted: Optional[set[str]] = None
        if scope is ContentScope.SELECTED:
            wanted = set(metrics)
            if sr.is_wide:            # raw column belongs to a selected metric?
                wanted |= {raw for (ent, met), raw in sr.layout.cells.items()
                           if met in wanted}
        for key, sp in list(specs.items()) + list(aspecs.items()):
            if key in keep:
                sp.include = True
            elif scope is ContentScope.ALL:
                sp.include = True
            elif wanted is not None:
                sp.include = key in wanted
            else:
                sp.include = base_include.get(key, True)

    def restore_include() -> None:
        for key, sp in list(specs.items()) + list(aspecs.items()):
            sp.include = base_include.get(key, True)

    protected: set[str] = set()
    for t in sr.tables:
        if t.time_column:
            protected.add(str(t.time_column))
    if profile.column_filter.keep_first_column:
        for t in sr.tables:
            if len(t.df.columns):
                protected.add(str(t.df.columns[0]))

    all_alarms: list[pd.DataFrame] = []

    # ---- per-file cleaned workbooks ------------------------------------- #
    n = len(sr.tables)
    cleaned_scope = opts.scope("cleaned")
    if opts.write_excel:
        set_include(cleaned_scope, protected)
        out.messages.append(
            f"Cleaned workbooks: {cleaned_scope.label().lower()} "
            f"({sum(1 for sp in sr.display_specs.values() if sp.include)} of "
            f"{len(sr.display_specs)} channels)")
    for i, t in enumerate(sr.tables, start=1):
        if cancelled():
            out.cancelled = True
            return out
        progress(30 + int(35 * i / max(1, n)), f"Writing {t.path.stem}{opts.suffix}.xlsx")
        lay = sr.layouts.get(str(t.path))
        alarms = None
        if lay is not None and lay.alarm_columns and profile.analysis.make_alarms:
            alarms = alarm_summary(t, lay, specs)
            if alarms is not None and not alarms.empty:
                all_alarms.append(alarms)
        if not opts.write_excel:
            continue
        if not len(t.df):
            # the period filter can empty a file completely
            out.messages.append(f"Skipped {t.path.name}: no row inside the period")
            continue
        rep_rules = [r for r in profile.rules
                     if r.kind.value not in set(profile.analysis.flag_exclude)]
        flags = (collect_flags(t.df, specs, rep_rules, t.time_column, t.entity)
                 if profile.analysis.make_flags and not (lay and lay.is_wide) else [])
        flags_df = pd.DataFrame(
            [{"Row": f.row, "Time": f.time, "Unit": f.entity, "Column": f.column,
              "Rule": f.label, "Value": f.value} for f in flags]
        )
        info = {
            "Generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "Tool": f"{APP_NAME} {APP_VERSION}",
            "Profile": profile.name,
            "Source file": str(t.path),
            "Rows": len(t.df),
            "Columns": t.df.shape[1],
            "Entity": t.entity,
            "Date": t.day or "",
            "Time column": t.time_column or "(none)",
            "Encoding": t.encoding,
            "Delimiter": {"\t": "TAB"}.get(t.delimiter, t.delimiter),
            "Header row in source": t.header_row + 1,
            "Dictionary": (str(sr.dictionary.source) if sr.dictionary and sr.dictionary.source
                           else "(none)"),
            "Reader notes": "; ".join(t.notes),
            "Untranslated columns": " | ".join(
                c for c in sr.unmatched if c in set(map(str, t.df.columns))
            ),
        }
        target = base_dir / f"{t.path.stem}{opts.suffix}.xlsx"
        try:
            if lay is not None and lay.is_wide and opts.split_sheets_by_entity:
                info["Layout"] = lay.describe()
                write_entity_workbook(
                    target, t, lay, specs, sr.mspecs, profile.rules, opts, info,
                    alarms=alarms,
                    header_template=profile.dictionary.header_template,
                    keep_raw_row=profile.dictionary.keep_raw_row,
                    chart_metrics=metrics,
                )
            else:
                write_cleaned_workbook(
                    target, t.df, specs, profile.rules, opts, t.time_column, info,
                    flags=flags_df, chart_metrics=metrics,
                    header_template=profile.dictionary.header_template,
                    keep_raw_row=profile.dictionary.keep_raw_row,
                )
            out.outputs.append(target)
            if lay is not None and lay.is_wide and opts.split_sheets_by_entity:
                out.messages.append(
                    f"Wrote {target.name} ({len(lay.entities)} unit sheets"
                    + (", PLANT" if lay.plant_columns else "")
                    + (f", {len(alarms)} active status bits" if alarms is not None
                       and not alarms.empty else "") + ")")
            else:
                out.messages.append(f"Wrote {target.name} ({len(flags)} rule hits)")
        except Exception as exc:
            out.messages.append(f"FAILED writing {target.name}: {type(exc).__name__}: {exc}")

    # ---- cross-unit analysis -------------------------------------------- #
    restore_include()
    progress(70, "Cross-unit analysis")

    # The analysis workbook and the HTML report can hold different amounts of
    # data; compute the union once and tell each output which part is its own.
    analysis_scope = opts.scope("analysis")
    html_scope = opts.scope("html")
    analysis_channels = channels_for(analysis_scope)
    if analysis_scope is not ContentScope.SELECTED and opts.analysis_max_metrics > 0:
        if len(analysis_channels) > opts.analysis_max_metrics:
            dropped = analysis_channels[opts.analysis_max_metrics:]
            analysis_channels = analysis_channels[:opts.analysis_max_metrics]
            out.messages.append(
                f"Analysis workbook capped at {opts.analysis_max_metrics} channels "
                f"({len(dropped)} left out: {', '.join(dropped[:6])}"
                + ("..." if len(dropped) > 6 else "")
                + ") - raise 'Max channels in the analysis workbook' to include them")
    html_channels = channels_for(html_scope) if opts.write_html else list(analysis_channels)
    if opts.html_extra_channels >= 0:
        extra_only = [c for c in html_channels if c not in analysis_channels]
        if len(extra_only) > opts.html_extra_channels:
            kept = set(extra_only[:opts.html_extra_channels])
            html_channels = [c for c in html_channels
                             if c in analysis_channels or c in kept]
            out.messages.append(
                f"HTML report capped at {opts.html_extra_channels} extra channel(s)")
    # The irradiance + temperature graph is drawn in the page from channels
    # that travel with it, so those two have to be there whatever the scope and
    # the cap say.  They are asked for by name, not decoration, so they are
    # added after the cap.
    html_roles = guess_roles(aspecs, profile.analysis)
    if opts.write_html and profile.analysis.wants("weather", "html"):
        for role in ("irradiance", "temp"):
            key = html_roles.get(role, "")
            if key and key in aspecs and key not in html_channels:
                html_channels.append(key)
                out.messages.append(
                    f"HTML report: {role} channel {key} added for the weather graph")

    profile.analysis.metrics = analysis_channels
    result = an.run_analysis(sr.analysis_tables, aspecs, profile.analysis)
    if all_alarms:
        result.alarms = pd.concat(all_alarms, ignore_index=True)
    out.analysis = result
    # ---- diagnostics (only what some output asked for) ------------------- #
    if any(profile.analysis.any_target(f) for f in DIAGNOSTIC_FEATURES):
        progress(74, "Diagnostics")
        try:
            result.diagnostics = run_diagnostics(
                sr.analysis_tables, aspecs, analysis_channels, profile.analysis,
                lang=("ja" if opts.html_language == "ja" else "en"))
            d = result.diagnostics
            made = [k for k in ("attribution", "availability", "efficiency",
                                "performance", "scatter", "tidy")
                    if getattr(d, k) is not None]
            if made:
                out.messages.append("Diagnostics: " + ", ".join(made))
            for note in d.notes:
                out.messages.append("Diagnostics: " + note)
            for line in d.findings:
                out.messages.append("  * " + line)
        except Exception as exc:
            out.messages.append(
                f"Diagnostics failed: {type(exc).__name__}: {exc}")

    out.messages.append(
        f"Analysis workbook: {analysis_scope.label().lower()} "
        f"({len(analysis_channels)} channel(s)) · HTML report: "
        f"{html_scope.label().lower()} ({len(html_channels)} channel(s))")

    pngs: dict[str, Path] = {}
    if opts.write_png and result.matrices:
        progress(78, "Rendering charts")
        for m in result.matrices:
            try:
                made = plot_metric_pair(m, png_dir, stamp)
                if made:
                    pngs[m.metric] = made[0]
                    out.outputs.extend(made)
            except Exception as exc:
                out.messages.append(f"Chart failed for {m.title}: {exc}")
        if result.daily is not None and not result.daily.empty:
            many = len(sr.entities) > 8
            for m in result.matrices[:4]:
                try:
                    if many:
                        p = plot_unit_ranking(
                            result.daily, m.title,
                            png_dir / f"{stamp}_rank_{m.metric[:20]}.png",
                            value_col="Total", unit=m.unit)
                    else:
                        p = plot_daily_bars(result.daily, m.title,
                                            png_dir / f"{stamp}_daily_{m.metric[:20]}.png")
                    if p:
                        out.outputs.append(p)
                except Exception as exc:
                    out.messages.append(f"Ranking chart failed for {m.title}: {exc}")

    custom: list[tuple[str, Path]] = []
    if profile.charts and (opts.write_png or opts.write_html or opts.write_excel):
        progress(82, "Rendering user charts")
        try:
            custom = build_charts(sr.analysis_tables, profile.charts, aspecs,
                                  png_dir, stamp, opts.max_html_points)
            for title, path in custom:
                out.outputs.append(path)
            if custom:
                out.messages.append(f"{len(custom)} user chart(s) rendered")
        except Exception as exc:
            out.messages.append(f"User charts failed: {type(exc).__name__}: {exc}")

    entities = ", ".join(sr.entities)
    days = ", ".join(sorted({t.day for t in sr.tables if t.day}))
    info = {
        "Generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "Tool": f"{APP_NAME} {APP_VERSION}",
        "Profile": profile.name,
        "Files": len(sr.tables),
        "Units": entities,
        "Dates": days,
        "Metrics compared": ", ".join(
            (aspecs[m].name if m in aspecs and aspecs[m].name else m)
            for m in analysis_channels
        ),
        "Content": (f"cleaned = {cleaned_scope.label()}, "
                    f"analysis = {analysis_scope.label()}, "
                    f"HTML = {html_scope.label()}"),
        "Layout": (sr.layout.describe() if sr.is_wide else "one file per unit"),
        "Deviation reference": profile.analysis.deviation_mode
        + (" (%)" if profile.analysis.deviation_percent else ""),
        "Dictionary": (str(sr.dictionary.source) if sr.dictionary and sr.dictionary.source
                       else "(none)"),
    }

    if opts.combined_workbook:
        progress(85, "Writing analysis workbook")
        target = base_dir / f"PCS_analysis_{stamp}.xlsx"
        try:
            write_analysis_workbook(target, result, profile.rules, opts, info, pngs,
                                    custom_charts=custom,
                                    analysis=profile.analysis)
            out.outputs.append(target)
            out.messages.append(f"Wrote {target.name}")
        except Exception as exc:
            out.messages.append(f"FAILED writing analysis workbook: {type(exc).__name__}: {exc}")

    if opts.write_html:
        progress(93, "Writing HTML report")
        target = base_dir / f"PCS_report_{stamp}.html"
        # Channels the analysis workbook does not hold still travel with the
        # report, so the reader can switch to one without a second run.  They
        # carry fewer points and no deviation frame to keep the file sane.
        extra: list[Any] = []
        rest = [c for c in html_channels if c not in set(analysis_channels)]
        for i, c in enumerate(rest, start=1):
            if cancelled():
                out.cancelled = True
                return out
            progress(93, f"HTML: extra channel {i}/{len(rest)}")
            try:
                mr = an.build_matrix(
                    sr.analysis_tables, c, aspecs.get(c),
                    deviation_mode=profile.analysis.deviation_mode,
                    deviation_percent=False,
                    round_to=profile.analysis.round_to,
                )
            except Exception as exc:
                out.messages.append(f"Extra channel {c} skipped: {exc}")
                continue
            if mr is not None and not mr.values.empty:
                mr.deviation = None
                extra.append(mr)
        if extra:
            out.messages.append(
                f"HTML report carries {len(extra)} channel(s) beyond the analysis set")
        try:
            ja = opts.html_language == "ja"
            title = (f"{profile.name} — 号機比較" if ja
                     else f"{profile.name} — unit comparison")
            if ja:
                subtitle = (f"{len(sr.tables)} ファイル・号機: {entities}"
                            + (f"・日付: {days}" if days else ""))
            else:
                subtitle = (f"{len(sr.tables)} file(s) · units: {entities}"
                            + (f" · dates: {days}" if days else ""))
            payload = build_payload(
                result, profile.rules,
                title=title,
                subtitle=subtitle,
                max_points=opts.max_html_points,
                extra_matrices=extra,
                extra_points=opts.html_extra_points,
                color_controls=opts.html_color_controls,
                worst_n=opts.html_worst_n,
                language=opts.html_language,
                sections=opts.html_sections,
                diagnostics=result.diagnostics,
                analysis=profile.analysis,
                roles=(result.diagnostics.roles if result.diagnostics is not None
                       else html_roles),
            )
            write_html_report(target, payload)
            out.outputs.append(target)
            out.messages.append(f"Wrote {target.name}")
        except Exception as exc:
            out.messages.append(f"FAILED writing HTML report: {type(exc).__name__}: {exc}")

    progress(100, "Done")
    return out
