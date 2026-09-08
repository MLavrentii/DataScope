"""matplotlib figures (PNG) - for embedding in Excel and for reports.

Restrained style: no chart junk, 2 pt lines, hairline grid, direct labels when
there are few series, legend when there are many.  Japanese labels work if any
CJK font is installed (Yu Gothic / Meiryo / MS Gothic / Noto).
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")

import matplotlib.dates as mdates      # noqa: E402
import matplotlib.pyplot as plt        # noqa: E402
import numpy as np                     # noqa: E402
import pandas as pd                     # noqa: E402

from .analysis import MatrixResult      # noqa: E402

PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100",
           "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
DASHES = ["-", (0, (5, 2)), (0, (1, 1.6)), (0, (7, 2, 1, 2))]
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
SURFACE = "#fcfcfb"

CJK_FONTS = ["Yu Gothic", "Meiryo", "MS Gothic", "Noto Sans CJK JP",
             "Noto Sans JP", "IPAexGothic", "TakaoPGothic", "DejaVu Sans"]

warnings.filterwarnings("ignore", message="Glyph .* missing from font")


def apply_style() -> None:
    plt.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "font.family": "sans-serif",
        "font.sans-serif": CJK_FONTS,
        "axes.edgecolor": GRID,
        "axes.labelcolor": INK2,
        "axes.titlesize": 12,
        "axes.titleweight": "600",
        "axes.titlecolor": INK,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": GRID,
        "grid.linewidth": 0.8,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "legend.frameon": False,
        "legend.fontsize": 9,
        "lines.linewidth": 2.0,
        "lines.solid_capstyle": "round",
        "figure.autolayout": False,
    })


def _finish(ax, title: str, ylabel: str) -> None:
    ax.set_title(title, loc="left", pad=12)
    ax.set_ylabel(ylabel, fontsize=9)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(GRID)
    ax.spines["bottom"].set_color(GRID)
    ax.grid(axis="x", visible=False)


def _time_axis(ax, index: pd.Index) -> None:
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 2:
        return
    span = index[-1] - index[0]
    if span <= pd.Timedelta(hours=30):
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=8))
    else:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=3, maxticks=7))


def plot_matrix(
    matrix: pd.DataFrame,
    title: str,
    unit: str = "",
    out_path: Optional[Path] = None,
    diverging: bool = False,
    max_points: int = 3000,
    width: float = 11.0,
    height: float = 4.2,
    dpi: int = 160,
) -> Optional[Path]:
    """One line per entity.  ``diverging`` draws a zero reference line."""
    if matrix is None or matrix.empty or out_path is None:
        return None
    apply_style()
    df = matrix
    if len(df) > max_points:
        df = df.iloc[:: int(np.ceil(len(df) / max_points))]

    fig, ax = plt.subplots(figsize=(width, height), dpi=dpi)
    cols = list(df.columns)
    for i, c in enumerate(cols):
        ax.plot(df.index, df[c].to_numpy(dtype="float64"),
                color=PALETTE[i % len(PALETTE)],
                linestyle=DASHES[(i // len(PALETTE)) % len(DASHES)],
                linewidth=2.0 if i < len(PALETTE) else 1.6,
                label=str(c))
    if diverging:
        ax.axhline(0, color="#c3c2b7", linewidth=1.4, zorder=1)

    # Direct labels only when they will not collide: few series and their last
    # values separated by more than 5 % of the y range.  Otherwise a legend.
    ends = []
    for i, c in enumerate(cols):
        s = df[c].dropna()
        if not s.empty:
            ends.append((float(s.iloc[-1]), s.index[-1], i, str(c)))
    y0, y1 = ax.get_ylim()
    span = max(abs(y1 - y0), 1e-9)
    labelled = False
    if 1 < len(cols) <= 4 and len(ends) == len(cols):
        vals = sorted(v for v, *_ in ends)
        if all((b - a) / span > 0.05 for a, b in zip(vals, vals[1:])):
            for v, x, i, name in ends:
                ax.annotate(f" {name}", (x, v), color=PALETTE[i % len(PALETTE)],
                            fontsize=9, va="center", ha="left", annotation_clip=False)
            ax.margins(x=0.08)
            labelled = True
    if len(cols) > 1 and not labelled:
        ax.legend(loc="upper left", bbox_to_anchor=(0, -0.16), ncol=min(len(cols), 6))
    _finish(ax, title, unit)
    _time_axis(ax, df.index)
    fig.tight_layout()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    return out_path


def plot_metric_pair(m: MatrixResult, out_dir: Path, stem: str) -> list[Path]:
    """Value chart + deviation chart for one metric."""
    made: list[Path] = []
    unit = m.unit
    p = plot_matrix(m.values, f"{m.title} — value by unit", unit,
                    out_dir / f"{stem}_{_safe(m.title)}_value.png")
    if p:
        made.append(p)
    if m.deviation is not None and not m.deviation.empty:
        du = "%" if m.deviation_percent else unit
        p = plot_matrix(m.deviation,
                        f"{m.title} — deviation from fleet {m.reference_mode}", du,
                        out_dir / f"{stem}_{_safe(m.title)}_dev.png", diverging=True)
        if p:
            made.append(p)
    return made


def plot_daily_bars(daily: pd.DataFrame, metric_name: str, out_path: Path,
                    value_col: str = "Mean") -> Optional[Path]:
    """Grouped bars: one bar per entity per day."""
    if daily is None or daily.empty:
        return None
    d = daily[daily["Metric"] == metric_name]
    if d.empty:
        return None
    apply_style()
    pivot = d.pivot_table(index="Date", columns="Entity", values=value_col, aggfunc="mean")
    fig, ax = plt.subplots(figsize=(max(6.0, 1.2 * len(pivot) + 3), 3.6), dpi=160)
    n = max(1, len(pivot.columns))
    width = 0.8 / n
    xs = np.arange(len(pivot))
    for i, c in enumerate(pivot.columns):
        ax.bar(xs + i * width - 0.4 + width / 2, pivot[c].to_numpy(dtype="float64"),
               width=width * 0.92, color=PALETTE[i % len(PALETTE)], label=str(c))
    ax.set_xticks(xs)
    ax.set_xticklabels([str(v) for v in pivot.index], rotation=0)
    ax.legend(loc="upper left", bbox_to_anchor=(0, -0.12), ncol=min(n, 6))
    _finish(ax, f"{metric_name} — daily {value_col.lower()}", "")
    fig.tight_layout()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    return out_path


def plot_unit_ranking(
    daily: pd.DataFrame,
    metric_name: str,
    out_path: Path,
    value_col: str = "Total",
    unit: str = "",
) -> Optional[Path]:
    """Ranked horizontal bars: which unit is behind, and by how much.

    With 20 units this answers the question a 20-line time chart cannot.
    """
    if daily is None or daily.empty:
        return None
    d = daily[daily["Metric"] == metric_name]
    if d.empty or value_col not in d.columns:
        return None
    totals = (d.groupby("Entity")[value_col].sum(min_count=1).dropna().sort_values())
    if totals.empty:
        return None
    apply_style()
    mean = float(totals.mean())
    fig, ax = plt.subplots(figsize=(9.5, max(3.0, 0.32 * len(totals) + 1.4)), dpi=160)
    dev = (totals - mean) / (abs(mean) + 1e-9) * 100.0
    colors = ["#d03b3b" if v < -3 else ("#2a78d6" if v > 3 else "#898781") for v in dev]
    ax.barh(totals.index.astype(str), totals.to_numpy(dtype="float64"),
            color=colors, height=0.68)
    ax.axvline(mean, color="#52514e", linewidth=1.4, linestyle=(0, (5, 2)))
    top = float(totals.max())
    ax.set_xlim(0, top * 1.30)
    ax.annotate(f"fleet mean {mean:,.0f} ", (mean, len(totals) - 0.35),
                color="#52514e", fontsize=9, va="center", ha="right")
    label_x = top * 1.03
    for i, (name, v) in enumerate(totals.items()):
        ax.annotate(f"{v:,.0f}", (label_x, i), va="center", ha="left",
                    fontsize=8.5, color=INK, annotation_clip=False)
        d = dev[name]
        ax.annotate(f"{d:+.1f}%", (top * 1.19, i), va="center", ha="left", fontsize=8.5,
                    color=("#b3261e" if d < -3 else ("#1c5cab" if d > 3 else MUTED)),
                    annotation_clip=False)
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", visible=True)
    _finish(ax, f"{metric_name} — {value_col.lower()} per unit"
                + (f" [{unit}]" if unit else ""), "")
    fig.tight_layout()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    return out_path


def _safe(name: str) -> str:
    keep = []
    for ch in str(name):
        keep.append(ch if (ch.isalnum() or ch in "-_ぁ-んァ-ヶ一-龠") else "_")
    return "".join(keep)[:40].strip("_") or "metric"
