"""Why is a unit under-performing?

The cross-unit matrix says *that* PCS01 is low.  These functions say *where*
the loss is, which decides who goes to site and with what.

The split is simple physics, and it needs no data beyond what the SCADA export
already carries:

* **DC per irradiance** - how much DC power the array makes for the light it
  receives.  Low here means the problem is outside the inverter: a string down,
  shading, soiling, a bad connection.
* **DC -> AC efficiency** - how much of that DC the inverter converts.  Low here
  means the inverter itself.

A unit can be low on one, the other, or both, and the action differs every
time.  Everything is plain pandas so the module is reusable outside this app.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

import numpy as np
import pandas as pd

from .loader import LoadedTable
from .models import (DIAGNOSTIC_FEATURES, ROLE_HINTS, AnalysisOptions,
                     ColumnSpec)

# How far from the fleet a unit must be before it is called out.
VERDICT_PCT = 2.0        # % on the DC-per-irradiance side
VERDICT_EFF_PT = 0.5     # percentage points on the inverter side
VERDICT_AC_PCT = 1.0     # a unit must actually be down on AC before it is blamed
MAX_FINDINGS = 6         # the summary is a headline, not a full listing


# --------------------------------------------------------------------------- #
# channel roles
# --------------------------------------------------------------------------- #
def guess_roles(specs: dict[str, ColumnSpec],
                opts: Optional[AnalysisOptions] = None) -> dict[str, str]:
    """Find which channel is AC power, DC power, irradiance, energy, temperature.

    Explicit settings in the profile win; anything left empty is matched against
    the translated column names, so the same code works on a plant whose
    channels are not called ``Data10``.
    """
    roles: dict[str, str] = {}
    explicit = {
        "ac_power": getattr(opts, "role_ac_power", "") if opts else "",
        "dc_power": getattr(opts, "role_dc_power", "") if opts else "",
        "irradiance": getattr(opts, "role_irradiance", "") if opts else "",
        "irradiance_h": getattr(opts, "role_irradiance_h", "") if opts else "",
        "ac_energy": getattr(opts, "role_ac_energy", "") if opts else "",
        "temp": getattr(opts, "role_temp", "") if opts else "",
    }
    for role, key in explicit.items():
        if key and key in specs:
            roles[role] = key

    def score(spec: ColumnSpec, hints: tuple[str, ...]) -> int:
        text = f"{spec.name} {spec.raw}".lower()
        return sum(1 for h in hints if h.lower() in text)

    for role, hints in ROLE_HINTS.items():
        if role in roles:
            continue
        best, best_score = "", 0
        taken = set(roles.values())
        for key, spec in specs.items():
            if not spec.is_numeric or key in taken:
                continue          # one column cannot be two different roles
            n = score(spec, hints)
            # "交流電力量" also contains "交流電力"; prefer the exact intent
            if role == "ac_power" and "電力量" in f"{spec.name}{spec.raw}":
                n = 0
            if role == "ac_energy" and "電力量" not in f"{spec.name}{spec.raw}":
                n = min(n, 0)
            if n > best_score:
                best, best_score = key, n
        if best:
            roles[role] = best
    return roles


# --------------------------------------------------------------------------- #
# conditions
# --------------------------------------------------------------------------- #
def stable_mask(irradiance: pd.Series, min_value: float, tol_pct: float,
                window: int) -> pd.Series:
    """Minutes with decent, steady light.

    Comparing units at dawn, at dusk or through passing cloud mostly measures
    the weather, not the plant: a cloud edge moves across 20 inverters over
    tens of seconds and every one of them reads differently for reasons that
    have nothing to do with its health.
    """
    s = pd.to_numeric(irradiance, errors="coerce")
    ok = s >= float(min_value)
    if window and window > 1 and tol_pct > 0:
        roll = s.rolling(window, center=True, min_periods=window)
        spread = (roll.max() - roll.min()) / roll.mean().abs().replace(0, np.nan)
        ok &= (spread * 100.0) <= float(tol_pct)
    return ok.fillna(False)


# --------------------------------------------------------------------------- #
# attribution
# --------------------------------------------------------------------------- #
@dataclass
class DiagnosticsResult:
    attribution: Optional[pd.DataFrame] = None
    availability: Optional[pd.DataFrame] = None
    efficiency: Optional[pd.DataFrame] = None
    performance: Optional[pd.DataFrame] = None
    findings: list[str] = None            # type: ignore[assignment]
    scatter: Optional[dict[str, Any]] = None
    tidy: Optional[pd.DataFrame] = None
    roles: dict[str, str] = None          # type: ignore[assignment]
    notes: list[str] = None               # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.findings is None:
            self.findings = []
        if self.roles is None:
            self.roles = {}
        if self.notes is None:
            self.notes = []


def _series(table: LoadedTable, key: str) -> Optional[pd.Series]:
    if not key or key not in table.df.columns:
        return None
    return pd.to_numeric(table.df[key], errors="coerce")


def _interval_hours(table: LoadedTable) -> float:
    if not table.time_column or table.time_column not in table.df.columns:
        return np.nan
    ts = pd.DatetimeIndex(pd.to_datetime(table.df[table.time_column], errors="coerce"))
    if len(ts) < 3:
        return np.nan
    delta = pd.Series(ts).diff().dropna()
    if delta.empty:
        return np.nan
    return float(pd.Timedelta(delta.mode().iloc[0]).total_seconds()) / 3600.0


def unit_table(tables: Iterable[LoadedTable], roles: dict[str, str],
               opts: AnalysisOptions) -> pd.DataFrame:
    """One row per unit: energy, DC-per-irradiance, efficiency, sample counts."""
    ac_k, dc_k, g_k = roles.get("ac_power"), roles.get("dc_power"), roles.get("irradiance")
    rows: list[dict[str, Any]] = []
    for t in tables:
        ac = _series(t, ac_k)
        dc = _series(t, dc_k)
        irr = _series(t, g_k)
        if ac is None:
            continue
        hours = _interval_hours(t)
        daylight = (irr > 0.05) if irr is not None else ac.notna()
        if opts.stable_only and irr is not None:
            good = stable_mask(irr, opts.stable_min_irradiance,
                               opts.stable_tolerance_pct, opts.stable_window)
        else:
            good = daylight
        row: dict[str, Any] = {
            "Entity": t.entity,
            "AC kWh": (float(ac[daylight].sum()) * hours) if np.isfinite(hours) else np.nan,
            "Samples used": int(good.sum()),
            "Daylight samples": int(daylight.sum()),
        }
        if dc is not None:
            row["DC kWh"] = (float(dc[daylight].sum()) * hours) if np.isfinite(hours) else np.nan
            eff = (ac / dc).where((dc > 5) & good)
            row["Efficiency %"] = float(eff.median() * 100.0) if eff.notna().any() else np.nan
        if irr is not None and dc is not None:
            dcg = (dc / irr).where(good & (irr > 0))
            row["DC per irradiance"] = float(dcg.median()) if dcg.notna().any() else np.nan
        if irr is not None:
            acg = (ac / irr).where(good & (irr > 0))
            row["AC per irradiance"] = float(acg.median()) if acg.notna().any() else np.nan
        cap = opts.capacity_for(t.entity)
        if cap > 0 and irr is not None and np.isfinite(hours):
            # PR = produced energy / (irradiation x capacity), IEC 61724 shape
            irradiation = float(irr[daylight].sum()) * hours          # kWh/m2
            if irradiation > 0:
                row["PR"] = round(row["AC kWh"] / (irradiation * cap), 4)
                row["Capacity kW"] = cap
        rows.append(row)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.groupby("Entity", as_index=False).agg(
        {c: ("sum" if c in ("AC kWh", "DC kWh", "Samples used", "Daylight samples")
             else "mean") for c in df.columns if c != "Entity"})

    # everything is expressed against the fleet median, not the mean: one very
    # bad unit would drag a mean and flatter everybody else
    for col, out in (("AC kWh", "AC vs fleet %"),
                     ("DC per irradiance", "DC side vs fleet %"),
                     ("AC per irradiance", "AC per irradiance vs fleet %")):
        if col in df.columns and df[col].notna().any():
            med = float(df[col].median())
            if med:
                df[out] = ((df[col] - med) / med * 100.0).round(2)
    if "Efficiency %" in df.columns and df["Efficiency %"].notna().any():
        df["Efficiency vs fleet pt"] = (
            df["Efficiency %"] - float(df["Efficiency %"].median())).round(2)
    return df


def _verdict(dc_dev: float, eff_dev: float, ac_dev: float) -> str:
    # A unit at or above the fleet is not a fault, whatever its internals say.
    # Without this a healthy unit with a slightly soft inverter but a strong
    # array gets reported as broken.
    if not np.isfinite(ac_dev) or ac_dev > -VERDICT_AC_PCT:
        return "OK"
    dc_bad = np.isfinite(dc_dev) and dc_dev <= -VERDICT_PCT
    eff_bad = np.isfinite(eff_dev) and eff_dev <= -VERDICT_EFF_PT
    if dc_bad and eff_bad:
        return "DC side + inverter / 直流側とインバータの両方"
    if dc_bad:
        return "DC side (array, strings, shading, soiling) / 直流側（アレイ・ストリング）"
    if eff_bad:
        return "Inverter / インバータ"
    if np.isfinite(ac_dev) and ac_dev <= -VERDICT_PCT:
        return "Unexplained - check curtailment and downtime / 原因不明（出力制御・停止を確認）"
    return "OK"


def attribution(df: pd.DataFrame) -> pd.DataFrame:
    """Add the verdict column: where each unit's shortfall actually is."""
    if df.empty:
        return df
    out = df.copy()
    dc = out.get("DC side vs fleet %", pd.Series(np.nan, index=out.index))
    eff = out.get("Efficiency vs fleet pt", pd.Series(np.nan, index=out.index))
    ac = out.get("AC vs fleet %", pd.Series(np.nan, index=out.index))
    out["Verdict"] = [
        _verdict(float(dc.iloc[i]) if pd.notna(dc.iloc[i]) else np.nan,
                 float(eff.iloc[i]) if pd.notna(eff.iloc[i]) else np.nan,
                 float(ac.iloc[i]) if pd.notna(ac.iloc[i]) else np.nan)
        for i in range(len(out))
    ]
    sort_col = "AC vs fleet %" if "AC vs fleet %" in out.columns else "Entity"
    return out.sort_values(sort_col).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# availability
# --------------------------------------------------------------------------- #
def availability(tables: Iterable[LoadedTable], roles: dict[str, str],
                 opts: AnalysisOptions) -> pd.DataFrame:
    """Minutes with usable light but no output, and what they cost in kWh.

    The loss is priced against what the fleet median unit made in the very same
    minutes, which is the only fair yardstick - the weather was identical.
    """
    ac_k, g_k = roles.get("ac_power"), roles.get("irradiance")
    if not ac_k or not g_k:
        return pd.DataFrame()

    frames: dict[str, pd.Series] = {}
    light: dict[str, pd.Series] = {}
    hours = np.nan
    for t in tables:
        ac, irr = _series(t, ac_k), _series(t, g_k)
        if ac is None or irr is None or not t.time_column:
            continue
        idx = pd.DatetimeIndex(pd.to_datetime(t.df[t.time_column], errors="coerce"))
        ac.index, irr.index = idx, idx
        key = t.entity
        frames[key] = pd.concat([frames[key], ac]) if key in frames else ac
        light[key] = pd.concat([light[key], irr]) if key in light else irr
        h = _interval_hours(t)
        if np.isfinite(h):
            hours = h
    if not frames:
        return pd.DataFrame()

    power = pd.DataFrame(frames).sort_index()
    power = power[~power.index.duplicated(keep="first")]
    irr = pd.DataFrame(light).sort_index()
    irr = irr[~irr.index.duplicated(keep="first")]
    usable = irr.mean(axis=1) >= float(opts.stable_min_irradiance)
    reference = power.median(axis=1)

    rows = []
    for unit in power.columns:
        s = power[unit]
        dead = usable & (s.fillna(0) <= 0)
        missing = usable & s.isna()
        lost = float(reference[dead].sum()) * (hours if np.isfinite(hours) else 0.0)
        rows.append({
            "Entity": unit,
            "Usable-light samples": int(usable.sum()),
            "Zero output": int(dead.sum()),
            "Missing data": int(missing.sum()),
            "Availability %": round(100.0 * (1 - dead.sum() / max(1, usable.sum())), 2),
            "Lost kWh vs fleet": round(lost, 1),
        })
    df = pd.DataFrame(rows)
    return df.sort_values("Lost kWh vs fleet", ascending=False).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# scatter + tidy
# --------------------------------------------------------------------------- #
def scatter_payload(tables: Iterable[LoadedTable], roles: dict[str, str],
                    opts: AnalysisOptions, max_points: int = 900) -> dict[str, Any]:
    """Power against irradiance per unit - where a DC fault is obvious by eye."""
    ac_k, g_k = roles.get("ac_power"), roles.get("irradiance")
    if not ac_k or not g_k:
        return {}
    series: dict[str, list[list[float]]] = {}
    for t in tables:
        ac, irr = _series(t, ac_k), _series(t, g_k)
        if ac is None or irr is None:
            continue
        keep = (irr > 0.05) & ac.notna() & irr.notna()
        x, y = irr[keep], ac[keep]
        if len(x) > max_points:                      # even thinning, keeps the shape
            step = int(np.ceil(len(x) / max_points))
            x, y = x.iloc[::step], y.iloc[::step]
        pts = [[round(float(a), 4), round(float(b), 3)] for a, b in zip(x, y)]
        series.setdefault(t.entity, []).extend(pts)
    if not series:
        return {}
    return {"x_label": ac_k, "series": series}


def tidy_table(tables: Iterable[LoadedTable], specs: dict[str, ColumnSpec],
               metrics: list[str], max_rows: int = 200_000) -> pd.DataFrame:
    """Long format (time / unit / metric / value) - what PivotTables want."""
    out: list[pd.DataFrame] = []
    total = 0
    for t in tables:
        if not t.time_column or t.time_column not in t.df.columns:
            continue
        for m in metrics:
            if m not in t.df.columns:
                continue
            spec = specs.get(m)
            block = pd.DataFrame({
                "Time": pd.to_datetime(t.df[t.time_column], errors="coerce"),
                "Entity": t.entity,
                "Metric": (spec.name if spec and spec.name else m),
                "Unit": (spec.unit if spec else ""),
                "Value": pd.to_numeric(t.df[m], errors="coerce"),
            })
            out.append(block)
            total += len(block)
            if total >= max_rows:
                break
        if total >= max_rows:
            break
    if not out:
        return pd.DataFrame()
    return pd.concat(out, ignore_index=True).head(max_rows)


# --------------------------------------------------------------------------- #
# findings
# --------------------------------------------------------------------------- #
def findings(attr: Optional[pd.DataFrame], avail: Optional[pd.DataFrame],
             lang: str = "ja") -> list[str]:
    """A few plain sentences: which units, how bad, and where to look."""
    ja = lang == "ja"
    out: list[str] = []
    if attr is not None and not attr.empty and "Verdict" in attr.columns:
        bad = attr[attr["Verdict"] != "OK"]
        if bad.empty:
            out.append("すべての号機が全体の中央値に近い値です。"
                       if ja else "Every unit tracks the fleet median.")
        extra = max(0, len(bad) - MAX_FINDINGS)
        for _, r in bad.head(MAX_FINDINGS).iterrows():
            ac = r.get("AC vs fleet %", np.nan)
            dc = r.get("DC side vs fleet %", np.nan)
            ef = r.get("Efficiency vs fleet pt", np.nan)
            verdict = str(r["Verdict"]).split(" / ")
            label = verdict[-1] if ja and len(verdict) > 1 else verdict[0]
            if ja:
                out.append(
                    f"{r['Entity']}：交流電力量が全体比 {ac:+.1f} %"
                    + (f"、直流側 {dc:+.1f} %" if pd.notna(dc) else "")
                    + (f"、変換効率 {ef:+.2f} pt" if pd.notna(ef) else "")
                    + f" → {label}")
            else:
                out.append(
                    f"{r['Entity']}: AC energy {ac:+.1f} % vs fleet"
                    + (f", DC side {dc:+.1f} %" if pd.notna(dc) else "")
                    + (f", efficiency {ef:+.2f} pt" if pd.notna(ef) else "")
                    + f" -> {label}")
        if extra:
            out.append(f"他に {extra} 号機が全体を下回っています（詳細は表を参照）。"
                       if ja else
                       f"{extra} more unit(s) are below the fleet - see the table.")
    if avail is not None and not avail.empty:
        worst = avail.iloc[0]
        if float(worst.get("Lost kWh vs fleet", 0)) > 0:
            out.append(
                f"{worst['Entity']}：日射のある時間帯に出力ゼロが "
                f"{int(worst['Zero output'])} 分、損失は約 "
                f"{worst['Lost kWh vs fleet']:.0f} kWh。"
                if ja else
                f"{worst['Entity']}: {int(worst['Zero output'])} minute(s) with no output "
                f"under usable light, about {worst['Lost kWh vs fleet']:.0f} kWh lost.")
    return out


# --------------------------------------------------------------------------- #
def run_diagnostics(tables: list[LoadedTable], specs: dict[str, ColumnSpec],
                    metrics: list[str], opts: AnalysisOptions,
                    lang: str = "ja") -> DiagnosticsResult:
    """Everything the feature toggles asked for, and nothing they did not."""
    res = DiagnosticsResult()
    wanted = [f for f in DIAGNOSTIC_FEATURES if opts.any_target(f)]
    if not wanted or not tables:
        return res

    res.roles = guess_roles(specs, opts)
    missing = [r for r in ("ac_power", "dc_power", "irradiance") if r not in res.roles]
    if missing:
        res.notes.append(
            "Channel role(s) not identified: " + ", ".join(missing)
            + " - set them on the Analysis tab to enable the diagnostics")

    needs_units = {"attribution", "efficiency", "performance", "findings"} & set(wanted)
    unit_df = pd.DataFrame()
    if needs_units and "ac_power" in res.roles:
        unit_df = unit_table(tables, res.roles, opts)

    if "attribution" in wanted and not unit_df.empty:
        res.attribution = attribution(unit_df)
    if "efficiency" in wanted and not unit_df.empty and "Efficiency %" in unit_df.columns:
        cols = ["Entity", "Efficiency %", "Efficiency vs fleet pt", "Samples used"]
        res.efficiency = unit_df[[c for c in cols if c in unit_df.columns]].copy()
    if "performance" in wanted:
        if not unit_df.empty and "PR" in unit_df.columns:
            cols = ["Entity", "Capacity kW", "AC kWh", "PR"]
            res.performance = unit_df[[c for c in cols if c in unit_df.columns]].copy()
            # PR above 1 is not physically possible for a real plant; the usual
            # cause is entering the AC rating instead of the DC nameplate.
            worst = float(pd.to_numeric(res.performance["PR"], errors="coerce").max())
            if np.isfinite(worst) and worst > 1.0:
                res.notes.append(
                    f"PR reaches {worst:.2f}, which is above 1 and therefore not "
                    "physically possible - the capacity entered is too small. "
                    "Use the DC nameplate of the array (usually 1.1-1.3x the AC "
                    "rating), not the inverter's AC rating.")
        else:
            res.notes.append(
                "Performance ratio skipped: set the nameplate DC capacity "
                "(kW) on the Analysis tab")
    if "availability" in wanted:
        res.availability = availability(tables, res.roles, opts)
    if "scatter" in wanted:
        res.scatter = scatter_payload(tables, res.roles, opts)
    if "tidy" in wanted:
        res.tidy = tidy_table(tables, specs, metrics)
    if "findings" in wanted:
        res.findings = findings(res.attribution if res.attribution is not None
                                else (attribution(unit_df) if not unit_df.empty else None),
                                res.availability, lang)
    return res
