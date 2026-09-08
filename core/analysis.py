"""Cross-entity analysis: compare the same metric across PCS units at the same
timestamp, quantify the deviation from the fleet, and summarise per day.

Everything here is plain pandas -> reusable outside this app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

from .loader import LoadedTable
from .models import AnalysisOptions, ColumnSpec


@dataclass
class MatrixResult:
    """One metric, aligned across entities."""

    metric: str            # raw column key
    title: str             # readable name
    unit: str
    values: pd.DataFrame   # index = timestamp, columns = entity
    deviation: Optional[pd.DataFrame] = None
    deviation_percent: bool = False
    reference: Optional[pd.Series] = None
    reference_mode: str = "mean"

    @property
    def entities(self) -> list[str]:
        return [str(c) for c in self.values.columns]


@dataclass
class AnalysisResult:
    matrices: list[MatrixResult] = field(default_factory=list)
    daily: Optional[pd.DataFrame] = None
    zero_report: Optional[pd.DataFrame] = None
    quality: Optional[pd.DataFrame] = None
    alarms: Optional[pd.DataFrame] = None
    diagnostics: Optional[Any] = None      # core.diagnostics.DiagnosticsResult


# --------------------------------------------------------------------------- #
def infer_interval(index: pd.Index) -> Optional[pd.Timedelta]:
    if len(index) < 3 or not isinstance(index, pd.DatetimeIndex):
        return None
    deltas = pd.Series(index).diff().dropna()
    if deltas.empty:
        return None
    try:
        return pd.Timedelta(deltas.mode().iloc[0])
    except Exception:
        return None


def common_numeric_columns(tables: Iterable[LoadedTable], min_share: float = 0.6) -> list[str]:
    """Numeric columns present in most tables, in first-seen order."""
    tables = list(tables)
    if not tables:
        return []
    order: list[str] = []
    counts: dict[str, int] = {}
    for t in tables:
        for c in t.numeric_columns:
            counts[c] = counts.get(c, 0) + 1
            if c not in order:
                order.append(c)
    need = max(1, int(len(tables) * min_share))
    return [c for c in order if counts[c] >= need]


def suggest_metrics(tables: Iterable[LoadedTable], limit: int = 8) -> list[str]:
    """Columns most worth cross-comparing: shared, varying, not constant."""
    tables = list(tables)
    shared = common_numeric_columns(tables)
    scored: list[tuple[float, str]] = []
    for c in shared:
        spread = 0.0
        for t in tables:
            if c in t.df.columns:
                s = pd.to_numeric(t.df[c], errors="coerce")
                if s.notna().sum() > 2 and s.abs().max():
                    spread = max(spread, float(s.std(ddof=0) / (abs(s.mean()) + 1e-9)))
        if spread > 0:
            scored.append((spread, c))
    scored.sort(reverse=True)
    return [c for _, c in scored[:limit]] or shared[:limit]


def _series_for(table: LoadedTable, metric: str) -> Optional[pd.Series]:
    if metric not in table.df.columns or table.time_column is None:
        return None
    s = pd.to_numeric(table.df[metric], errors="coerce")
    s.index = pd.DatetimeIndex(table.df[table.time_column])
    s = s[~s.index.duplicated(keep="first")]
    return s.sort_index()


def build_matrix(
    tables: Iterable[LoadedTable],
    metric: str,
    spec: Optional[ColumnSpec] = None,
    tolerance: str = "30s",
    deviation_mode: str = "mean",
    deviation_percent: bool = True,
    round_to: int = 3,
    floor_pct: float = 2.0,
    floor_abs: float = 0.0,
) -> Optional[MatrixResult]:
    """Align one metric from every table onto a shared time axis."""
    series: dict[str, pd.Series] = {}
    for t in tables:
        s = _series_for(t, metric)
        if s is None or s.dropna().empty:
            continue
        key = t.entity
        if key in series:                     # several days for the same PCS
            series[key] = pd.concat([series[key], s]).sort_index()
            series[key] = series[key][~series[key].index.duplicated(keep="first")]
        else:
            series[key] = s
    if not series:
        return None

    # snap timestamps to the finest common grid so that "same time" really lines up
    intervals = [i for i in (infer_interval(s.index) for s in series.values()) if i]
    grid = min(intervals) if intervals else pd.Timedelta(tolerance)
    if grid <= pd.Timedelta(0):
        grid = pd.Timedelta(tolerance)
    snapped = {}
    for k, s in series.items():
        s = s.copy()
        s.index = s.index.round(grid)
        s = s[~s.index.duplicated(keep="first")]
        snapped[k] = s

    values = pd.DataFrame(snapped).sort_index()
    values = values.reindex(sorted(values.columns), axis=1)
    if round_to is not None:
        values = values.round(round_to)

    ref = None
    dev = None
    if len(values.columns) > 1:
        mode = deviation_mode
        if mode == "median":
            ref = values.median(axis=1, skipna=True)
        elif mode == "max":
            ref = values.max(axis=1, skipna=True)
        elif mode == "best":
            # the entity with the highest total becomes the reference
            best_col = values.sum(axis=0, skipna=True).idxmax()
            ref = values[best_col]
            mode = f"best ({best_col})"
        else:
            ref = values.mean(axis=1, skipna=True)
        if deviation_percent:
            # ignore rows where the reference is too small to divide by
            scale = float(np.nanmax(np.abs(values.to_numpy(dtype="float64"))) or 0.0)
            floor = max(abs(floor_abs), scale * max(0.0, floor_pct) / 100.0)
            denom = ref.where(ref.abs() > floor)
            dev = (values.sub(ref, axis=0).div(denom, axis=0) * 100.0)
        else:
            dev = values.sub(ref, axis=0)
        dev = dev.round(round_to if round_to is not None else 3)
    else:
        mode = deviation_mode

    title = (spec.name if spec and spec.name else metric)
    unit = spec.unit if spec else ""
    return MatrixResult(
        metric=metric,
        title=title,
        unit=unit,
        values=values,
        deviation=dev,
        deviation_percent=deviation_percent,
        reference=ref,
        reference_mode=mode,
    )


def daily_summary(
    tables: Iterable[LoadedTable],
    metrics: list[str],
    specs: dict[str, ColumnSpec],
    round_to: int = 3,
) -> pd.DataFrame:
    """One row per entity/day/metric: count, mean, max, min, energy, zeros, gaps."""
    rows = []
    for t in tables:
        if t.time_column is None:
            continue
        ts = pd.DatetimeIndex(t.df[t.time_column])
        interval = infer_interval(ts)
        hours = (interval.total_seconds() / 3600.0) if interval is not None else np.nan
        for day, idx in pd.Series(range(len(ts)), index=ts).groupby(ts.date):
            sl = t.df.iloc[list(idx.values)]
            for metric in metrics:
                if metric not in sl.columns:
                    continue
                s = pd.to_numeric(sl[metric], errors="coerce")
                spec = specs.get(metric)
                valid = int(s.notna().sum())
                expected = (int(round(pd.Timedelta(days=1) / interval))
                            if interval is not None and interval > pd.Timedelta(0) else len(s))
                zeros = int((s.abs() <= 0).sum())
                # 計測値種別: a per-interval difference is already an amount, an
                # instantaneous reading has to be integrated over the interval.
                is_delta = bool(spec and ("差分" in spec.kind or "diff" in spec.kind.lower()))
                if not valid:
                    total = np.nan
                elif is_delta:
                    total = round(float(s.sum()), round_to)
                elif np.isfinite(hours):
                    total = round(float(s.sum()) * hours, round_to)
                else:
                    total = np.nan
                rows.append({
                    "Entity": t.entity,
                    "Date": str(day),
                    "Metric": (spec.name if spec and spec.name else metric),
                    "Unit": (spec.unit if spec else ""),
                    "Samples": valid,
                    "Expected": expected,
                    "Coverage %": round(100.0 * valid / expected, 2) if expected else np.nan,
                    "Mean": round(float(s.mean()), round_to) if valid else np.nan,
                    "Max": round(float(s.max()), round_to) if valid else np.nan,
                    "Min": round(float(s.min()), round_to) if valid else np.nan,
                    "Total": total,
                    "Total note": ("sum of differences" if is_delta else "value x interval"),
                    "Zero samples": zeros,
                    "Zero %": round(100.0 * zeros / valid, 2) if valid else np.nan,
                    "Missing": int(s.isna().sum()),
                    "Negative": int((s < 0).sum()),
                })
    return pd.DataFrame(rows)


def zero_report(tables: Iterable[LoadedTable], specs: dict[str, ColumnSpec]) -> pd.DataFrame:
    """Where are the zeros?  Per entity/column: count, share, longest run."""
    rows = []
    for t in tables:
        for c in t.numeric_columns:
            s = pd.to_numeric(t.df[c], errors="coerce")
            valid = int(s.notna().sum())
            if not valid:
                continue
            iszero = (s.abs() <= 0).fillna(False)
            n_zero = int(iszero.sum())
            if n_zero == 0:
                continue
            grp = (iszero != iszero.shift()).cumsum()
            runs = iszero.groupby(grp).sum()
            spec = specs.get(c)
            rows.append({
                "Entity": t.entity,
                "Date": t.day or "",
                "Column": (spec.name if spec and spec.name else c),
                "Unit": (spec.unit if spec else ""),
                "Zero samples": n_zero,
                "Zero %": round(100.0 * n_zero / valid, 2),
                "Longest zero run": int(runs.max()) if len(runs) else 0,
                "Raw column": c,
            })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["Zero %", "Entity"], ascending=[False, True]).reset_index(drop=True)
    return df


def quality_report(tables: Iterable[LoadedTable]) -> pd.DataFrame:
    rows = []
    for t in tables:
        ts = (pd.DatetimeIndex(t.df[t.time_column]) if t.time_column else None)
        interval = infer_interval(ts) if ts is not None else None
        gaps = 0
        if ts is not None and interval is not None and len(ts) > 1:
            d = pd.Series(ts).diff().dropna()
            gaps = int((d > interval * 1.5).sum())
        rows.append({
            "File": t.path.name,
            "Entity": t.entity,
            "Date": t.day or "",
            "Rows": len(t.df),
            "Columns": t.df.shape[1],
            "Numeric columns": len(t.numeric_columns),
            "Text columns": len(t.text_columns),
            "Time column": t.time_column or "(none)",
            "Interval": str(interval) if interval is not None else "",
            "Time gaps": gaps,
            "Encoding": t.encoding,
            "Delimiter": {"\t": "TAB"}.get(t.delimiter, t.delimiter),
            "Header row": t.header_row + 1,
            "Notes": "; ".join(t.notes),
        })
    return pd.DataFrame(rows)


def run_analysis(
    tables: list[LoadedTable],
    specs: dict[str, ColumnSpec],
    opts: AnalysisOptions,
    matrix_tables: Optional[list[LoadedTable]] = None,
) -> AnalysisResult:
    """``matrix_tables`` may be wider than ``tables``.

    A plant-wide channel (the horizontal pyranometer) lives in a table of its
    own, which belongs in the channel comparison but not in the per-unit
    summaries - so the daily totals, the zero report and the quality table keep
    using ``tables``.
    """
    metrics = [m for m in opts.metrics] or suggest_metrics(tables)
    res = AnalysisResult()
    if opts.make_matrix:
        for m in metrics:
            mr = build_matrix(
                matrix_tables or tables, m, specs.get(m),
                deviation_mode=opts.deviation_mode,
                deviation_percent=opts.deviation_percent,
                round_to=opts.round_to,
                floor_pct=opts.deviation_floor_pct,
                floor_abs=opts.deviation_floor_abs,
            )
            if mr is not None and not mr.values.empty:
                if not opts.make_deviation:
                    mr.deviation = None
                res.matrices.append(mr)
    if opts.make_daily_summary:
        res.daily = daily_summary(tables, metrics, specs, opts.round_to)
    if opts.zero_counts:
        res.zero_report = zero_report(tables, specs)
    res.quality = quality_report(tables)
    return res
