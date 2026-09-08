"""Wide files: one file, every unit side by side.

The Aomori exports put 20 PCS in a single daily file::

    Time[+09:00], PCS01_Data01, ..., PCS01_Data15, PCS02_Data01, ..., ANN_101, ...

This module recognises that shape and turns it into one virtual table per
unit, so the rest of the pipeline (matrix, deviation, rules, charts) works
unchanged.  Column names inside a unit table are the *metric key*
(``Data01``), identical for every unit, which is exactly what the comparison
code needs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

from .loader import LoadedTable
from .models import PLANT_ENTITY, ColumnSpec, IdentityOptions


@dataclass
class WideLayout:
    """What the column names say about the file's structure."""

    entities: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    # (entity, metric) -> raw column name
    cells: dict[tuple[str, str], str] = field(default_factory=dict)
    plant_columns: list[str] = field(default_factory=list)
    alarm_columns: list[str] = field(default_factory=list)
    alarm_entity: dict[str, str] = field(default_factory=dict)   # column -> entity

    @property
    def is_wide(self) -> bool:
        return len(self.entities) > 1 and len(self.metrics) > 0

    def describe(self) -> str:
        return (f"{len(self.entities)} units x {len(self.metrics)} metrics, "
                f"{len(self.plant_columns)} plant column(s), "
                f"{len(self.alarm_columns)} status/alarm bit(s)")


def _entity_sort_key(name: str) -> tuple:
    m = re.search(r"(\d+)", name)
    return (0, int(m.group(1)), name) if m else (1, 0, name)


GUESS_PATTERNS = (
    r"^(PCS\d+)[_ ]+(.+)$",
    r"^([A-Za-z]{2,6}\d{1,3})[_ ]+(.+)$",
    r"^(\d{1,3})[_ ]+(.+)$",
)


def guess_entity_pattern(columns: Iterable[str], min_entities: int = 2,
                         min_metrics: int = 2) -> str:
    """Find a "<unit>_<channel>" column pattern when none was configured.

    Without this, a profile made with 'New' shows all 689 raw columns and the
    per-unit comparison silently does nothing.
    """
    cols = [str(c) for c in columns]
    best, best_score = "", 0
    for pattern in GUESS_PATTERNS:
        try:
            rx = re.compile(pattern)
        except re.error:
            continue
        entities: dict[str, set[str]] = {}
        for c in cols:
            m = rx.match(c)
            if m and len(m.groups()) > 1:
                entities.setdefault(m.group(1), set()).add(m.group(2))
        if len(entities) < min_entities:
            continue
        metrics = set().union(*entities.values()) if entities else set()
        if len(metrics) < min_metrics:
            continue
        # prefer the pattern that explains the most columns
        score = sum(len(v) for v in entities.values())
        if score > best_score:
            best, best_score = pattern, score
    return best


def guess_alarm_pattern(df, columns: Iterable[str]) -> str:
    """Spot binary status-bit columns (ANN_101, DI_2003 ...) so 362 of them do
    not drown the channel list."""
    candidates = [c for c in map(str, columns)
                  if re.fullmatch(r"[A-Za-z]{2,6}_\d{3,5}", c)]
    if len(candidates) < 8:
        return ""
    binary = 0
    for c in candidates[:40]:
        if c not in df.columns:
            continue
        vals = pd.to_numeric(df[c], errors="coerce").dropna().unique()
        if len(vals) and set(np.unique(vals)).issubset({0.0, 1.0}):
            binary += 1
    if binary < 5:
        return ""
    prefix = candidates[0].split("_")[0]
    same = sum(1 for c in candidates if c.startswith(prefix + "_"))
    if same >= len(candidates) * 0.8:
        return rf"^{re.escape(prefix)}_\d+$"
    return r"^[A-Za-z]{2,6}_\d{3,5}$"


def analyse_layout(
    columns: Iterable[str],
    identity: IdentityOptions,
    time_column: Optional[str] = None,
) -> WideLayout:
    """Split column names into (entity, metric), plant and alarm groups."""
    layout = WideLayout()
    ent_re = None
    if identity.entity_from_column:
        try:
            ent_re = re.compile(identity.entity_from_column)
        except re.error:
            ent_re = None
    alarm_re = None
    if identity.alarm_pattern:
        try:
            alarm_re = re.compile(identity.alarm_pattern)
        except re.error:
            alarm_re = None

    for col in columns:
        c = str(col)
        if time_column is not None and c == time_column:
            continue
        if alarm_re is not None:
            am = alarm_re.match(c)
            if am:
                layout.alarm_columns.append(c)
                continue
        if ent_re is not None:
            m = ent_re.match(c)
            if m and m.groups():
                entity = m.group(1)
                metric = m.group(2) if len(m.groups()) > 1 else c[m.end(1):].lstrip("_ ")
                metric = metric or c
                if entity not in layout.entities:
                    layout.entities.append(entity)
                if metric not in layout.metrics:
                    layout.metrics.append(metric)
                layout.cells[(entity, metric)] = c
                continue
        layout.plant_columns.append(c)

    layout.entities.sort(key=_entity_sort_key)

    # Map alarm bits onto units when the numbering allows it: ANN_1701 -> the
    # 17th unit, bit 01.  Anything outside the unit range stays plant level.
    if layout.alarm_columns and layout.entities:
        index: dict[int, str] = {}
        for e in layout.entities:
            m = re.search(r"(\d+)", e)
            if m:
                index[int(m.group(1))] = e
        for c in layout.alarm_columns:
            digits = re.findall(r"(\d+)", c)
            if not digits:
                continue
            num = digits[-1]
            if len(num) >= 3:
                unit = int(num[:-2])
                if unit in index:
                    layout.alarm_entity[c] = index[unit]
    return layout


def strip_entity(name: str, entity: str) -> str:
    """``"PCS01 ①直流電力"`` -> ``"①直流電力"``."""
    if not name or not entity:
        return name
    pattern = rf"^\s*{re.escape(entity)}\s*[\s_　:：\-]*"
    out = re.sub(pattern, "", str(name), flags=re.IGNORECASE).strip()
    return out or name


def metric_specs(
    layout: WideLayout,
    specs: dict[str, ColumnSpec],
    strip: bool = True,
) -> dict[str, ColumnSpec]:
    """One ColumnSpec per *metric*, named without the unit prefix."""
    out: dict[str, ColumnSpec] = {}
    for metric in layout.metrics:
        chosen: Optional[ColumnSpec] = None
        entity_of = ""
        for entity in layout.entities:
            raw = layout.cells.get((entity, metric))
            if raw is None:
                continue
            spec = specs.get(raw)
            if spec is None:
                continue
            if chosen is None or (not chosen.matched and spec.matched):
                chosen, entity_of = spec, entity
            if chosen is not None and chosen.matched:
                break
        if chosen is None:
            out[metric] = ColumnSpec(raw=metric, name=metric, metric=metric)
            continue
        name = strip_entity(chosen.name, entity_of) if strip else chosen.name
        out[metric] = ColumnSpec(
            raw=metric,
            name=name or metric,
            unit=chosen.unit,
            note=chosen.note,
            kind=chosen.kind,
            shared=chosen.shared,
            metric=metric,
            is_numeric=chosen.is_numeric,
            include=chosen.include,
            decimals=chosen.decimals,
            matched=chosen.matched,
        )
    return out


def plant_table(table: LoadedTable, layout: WideLayout) -> Optional[LoadedTable]:
    """The plant-wide columns as one table under :data:`PLANT_ENTITY`.

    ``Opt_Data22`` (日射強度2, the horizontal pyranometer) and ``Opt_Data21``
    (パネル温度) are measured once for the site, so they are not part of any
    unit's table and were previously reachable only on the PLANT sheet of the
    cleaned workbook.  Giving them a table of their own puts them in the
    channel list next to ③日射量(傾斜) without copying one sensor into twenty
    columns - and without adding a twenty-first "unit" to the comparison.
    """
    if not layout.is_wide or not layout.plant_columns:
        return None
    time_col = table.time_column
    keep = [c for c in layout.plant_columns if c in table.df.columns]
    if not keep:
        return None
    cols = ([time_col] if time_col else []) + keep
    sub = table.df.loc[:, [c for c in cols if c in table.df.columns]].copy()
    return LoadedTable(
        path=table.path,
        df=sub,
        time_column=time_col,
        entity=PLANT_ENTITY,
        day=table.day,
        encoding=table.encoding,
        delimiter=table.delimiter,
        header_row=table.header_row,
        unit_row={},
        numeric_columns=[c for c in keep if c in table.numeric_columns],
        text_columns=[c for c in keep if c in table.text_columns],
        notes=[f"plant columns of {table.path.name}"],
    )


def split_tables(table: LoadedTable, layout: WideLayout) -> list[LoadedTable]:
    """One :class:`LoadedTable` per entity, columns renamed to the metric key."""
    out: list[LoadedTable] = []
    if not layout.is_wide:
        return out
    time_col = table.time_column
    for entity in layout.entities:
        pairs = [(m, layout.cells[(entity, m)]) for m in layout.metrics
                 if (entity, m) in layout.cells]
        if not pairs:
            continue
        cols = ([time_col] if time_col else []) + [raw for _, raw in pairs]
        sub = table.df.loc[:, [c for c in cols if c in table.df.columns]].copy()
        rename = {raw: metric for metric, raw in pairs}
        sub = sub.rename(columns=rename)
        numeric = [m for m, raw in pairs if raw in table.numeric_columns]
        text = [m for m, raw in pairs if raw in table.text_columns]
        out.append(
            LoadedTable(
                path=table.path,
                df=sub,
                time_column=time_col,
                entity=entity,
                day=table.day,
                encoding=table.encoding,
                delimiter=table.delimiter,
                header_row=table.header_row,
                unit_row={},
                numeric_columns=numeric,
                text_columns=text,
                notes=[f"split from {table.path.name}"],
            )
        )
    return out


def alarm_summary(
    table: LoadedTable,
    layout: WideLayout,
    specs: dict[str, ColumnSpec],
    active_only: bool = True,
) -> pd.DataFrame:
    """One row per status/alarm bit: how long it was on, when it changed."""
    rows: list[dict[str, Any]] = []
    time = (pd.DatetimeIndex(table.df[table.time_column])
            if table.time_column in table.df.columns else None)
    for col in layout.alarm_columns:
        if col not in table.df.columns:
            continue
        s = pd.to_numeric(table.df[col], errors="coerce")
        on = (s > 0).fillna(False)
        count = int(on.sum())
        if active_only and count == 0:
            continue
        idx = np.flatnonzero(on.to_numpy())
        first = last = ""
        if len(idx) and time is not None:
            first = str(time[idx[0]])
            last = str(time[idx[-1]])
        # number of separate ON periods
        grp = (on != on.shift()).cumsum()
        periods = int(on.groupby(grp).max().sum())
        spec = specs.get(col)
        rows.append({
            "Entity": layout.alarm_entity.get(col, "PLANT"),
            "Bit": col,
            "Name": (spec.name if spec and spec.matched else ""),
            "Date": table.day or "",
            "Minutes on": count,
            "Share %": round(100.0 * count / max(1, len(s)), 2),
            "Events": periods,
            "First on": first,
            "Last on": last,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["Entity", "Minutes on"],
                            ascending=[True, False]).reset_index(drop=True)
    return df


def suggest_wide_metrics(
    layout: WideLayout,
    mspecs: dict[str, ColumnSpec],
    tables: list[LoadedTable],
    skip_shared: bool = True,
    limit: int = 8,
) -> list[str]:
    """Metrics worth comparing between units: numeric, varying, not a shared sensor."""
    scored: list[tuple[float, str]] = []
    for metric in layout.metrics:
        spec = mspecs.get(metric)
        if spec is not None and not spec.is_numeric:
            continue
        if skip_shared and spec is not None and spec.shared:
            continue
        spread = 0.0
        for t in tables:
            if metric not in t.df.columns:
                continue
            s = pd.to_numeric(t.df[metric], errors="coerce")
            if s.notna().sum() > 2:
                mu = float(abs(s.mean()))
                spread = max(spread, float(s.std(ddof=0)) / (mu + 1e-9))
        if spread > 0:
            scored.append((spread, metric))
    scored.sort(reverse=True)
    return [m for _, m in scored[:limit]]
