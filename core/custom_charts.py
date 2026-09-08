"""User-defined charts: pick X, pick Y, decide how to group.

A :class:`ChartSpec` says *what* to plot; this module works out *how*:

* one chart with everything on it (``single``)
* one chart per selected channel, all units on it (``per_column``)
* one chart per unit, all selected channels on it (``per_entity``)
* one chart per name pattern (``pattern``) - e.g. ``Data10`` puts every
  column whose raw name or readable name contains "Data10" on one chart

Series with different units are never squeezed onto one axis: the chart is
split per unit automatically, unless ``normalize`` is on (then every series
is drawn as a percentage of its own maximum, which is unit-free).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from .chart_png import (
    DASHES,
    GRID,
    INK2,
    MUTED,
    PALETTE,
    _finish,
    _safe,
    _time_axis,
    apply_style,
)
from .loader import LoadedTable
from .models import ChartSpec, ColumnSpec


@dataclass
class Series:
    label: str
    x: pd.Series
    y: pd.Series
    unit: str = ""
    entity: str = ""
    column: str = ""


def _matches(pattern: str, spec: Optional[ColumnSpec], column: str) -> bool:
    needle = pattern.strip().lower()
    if not needle:
        return False
    hay = [column.lower()]
    if spec is not None:
        hay += [spec.name.lower(), spec.raw.lower()]
    return any(needle in h for h in hay)


def collect_series(
    tables: Iterable[LoadedTable],
    chart: ChartSpec,
    specs: dict[str, ColumnSpec],
    max_points: int = 4000,
) -> list[Series]:
    """Turn a ChartSpec into concrete (x, y) series."""
    out: list[Series] = []
    wanted_entities = {e for e in chart.entities if e}
    for table in tables:
        if wanted_entities and table.entity not in wanted_entities:
            continue
        x_col = chart.x_column or table.time_column
        if x_col not in table.df.columns:
            continue
        x_raw = table.df[x_col]
        for col in chart.y_columns:
            if col not in table.df.columns or col == x_col:
                continue
            y_raw = pd.to_numeric(table.df[col], errors="coerce")
            if y_raw.notna().sum() < 2:
                continue
            x = x_raw
            if not pd.api.types.is_datetime64_any_dtype(x):
                x = pd.to_numeric(x, errors="coerce")
            keep = y_raw.notna() & pd.Series(x).notna().to_numpy()
            x, y = pd.Series(x)[keep], y_raw[keep]
            if len(x) > max_points:
                step = int(np.ceil(len(x) / max_points))
                x, y = x.iloc[::step], y.iloc[::step]
            spec = specs.get(col)
            name = spec.name if spec and spec.name else col
            label = f"{table.entity} · {name}" if table.entity else name
            out.append(Series(label=label, x=x, y=y,
                              unit=(spec.unit if spec else ""),
                              entity=table.entity, column=col))
    return out


def _group(chart: ChartSpec, series: list[Series],
           specs: dict[str, ColumnSpec]) -> list[tuple[str, list[Series]]]:
    mode = chart.group_mode
    if mode == "per_column":
        groups: dict[str, list[Series]] = {}
        for s in series:
            groups.setdefault(s.column, []).append(s)
        return [((specs[c].name if c in specs and specs[c].name else c), items)
                for c, items in groups.items()]
    if mode == "per_entity":
        groups = {}
        for s in series:
            groups.setdefault(s.entity or "all", []).append(s)
        return list(groups.items())
    if mode == "pattern":
        out = []
        for pat in chart.patterns:
            items = [s for s in series if _matches(pat, specs.get(s.column), s.column)]
            if items:
                out.append((pat, items))
        return out
    return [(chart.title or "chart", series)]


def _split_by_unit(items: list[Series], normalize: bool) -> list[tuple[str, list[Series]]]:
    if normalize:
        return [("", items)]
    units: dict[str, list[Series]] = {}
    for s in items:
        units.setdefault(s.unit or "", []).append(s)
    if len(units) <= 1:
        return [(next(iter(units), ""), items)]
    return list(units.items())


def render_chart(
    title: str,
    items: list[Series],
    out_path: Path,
    chart_type: str = "line",
    normalize: bool = False,
    unit: str = "",
) -> Optional[Path]:
    import matplotlib.pyplot as plt

    if not items:
        return None
    apply_style()
    fig, ax = plt.subplots(figsize=(11.5, 4.6), dpi=160)
    for i, s in enumerate(items):
        y = s.y.astype("float64")
        if normalize:
            top = float(np.nanmax(np.abs(y.to_numpy()))) or 1.0
            y = y / top * 100.0
        color = PALETTE[i % len(PALETTE)]
        dash = DASHES[(i // len(PALETTE)) % len(DASHES)]
        if chart_type == "scatter":
            ax.plot(s.x, y, marker="o", markersize=3.2, linestyle="none",
                    color=color, alpha=0.75, label=s.label)
        elif chart_type == "bar":
            ax.bar(s.x, y, color=color, alpha=0.8, label=s.label)
        else:
            ax.plot(s.x, y, color=color, linestyle=dash,
                    linewidth=2.0 if i < len(PALETTE) else 1.6, label=s.label)

    ylabel = "% of each series maximum" if normalize else unit
    if len(items) > 1:
        ax.legend(loc="upper left", bbox_to_anchor=(0, -0.16),
                  ncol=min(len(items), 4), fontsize=8.5)
    _finish(ax, title, ylabel)
    if items and pd.api.types.is_datetime64_any_dtype(items[0].x):
        _time_axis(ax, pd.DatetimeIndex(items[0].x))
    else:
        ax.grid(axis="x", visible=True)
    fig.tight_layout()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    return out_path


def build_charts(
    tables: list[LoadedTable],
    charts: list[ChartSpec],
    specs: dict[str, ColumnSpec],
    out_dir: Path,
    stem: str = "chart",
    max_points: int = 4000,
) -> list[tuple[str, Path]]:
    """Render every enabled ChartSpec.  Returns [(title, png path), ...]."""
    made: list[tuple[str, Path]] = []
    for ci, chart in enumerate(charts, start=1):
        if not chart.enabled or not chart.y_columns:
            continue
        series = collect_series(tables, chart, specs, max_points)
        if not series:
            continue
        for gi, (group_title, items) in enumerate(_group(chart, series, specs), start=1):
            for ui, (unit, sub) in enumerate(_split_by_unit(items, chart.normalize), start=1):
                base = chart.title or group_title or "chart"
                title = base if base == group_title else f"{base} — {group_title}"
                if unit:
                    title = f"{title} [{unit}]"
                suffix = f"{ci:02d}_{gi:02d}" + (f"_{ui}" if ui > 1 else "")
                path = out_dir / f"{stem}_custom{suffix}_{_safe(group_title)}.png"
                p = render_chart(title, sub, path, chart.chart_type,
                                 chart.normalize, unit)
                if p is not None:
                    made.append((title, p))
    return made


def default_charts(metrics: list[str], specs: dict[str, ColumnSpec]) -> list[ChartSpec]:
    """A sensible starting set so the Analysis tab is never empty."""
    if not metrics:
        return []
    first = metrics[0]
    name = specs[first].name if first in specs and specs[first].name else first
    return [
        ChartSpec(title=f"{name} — every unit", y_columns=[first],
                  group_mode="per_column", chart_type="line"),
        ChartSpec(title="Selected channels per unit", y_columns=list(metrics[:4]),
                  group_mode="per_entity", chart_type="line", normalize=True,
                  enabled=False),
    ]
