"""Self-contained interactive HTML report.

One file, no internet: filters, a colour-scaled matrix table, interactive
line charts with crosshair tooltips, light/dark, CSV export of the current
view.  Everything is embedded as JSON at the bottom of the page.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd

from .analysis import AnalysisResult
from .models import APP_NAME, APP_VERSION, HTML_SECTIONS, Rule, RuleKind
from .rules import gradient_style, hex_color

MAX_TABLE_CELLS = 400_000


def _jsonable(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return None if not np.isfinite(f) else round(f, 6)
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.isoformat(sep=" ", timespec="seconds")
    if pd.isna(v):
        return None
    return str(v)


def _downsample(df: pd.DataFrame, limit: int) -> tuple[pd.DataFrame, int]:
    if limit <= 0 or len(df) <= limit:
        return df, 1
    step = int(np.ceil(len(df) / limit))
    return df.iloc[::step], step


def _frame_payload(df: pd.DataFrame, limit: int) -> dict[str, Any]:
    small, step = _downsample(df, limit)
    return {
        "time": [_jsonable(t) for t in small.index],
        "columns": [str(c) for c in small.columns],
        "series": {str(c): [_jsonable(v) for v in small[c].to_numpy()] for c in small.columns},
        "step": step,
        "rows_total": int(len(df)),
    }


def _table_payload(df: Optional[pd.DataFrame], limit_rows: int = 5000) -> Optional[dict[str, Any]]:
    if df is None or df.empty:
        return None
    d = df.head(limit_rows)
    return {
        "columns": [str(c) for c in d.columns],
        "rows": [[_jsonable(v) for v in row] for row in d.to_numpy(dtype=object)],
        "truncated": bool(len(df) > limit_rows),
        "rows_total": int(len(df)),
    }


def _is_shared(df: Optional[pd.DataFrame]) -> bool:
    """True when every unit reports the identical series.

    Irradiance and ambient temperature are measured once for the plant and
    copied into all 20 PCS blocks, so twenty identically-coloured lines carry
    one line's worth of information.  The page draws such a channel once, in
    its own colour, and lets the reader put it on any graph.
    """
    if df is None or df.empty or df.shape[1] < 2:
        return False
    try:
        num = df.apply(pd.to_numeric, errors="coerce")
    except (TypeError, ValueError):
        return False
    first = num.iloc[:, 0].to_numpy(dtype="float64")
    if not np.isfinite(first).any():
        return False                      # an empty channel is not "shared"
    for c in list(num.columns)[1:]:
        other = num[c].to_numpy(dtype="float64")
        if not np.allclose(first, other, rtol=1e-6, atol=1e-9, equal_nan=True):
            return False
    return True


def _metric_entry(m: Any, max_points: int, chosen: bool,
                  with_deviation: bool = True) -> dict[str, Any]:
    ncols = max(1, len(m.values.columns))
    row_limit = max(200, min(max_points, int(MAX_TABLE_CELLS / ncols)))
    return {
        "key": m.metric,
        "title": m.title,
        "unit": m.unit,
        "entities": m.entities,
        "shared": _is_shared(m.values),
        "value": _frame_payload(m.values, row_limit),
        "deviation": (_frame_payload(m.deviation, row_limit)
                      if with_deviation and m.deviation is not None
                      and not m.deviation.empty else None),
        "deviation_percent": bool(m.deviation_percent),
        "reference_mode": m.reference_mode,
        "cells": int(len(m.values) * ncols),
        "chosen": bool(chosen),
    }


def _diagnostics_payload(diag: Any, analysis: Any) -> dict[str, Any]:
    """Only the diagnostics that were targeted at the HTML."""
    out: dict[str, Any] = {"findings": [], "attribution": None,
                           "availability": None, "efficiency": None,
                           "performance": None, "scatter": None, "notes": []}
    if diag is None or analysis is None:
        return out
    if analysis.wants("findings", "html"):
        out["findings"] = list(diag.findings or [])
        out["notes"] = list(diag.notes or [])
    # Excel keeps every intermediate column; the page shows the ones that carry
    # the message, or the table is too wide to read.
    headline = ["Entity", "AC kWh", "AC vs fleet %", "DC side vs fleet %",
                "Efficiency %", "Efficiency vs fleet pt", "Samples used", "Verdict"]
    for key in ("attribution", "availability", "efficiency", "performance"):
        if not analysis.wants(key, "html"):
            continue
        df = getattr(diag, key, None)
        if df is not None and key == "attribution" and not df.empty:
            df = df[[c for c in headline if c in df.columns]]
        out[key] = _table_payload(df)
    if analysis.wants("scatter", "html"):
        out["scatter"] = getattr(diag, "scatter", None)
    return out


def _default_graphs(roles: dict[str, str], analysis: Any,
                    keys: set[str]) -> list[dict[str, Any]]:
    """The extra graphs the page opens with.

    Channel *keys*, not indexes: the reader can hide channels and the page
    resolves the keys itself, so a graph never points at the wrong series.
    """
    out: list[dict[str, Any]] = []
    if analysis is None or not analysis.wants("weather", "html"):
        return out
    want = [roles.get("irradiance", ""), roles.get("temp", "")]
    have = [k for k in want if k and k in keys]
    if have:
        out.append({"channels": have})
    return out


def build_payload(
    result: AnalysisResult,
    rules: list[Rule],
    title: str,
    subtitle: str = "",
    max_points: int = 4000,
    extra_matrices: Optional[list[Any]] = None,
    extra_points: int = 1500,
    color_controls: bool = True,
    worst_n: int = 6,
    language: str = "ja",
    sections: Optional[Sequence[str]] = None,
    diagnostics: Optional[Any] = None,
    analysis: Optional[Any] = None,
    roles: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    """JSON for the page.

    ``extra_matrices`` are channels the app did *not* select for the analysis;
    they travel with the report so the reader can switch to them without
    re-running the job.  They carry fewer points and no deviation frame, which
    keeps the file from doubling in size.
    """
    want = set(sections) if sections is not None else set(HTML_SECTIONS)
    # A section left out is not just hidden: its data never reaches the file.
    needs_series = bool({"chart", "matrix"} & want)
    metrics = ([_metric_entry(m, max_points, chosen=True) for m in result.matrices]
               if needs_series else [])
    chosen_keys = {m["key"] for m in metrics}
    if needs_series:
        for m in (extra_matrices or []):
            if m.metric in chosen_keys:
                continue
            metrics.append(_metric_entry(m, extra_points, chosen=False,
                                         with_deviation=False))

    zero_rule = next((r for r in rules if r.kind is RuleKind.ZERO and r.enabled), None)
    legend = [
        {"label": r.display_name(), "color": hex_color(r.fill), "kind": r.kind.value}
        for r in rules if r.enabled and r.kind not in (RuleKind.COLOR_SCALE, RuleKind.DATA_BAR)
    ]
    style = gradient_style(rules)
    style["controls"] = bool(color_controls)
    return {
        "app": f"{APP_NAME} {APP_VERSION}",
        "title": title,
        "subtitle": subtitle,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "metrics": metrics,
        "daily": _table_payload(result.daily) if "daily" in want else None,
        "zeros": _table_payload(result.zero_report) if "zeros" in want else None,
        "quality": _table_payload(result.quality) if "quality" in want else None,
        "alarms": _table_payload(result.alarms) if "alarms" in want else None,
        "sections": sorted(want, key=list(HTML_SECTIONS).index),
        **_diagnostics_payload(diagnostics, analysis),
        "zero_highlight": zero_rule is not None,
        "legend": legend,
        "style": style,
        "has_extra": any(not m["chosen"] for m in metrics),
        "worst_n": max(1, int(worst_n)),
        "roles": dict(roles or {}),
        "default_graphs": _default_graphs(dict(roles or {}), analysis,
                                         {m["key"] for m in metrics}),
        "lang": (language if language in ("ja", "en", "both") else "ja"),
    }


def write_html_report(out_path: Path, payload: dict[str, Any]) -> Path:
    html = TEMPLATE.replace("/*__PAYLOAD__*/ null", json.dumps(payload, ensure_ascii=False))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


# --------------------------------------------------------------------------- #
# the page
# --------------------------------------------------------------------------- #
TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DataScope report</title>
<style>
:root{
  color-scheme: light;
  --surface:#fcfcfb; --plane:#f4f4f1; --card:#ffffff;
  --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,.10);
  --zero:#e6e6e3; --accent:#2a78d6;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100;
  --s5:#e87ba4; --s6:#008300; --s7:#4a3aa7; --s8:#e34948;
  /* plant-wide channels (irradiance, temperature): one colour each, and
     deliberately outside the unit palette so they can never be mistaken
     for a PCS line. */
  --w1:#3f3f46; --w2:#a16207; --w3:#0e7490; --w4:#9d174d;
  --pos:#2a78d6; --neg:#d03b3b;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme: dark;
    --surface:#1a1a19; --plane:#0d0d0d; --card:#212120;
    --ink:#ffffff; --ink2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
    --zero:#3a3a37;
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500;
    --s5:#d55181; --s6:#008300; --s7:#9085e9; --s8:#e66767;
    --w1:#e4e4e7; --w2:#d4a017; --w3:#22a5c0; --w4:#e05285;
    --pos:#3987e5; --neg:#d03b3b;
  }
}
:root[data-theme="dark"]{
  color-scheme: dark;
  --surface:#1a1a19; --plane:#0d0d0d; --card:#212120;
  --ink:#ffffff; --ink2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
  --zero:#3a3a37;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500;
  --s5:#d55181; --s6:#008300; --s7:#9085e9; --s8:#e66767;
  --w1:#e4e4e7; --w2:#d4a017; --w3:#22a5c0; --w4:#e05285;
  --pos:#3987e5; --neg:#d03b3b;
}
@media print{
  .controls, #chanField, .rowbtns, .daytabs, #colorCard, .tip,
  .gbar, #buildBar, .zoombar{display:none!important}
  body{background:#fff}
  .card{break-inside:avoid;page-break-inside:avoid;border-color:#ccc;box-shadow:none}
  .tblwrap{max-height:none;overflow:visible}
  table.grid th{position:static}
  table.grid th:first-child,table.grid td:first-child{position:static}
}
*{box-sizing:border-box}
body{margin:0;background:var(--plane);color:var(--ink);
  font:14px/1.5 system-ui,-apple-system,"Segoe UI","Hiragino Kaku Gothic ProN","Yu Gothic",sans-serif}
header{padding:22px 24px 14px;background:var(--surface);border-bottom:1px solid var(--border)}
h1{margin:0;font-size:20px;letter-spacing:-.01em}
.sub{color:var(--ink2);font-size:13px;margin-top:4px}
.meta{color:var(--muted);font-size:12px;margin-top:2px}
main{padding:18px 24px 60px;max-width:1600px;margin:0 auto}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:16px 18px;margin-bottom:18px}
.card h2{margin:0 0 12px;font-size:15px;font-weight:600}
#chartCard{position:relative}
.controls{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:flex-end;
  background:var(--surface);border:1px solid var(--border);border-radius:10px;
  padding:12px 16px;margin-bottom:18px;position:sticky;top:0;z-index:20}
.field{display:flex;flex-direction:column;gap:4px}
.field label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
input[type=datetime-local]{min-width:186px}
select,input[type=text],input[type=number],input[type=datetime-local],button{
  font:inherit;font-size:13px;color:var(--ink);background:var(--card);
  border:1px solid var(--border);border-radius:7px;padding:6px 9px}
button{cursor:pointer}
button:hover{border-color:var(--accent)}
button.primary{background:var(--accent);color:#fff;border-color:transparent}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--border);
  border-radius:999px;padding:4px 11px;font-size:12px;cursor:pointer;background:var(--card);
  user-select:none}
.chip .dot{width:9px;height:9px;border-radius:50%}
.chip.off{opacity:.38}
.legend{display:flex;flex-wrap:wrap;gap:8px 16px;font-size:12px;color:var(--ink2);
  margin-top:10px}
.legend span.sw{display:inline-block;width:12px;height:12px;border-radius:3px;
  border:1px solid var(--border);vertical-align:-2px;margin-right:5px}
.chartwrap{position:relative}
#chart{display:block;width:100%;height:auto;overflow:visible}
.chip svg{display:inline-block;flex:none;width:16px;height:9px;overflow:visible}
.tip{position:absolute;pointer-events:none;background:var(--card);color:var(--ink);
  border:1px solid var(--border);border-radius:8px;padding:8px 10px;font-size:12px;
  box-shadow:0 6px 20px rgba(0,0,0,.14);opacity:0;transition:opacity .08s;min-width:150px;
  max-width:min(560px,92%);z-index:5;backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px)}
.tip b{font-weight:600}
.tip table{border-collapse:collapse;margin-top:3px}
.tip td{padding:1px 0;white-space:nowrap}
.tip td.v{text-align:right;padding-left:12px;font-variant-numeric:tabular-nums}
/* every visible unit belongs in the readout, so a long list becomes columns */
.tip .tcols{display:flex;gap:16px;align-items:flex-start}
.tip .th{color:var(--muted);font-size:11px;margin-top:5px;white-space:nowrap}
.tip .th:first-child{margin-top:2px}
.tip .dot2{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:6px;
  vertical-align:-1px}
.tblwrap{overflow:auto;max-height:70vh;border:1px solid var(--border);border-radius:8px}
table.grid{border-collapse:separate;border-spacing:0;width:100%;font-size:12px;
  font-variant-numeric:tabular-nums}
table.grid th,table.grid td{padding:4px 8px;border-bottom:1px solid var(--border);
  white-space:nowrap;text-align:right}
table.grid th{position:sticky;top:0;background:var(--plane);z-index:2;font-weight:600;
  text-align:right}
table.grid th:first-child,table.grid td:first-child{text-align:left;position:sticky;left:0;
  background:var(--surface);z-index:1}
table.grid th:first-child{z-index:3}
table.grid tr:hover td{outline:1px solid var(--border)}
td.zero{background:var(--zero)!important;color:var(--muted)}
td.nan{color:var(--muted);background:repeating-linear-gradient(45deg,transparent,transparent 3px,var(--grid) 3px,var(--grid) 4px)}
.small{font-size:12px;color:var(--muted)}
.rowbtns{display:flex;gap:8px;margin-top:10px;align-items:center}
.kpis{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:14px}
.kpi{background:var(--card);border:1px solid var(--border);border-radius:9px;
  padding:10px 14px;min-width:130px}
.kpi .k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.kpi .v{font-size:20px;font-weight:600;margin-top:2px}
.kpi .u{font-size:11px;color:var(--muted)}
.hidden{display:none}
input[type=color]{width:34px;height:28px;padding:1px;background:var(--card);
  border:1px solid var(--border);border-radius:6px;cursor:pointer}
.swatches{display:flex;gap:5px;align-items:center}
.inline{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:flex-end}
.scaleBar{display:flex;align-items:center;gap:0;margin-top:10px;border:1px solid var(--border);
  border-radius:6px;overflow:hidden;max-width:520px}
.scaleBar div{flex:1;height:20px;font-size:10px;line-height:20px;text-align:center;
  font-variant-numeric:tabular-nums}
.findings{margin:0;padding-left:20px}
.findings li{margin:4px 0;line-height:1.55}
.findings li.ok{color:var(--ink2)}
#scatter{display:block;width:100%;height:auto;overflow:visible}
.panel{position:relative;margin-bottom:2px}
.panel svg{display:block;width:100%;height:auto;overflow:visible}
.panel .cap{position:absolute;left:66px;top:2px;font-size:11.5px;font-weight:600;
  color:var(--ink2);pointer-events:none}
.tag{font-size:10px;border:1px solid var(--border);border-radius:4px;padding:0 4px;
  color:var(--muted);margin-left:6px}
.daytabs{display:flex;flex-wrap:wrap;gap:0;margin:-4px 0 12px;
  border-bottom:1px solid var(--border)}
.daytab{border:1px solid transparent;border-bottom:none;background:none;color:var(--ink2);
  border-radius:8px 8px 0 0;padding:6px 13px;font-size:12.5px;cursor:pointer;
  margin-bottom:-1px;font-variant-numeric:tabular-nums;white-space:nowrap}
.daytab:hover{color:var(--ink);background:var(--plane)}
.daytab.on{background:var(--card);border-color:var(--border);color:var(--ink);
  font-weight:600;border-bottom:1px solid var(--card)}
.daytab .n{color:var(--muted);font-weight:400;margin-left:6px;font-size:11px}
/* --- graph builder --- */
#buildCard{position:relative}
#buildBar{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:flex-end;margin-bottom:12px}
.gbox{border:1px solid var(--border);border-radius:9px;padding:10px 12px 8px;
  margin-bottom:12px;background:var(--card)}
.gbox.empty{color:var(--muted);font-size:12.5px}
.gbar{display:flex;flex-wrap:wrap;gap:6px 8px;align-items:center;margin-top:8px}
.gbar .sp{flex:1 1 12px}
.gchip{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--border);
  border-radius:999px;padding:3px 6px 3px 10px;font-size:12px;background:var(--surface)}
.gchip .x{cursor:pointer;color:var(--muted);border:none;background:none;font-size:14px;
  line-height:1;padding:0 3px}
.gchip .x:hover{color:var(--neg)}
.gchip .sw{width:16px;height:3px;border-radius:2px;flex:none}
.gchip .gain{width:52px;font-size:11px;padding:1px 3px;text-align:right}
.gchip .x:disabled{opacity:.25;cursor:default}
#chart,.panel svg,.gbox svg{user-select:none;-webkit-user-select:none}
.zoombar{display:flex;flex-wrap:wrap;gap:6px 8px;align-items:center;margin-bottom:10px}
.zoombar button{padding:4px 9px}
.zoombar button:disabled{opacity:.4;cursor:default;border-color:var(--border)}
.zoombar .small{color:var(--muted)}
.zoombar .sep{width:1px;height:20px;background:var(--border);margin:0 2px}
.tipset{display:inline-flex;align-items:center;gap:5px;font-size:11px;color:var(--muted)}
.tipset select{font-size:12px;padding:3px 6px}
.tipset input[type=range]{width:88px;accent-color:var(--accent);padding:0}
.gwrap{display:block}
.gwrap.two{display:grid;grid-template-columns:repeat(2,1fr);gap:0 16px}
</style>
</head>
<body>
<header>
  <h1 id="title">DataScope report</h1>
  <div class="sub" id="subtitle"></div>
  <div class="meta" id="meta"></div>
</header>
<main>
  <div class="controls">
    <div class="field"><label data-t="f.lang">Language</label>
      <select id="lang">
        <option value="ja">日本語</option>
        <option value="en">English</option>
        <option value="both">日本語 + English</option>
      </select></div>
    <div class="field"><label data-t="f.channel"></label><select id="metric" style="max-width:340px"></select></div>
    <div class="field" id="scopeField"><label data-t="f.list"></label>
      <select id="scope">
        <option value="chosen" data-t="o.chosen"></option>
        <option value="all" data-t="o.allch"></option>
      </select></div>
    <div class="field"><label data-t="f.findch"></label>
      <input type="text" id="mfilter" size="12" data-tp="p.findch"></div>
    <div class="field"><label data-t="f.layout"></label>
      <select id="layout">
        <option value="single" data-t="o.single"></option>
        <option value="stack" data-t="o.stack"></option>
        <option value="grid2" data-t="o.grid2"></option>
        <option value="overlay" data-t="o.overlay"></option>
      </select></div>
    <div class="field"><label data-t="f.view"></label>
      <select id="view">
        <option value="value" data-t="o.value"></option>
        <option value="deviation" data-t="o.dev"></option>
      </select></div>
    <div class="field"><label data-t="f.from"></label>
      <input type="datetime-local" id="from" step="60"></div>
    <div class="field"><label data-t="f.to"></label>
      <input type="datetime-local" id="to" step="60"></div>
    <div class="field"><label>&nbsp;</label>
      <div class="rowbtns"><button id="clearrange" data-t="b.clearrange"></button></div></div>
    <div class="field"><label data-t="f.units"></label><div class="chips" id="entities"></div></div>
    <div class="field"><label data-t="f.minspread"></label><input type="number" id="minspread" step="any" style="width:90px" placeholder="0"></div>
    <div class="field"><label data-t="f.findunit"></label>
      <input type="text" id="ufilter" size="8" placeholder="PCS0"></div>
    <div class="field"><label data-t="f.quick"></label>
      <div class="rowbtns">
        <input type="number" id="worstn" min="1" step="1" style="width:56px" data-tt="t.worstn">
        <button id="worst">Worst N</button><button id="all" data-t="b.all"></button>
        <button id="none" data-t="b.none"></button><button id="invert" data-t="b.invert"></button>
      </div>
    </div>
    <div class="field"><label data-t="f.rows"></label>
      <div class="rowbtns">
        <label class="small"><input type="checkbox" id="hidezero"> <span data-t="b.hidezero"></span></label>
      </div>
    </div>
    <div class="field"><label>&nbsp;</label>
      <div class="rowbtns">
        <button id="reset" data-t="b.reset"></button>
        <button id="csv" class="primary" data-t="b.csv"></button>
        <button id="theme" data-t="b.theme"></button>
      </div>
    </div>
  </div>

  <div class="card hidden" id="findingsCard">
    <h2 data-t="h.findings"></h2>
    <ul id="findings" class="findings"></ul>
    <div class="small" id="findingsNotes"></div>
  </div>

  <div class="card hidden" id="diagCard">
    <h2 data-t="h.diag"></h2>
    <div class="daytabs" id="diagTabs"></div>
    <div class="tblwrap"><table class="grid" id="diagTable"></table></div>
    <div class="rowbtns" style="margin-top:10px">
      <button id="diagCsv" data-t="b.csvtable"></button>
    </div>
    <div class="small" id="diagNote" style="margin-top:8px"></div>
  </div>

  <div class="card hidden" id="scatterCard">
    <h2 data-t="h.scatter"></h2>
    <div class="chartwrap" id="scatterwrap">
      <svg id="scatter" preserveAspectRatio="none"></svg>
    </div>
    <div class="small" data-t="n.scatter" style="margin-top:8px"></div>
  </div>

  <div class="card" id="chartCard">
    <h2 id="chartTitle"></h2>
    <div class="field hidden" id="chanField" style="margin-bottom:12px">
      <label data-t="f.channels"></label>
      <div class="chips" id="chanChips"></div>
      <div class="rowbtns" style="margin-top:8px">
        <button id="pairBtn" data-t="b.pair"></button>
        <button id="chanNone" data-t="b.chanone"></button>
      </div>
    </div>
    <div class="daytabs" id="cdaytabs"></div>
    <div class="kpis" id="kpis"></div>
    <div class="zoombar">
      <button id="zin" data-t="b.zin" data-tt="t.zoom"></button>
      <button id="zout" data-t="b.zout"></button>
      <button id="zleft" data-t="b.zleft"></button>
      <button id="zright" data-t="b.zright"></button>
      <span class="sep"></span>
      <button id="yin" data-t="b.yin" data-tt="t.yzoom"></button>
      <button id="yout" data-t="b.yout"></button>
      <button id="yup" data-t="b.yup"></button>
      <button id="ydown" data-t="b.ydown"></button>
      <label class="tipset"><span data-t="f.gheight"></span>
        <input type="range" id="gheight" min="60" max="250" step="10" data-tt="t.gheight">
      </label>
      <span class="sep"></span>
      <button id="zall" data-t="b.zall"></button>
      <span class="small" id="zspan"></span>
      <span class="sep"></span>
      <label class="tipset"><span data-t="f.tipmode"></span>
        <select id="tipmode" data-tt="t.tipmode">
          <option value="always" data-t="o.tipalways"></option>
          <option value="ctrl" data-t="o.tipctrl"></option>
          <option value="off" data-t="o.tipoff"></option>
        </select></label>
      <label class="tipset"><span data-t="f.tipclear"></span>
        <input type="range" id="tipclear" min="0" max="70" step="5" data-tt="t.tipclear">
      </label>
    </div>
    <div class="chartwrap" id="chartwrap">
      <svg id="chart" preserveAspectRatio="none"></svg>
    </div>
    <div id="stackwrap" class="hidden"></div>
    <div class="tip" id="tip"></div>
    <div class="chips" id="legend" style="margin-top:12px"></div>
    <div class="small" id="chartNote" style="margin-top:8px"></div>
    <div class="rowbtns" style="margin-top:12px">
      <button id="pngBtn" data-t="b.png"></button>
      <button id="jpgBtn" data-t="b.jpg"></button>
      <button id="printBtn" data-t="b.print"></button>
      <button id="saveBtn" class="primary" data-t="b.savehtml"></button>
      <button id="chartOnlyBtn" data-t="b.savechart"></button>
      <button id="windowBtn" data-t="b.savewindow"></button>
      <span class="small" data-t="n.export"></span>
    </div>
    <div class="small" data-t="n.keys" style="margin-top:6px"></div>
    <div class="small" data-t="n.days" style="margin-top:4px"></div>
    <div class="small" data-t="n.tip" style="margin-top:4px"></div>
  </div>

  <div class="card" id="buildCard">
    <h2 data-t="h.build"></h2>
    <div id="buildBar">
      <div class="field"><label data-t="f.gcols"></label>
        <select id="gcols">
          <option value="1" data-t="o.col1"></option>
          <option value="2" data-t="o.col2"></option>
        </select></div>
      <div class="field"><label>&nbsp;</label>
        <div class="rowbtns">
          <button id="addGraph" class="primary" data-t="b.addgraph"></button>
          <button id="resetGraphs" data-t="b.resetgraphs"></button>
        </div></div>
    </div>
    <div class="daytabs" id="gdaytabs"></div>
    <div class="zoombar" id="gzoomBar">
      <button id="gzin" data-t="b.zin" data-tt="t.zoom"></button>
      <button id="gzout" data-t="b.zout"></button>
      <button id="gzleft" data-t="b.zleft"></button>
      <button id="gzright" data-t="b.zright"></button>
      <span class="sep"></span>
      <button id="gyin" data-t="b.yin" data-tt="t.yzoom"></button>
      <button id="gyout" data-t="b.yout"></button>
      <button id="gyup" data-t="b.yup"></button>
      <button id="gydown" data-t="b.ydown"></button>
      <label class="tipset"><span data-t="f.gheight"></span>
        <input type="range" id="ggheight" min="60" max="250" step="10" data-tt="t.gheight">
      </label>
      <span class="sep"></span>
      <button id="gzall" data-t="b.zall"></button>
      <span class="small" id="gzspan"></span>
      <span class="sep"></span>
      <label class="tipset"><span data-t="f.tipmode"></span>
        <select id="gtipmode" data-tt="t.tipmode">
          <option value="always" data-t="o.tipalways"></option>
          <option value="ctrl" data-t="o.tipctrl"></option>
          <option value="off" data-t="o.tipoff"></option>
        </select></label>
      <label class="tipset"><span data-t="f.tipclear"></span>
        <input type="range" id="gtipclear" min="0" max="70" step="5" data-tt="t.tipclear">
      </label>
    </div>
    <div id="graphwrap" class="gwrap"></div>
    <div class="tip" id="btip"></div>
    <div class="small" id="gnote" style="margin-top:6px;color:var(--accent)"></div>
    <div class="small" data-t="n.zoom" style="margin-top:8px"></div>
    <div class="small" data-t="n.build" style="margin-top:4px"></div>
    <div class="small" data-t="n.shared" style="margin-top:4px"></div>
  </div>

  <div class="card hidden" id="colorCard">
    <h2><span data-t="h.colors"></span> <span class="tag" id="styleSrc"></span></h2>
    <div class="inline">
      <div class="field"><label data-t="f.steps"></label>
        <select id="cmode">
          <option value="off" data-t="o.off"></option>
          <option value="2color" data-t="o.c2"></option>
          <option value="3color" data-t="o.c3"></option>
          <option value="5color" data-t="o.c5"></option>
        </select></div>
      <div class="field"><label data-t="f.colors"></label>
        <div class="swatches" id="swatches"></div></div>
      <div class="field"><label data-t="f.scale"></label>
        <select id="cbounds">
          <option value="auto" data-t="o.auto"></option>
          <option value="numbers" data-t="o.fixed"></option>
        </select></div>
      <div class="field"><label data-t="f.stops"></label>
        <input type="text" id="cstops" size="22" placeholder="0, 25, 50, 75, 90"></div>
      <div class="field"><label data-t="f.strength"></label>
        <input type="number" id="cstrength" min="10" max="100" step="5" style="width:78px"></div>
      <div class="field"><label data-t="f.zero"></label>
        <div class="swatches"><input type="color" id="czero">
          <label class="small"><input type="checkbox" id="czeroOn"> <span data-t="b.on"></span></label></div></div>
      <div class="field"><label data-t="f.bars"></label>
        <div class="swatches"><input type="color" id="cbar">
          <label class="small"><input type="checkbox" id="cbarOn"> <span data-t="b.on"></span></label></div></div>
      <div class="field"><label>&nbsp;</label>
        <div class="rowbtns">
          <button id="crev" data-t="b.reverse"></button>
          <button id="cfromapp" data-t="b.fromapp"></button>
        </div></div>
    </div>
    <div class="scaleBar" id="scaleBar"></div>
    <div class="small" id="colorNote" style="margin-top:8px"></div>
  </div>

  <div class="card" id="matrixCard">
    <h2 data-t="h.matrix"></h2>
    <div class="daytabs" id="daytabs"></div>
    <div class="tblwrap"><table class="grid" id="matrix"></table></div>
    <div class="rowbtns">
      <button id="more" data-t="b.more"></button>
      <span class="small" id="matrixNote"></span>
    </div>
    <div class="legend" id="ruleLegend"></div>
  </div>

  <div class="card" id="dailyCard">
    <h2 data-t="h.daily"></h2>
    <div class="tblwrap"><table class="grid" id="daily"></table></div>
  </div>

  <div class="card" id="zeroCard">
    <h2 data-t="h.zeros"></h2>
    <div class="tblwrap"><table class="grid" id="zeros"></table></div>
  </div>

  <div class="card" id="alarmCard">
    <h2 data-t="h.alarms"></h2>
    <div class="tblwrap"><table class="grid" id="alarms"></table></div>
  </div>

  <div class="card" id="qualityCard">
    <h2 data-t="h.quality"></h2>
    <div class="tblwrap"><table class="grid" id="quality"></table></div>
  </div>
</main>

<script>
const __PAYLOAD = /*__PAYLOAD__*/ null;
/* A copy written by "save only this chart" carries a pruned payload in the head;
   the line above is then replaced by "null" so the file is actually smaller. */
const DATA = (typeof window !== "undefined" && window.__DATA_OVERRIDE__)
             ? window.__DATA_OVERRIDE__ : __PAYLOAD;
/* ---------- language -------------------------------------------------------
   Three modes: ja, en, both.  "both" keeps the bilingual labels this report
   used to have; the app writes its own UI language into the payload, so a
   Japanese session produces a Japanese report by default.                   */
const STR = {
  "f.lang":      ["言語",            "Language"],
  "f.channel":   ["項目",            "Channel"],
  "f.list":      ["項目一覧",         "Channel list"],
  "f.findch":    ["項目検索",         "Find channel"],
  "f.view":      ["表示",            "View"],
  "f.layout":    ["グラフ配置",       "Layout"],
  "f.channels":  ["表示する項目",     "Channels shown"],
  "o.single":    ["1項目",           "One channel"],
  "o.stack":     ["上下に並べる",     "Stacked, shared time axis"],
  "o.grid2":     ["横に2つずつ並べる", "Side by side, two per row"],
  "o.overlay":   ["1つに重ねる",      "Overlaid on one chart"],
  "n.overlayn":  ["単位が異なるため各項目の最大値に対する % で表示しています。",
                  "Units differ, so each channel is drawn as % of its own maximum."],
  "n.overlay1":  ["号機を1つだけ表示中のため、その号機の各項目を重ねています。",
                  "One unit is visible, so its channels are overlaid directly."],
  "n.overlaym":  ["表示中の号機の平均を項目ごとに1本で描いています。",
                  "One line per channel: the mean across the visible units."],
  "n.keys":      ["← → で1点ずつ移動（Shift で10点、Ctrl で60点）、Home/End で端、Esc で解除。"
                  + "＋ − で時間の拡大縮小、, . で前後へ、[ ] で縦軸の拡大縮小、PageUp/PageDown で上下へ、0 でリセット。",
                  "← → step one sample (Shift 10, Ctrl 60), Home/End jump to the ends, Esc clears. "
                  + "+ - zoom time, , and . step earlier/later, [ ] zoom the value axis, PageUp/PageDown move it, 0 resets."],
  "n.stack":     ["同じ時間軸で上下に並べています。どのグラフでもマウスを乗せると全項目の値が出ます。",
                  "Same time axis top to bottom - hover any panel to read every channel at that moment."],
  "f.from":      ["開始",            "From"],
  "f.to":        ["終了",            "To"],
  "f.units":     ["号機",            "Units"],
  "f.minspread": ["最小差",          "Min spread"],
  "f.findunit":  ["号機検索",         "Find unit"],
  "f.quick":     ["かんたん選択",      "Quick pick"],
  "f.rows":      ["行",              "Rows"],
  "f.steps":     ["段階",            "Steps"],
  "f.colors":    ["色",              "Colours"],
  "f.scale":     ["基準",            "Scale"],
  "f.stops":     ["しきい値",         "Step values"],
  "f.strength":  ["濃度 %",          "Strength %"],
  "f.zero":      ["ゼロ値",          "Zero"],
  "f.bars":      ["データバー",       "Bars"],
  "o.chosen":    ["アプリで選択した項目", "Selected in app"],
  "o.allch":     ["全項目",           "All channels"],
  "o.value":     ["実測値",           "Value"],
  "o.dev":       ["平均との差",        "Deviation vs fleet"],
  "o.off":       ["色なし",           "Off"],
  "o.c2":        ["2色",             "2 colours"],
  "o.c3":        ["3色",             "3 colours"],
  "o.c5":        ["5色",             "5 colours"],
  "o.auto":      ["最小 → 最大（自動）", "Min → max (auto)"],
  "o.fixed":     ["数値指定",          "Fixed steps"],
  "p.findch":    ["Data10 / 電力",    "Data10 / power"],
  "b.all":       ["すべて",           "All"],
  "b.none":      ["なし",            "None"],
  "b.invert":    ["反転選択",         "Invert"],
  "b.hidezero":  ["ゼロ行を隠す",      "hide all-zero"],
  "b.reset":     ["リセット",         "Reset"],
  "b.csv":       ["CSV 出力",         "Export CSV"],
  "b.theme":     ["表示テーマ",        "Theme"],
  "b.on":        ["有効",            "on"],
  "b.reverse":   ["反転",            "Reverse"],
  "b.fromapp":   ["アプリ設定に戻す",   "Back to app setting"],
  "b.more":      ["行を追加表示",      "Show more rows"],
  "b.clearrange":["期間をクリア",      "Clear period"],
  "b.pair":      ["出力＋日射量",      "Power + irradiance"],
  "b.chanone":   ["表示中の1項目だけ",  "Just this channel"],
  "b.png":       ["グラフを PNG 保存",  "Chart as PNG"],
  "b.jpg":       ["グラフを JPEG 保存", "Chart as JPEG"],
  "b.print":     ["印刷 / PDF",        "Print / PDF"],
  "b.savehtml":  ["この表示を保存",     "Save this view"],
  "b.savechart": ["このグラフだけ保存",  "Save only this chart"],
  "b.savewindow":["表示中の日・期間だけ保存", "Save the shown days only"],
  "t.daytab":    ["クリックで日を追加・解除、Shift＋クリックでその日だけ。選んだ日だけが隣どうしに並びます。",
                  "Click to add or drop a day, Shift-click for just that one. Only the chosen days are drawn, side by side."],
  "n.tip":       ["値の吹き出しは「常に表示 / Ctrl 押下中のみ / 表示しない」から選べ、透明度も変えられます（グラフの上のバー）。"
                  + "設定は保存した HTML にも引き継がれます。",
                  "The value box can be Always / Only while Ctrl / Off, and its background can be made see-through - "
                  + "the controls sit in the bar above each graph. The choice travels with a saved copy."],
  "n.noday":     ["選択した日にデータがありません。",
                  "No data on the selected days."],
  "n.days":      ["日を選ぶとその日だけが隣どうしに並びます（間の日は詰めて表示、区切り線が入ります）。"
                  + "「表示中の日・期間だけ保存」はその日だけのデータで新しい HTML を作ります。",
                  "Pick days and only those are drawn, packed side by side with a divider between them. "
                  + "\"Save the shown days only\" writes a new HTML holding just that data."],
  "b.csvtable":  ["この表を CSV 保存",  "This table as CSV"],
  "n.export":    ["「この表示を保存」は今の設定のまま開ける HTML を書き出します。PDF はブラウザの印刷から保存してください。",
                  "\"Save this view\" writes an HTML file that reopens exactly as it looks now. For PDF use the browser's print dialog."],
  "b.worst":     ["差の大きい N 号機",  "Worst N"],
  "b.zin":       ["拡大 ＋",           "Zoom in +"],
  "b.zout":      ["縮小 －",           "Zoom out -"],
  "b.zleft":     ["◀ 前へ",            "◀ Earlier"],
  "b.zright":    ["次へ ▶",            "Later ▶"],
  "b.zall":      ["表示をリセット",      "Reset view"],
  "b.yin":       ["縦 ＋",             "Y in +"],
  "b.yout":      ["縦 －",             "Y out -"],
  "b.yup":       ["▲",                "▲"],
  "b.ydown":     ["▼",                "▼"],
  "f.gheight":   ["グラフの高さ",       "Height"],
  "b.delseries": ["この系列を外す",      "Drop this series"],
  "t.gain":      ["この系列の変化だけを、その系列の平均のまわりで何倍に拡大するか。気温のようにほとんど動かない値を、"
                  + "位置を変えずに読める大きさにできます。軸は実データのままなので他の系列は縮みません。"
                  + "1 以外にすると凡例・注記・書き出した画像すべてに「×3」と表示されます（値そのものは変わりません）。",
                  "How many times to amplify just this series, around its own mean - so a channel that barely moves "
                  + "(a temperature) becomes readable without leaving its place. The axis stays on the real data, so the "
                  + "other series do not shrink. Anything other than 1 is written as \"x3\" next to the name in the "
                  + "legend, the note and the exported image; the values themselves are untouched."],
  "t.moveup":    ["描画順を後ろへ（前面に出す）", "Draw later - on top of the others"],
  "t.movedown":  ["描画順を前へ（背面に送る）",   "Draw earlier - behind the others"],
  "n.gain":      ["平均のまわりで拡大して描いている系列: {list}（値そのものは変わりません）",
                  "Amplified around its own mean: {list} (the values themselves are unchanged)"],
  "t.yzoom":     ["縦軸（値）の拡大。Alt ＋ マウスホイールでもポインタ位置を中心に拡大縮小できます。"
                  + "▲▼ で上下に移動。単位が違うグラフでも「自動範囲の何割を見るか」で指定するので、どのグラフにも同じように効きます。",
                  "Zoom the value axis. Alt + mouse wheel does the same around the pointer, and ▲▼ move up and down. "
                  + "It is stored as a fraction of each panel's own automatic range, so one setting works whatever the unit."],
  "t.gheight":   ["グラフの高さを 60〜250% で伸縮します。書き出す画像も同じ高さになります。",
                  "Stretch the graph's height between 60% and 250%. Exported images follow."],
  "f.tipmode":   ["値の吹き出し",       "Readout"],
  "o.tipalways": ["常に表示",           "Always"],
  "o.tipctrl":   ["Ctrl 押下中のみ",     "Only while Ctrl"],
  "o.tipoff":    ["表示しない",          "Off"],
  "f.tipclear":  ["透明度",             "See-through"],
  "t.tipmode":   ["値の吹き出しの出し方。「Ctrl 押下中のみ」にすると十字線だけが動き、Ctrl（⌘）を押している間だけ値が出ます。"
                  + "「表示しない」でも十字線とグラフ上の点は残ります。キーボードの ← → は常に値を表示します。",
                  "How the value box behaves. \"Only while Ctrl\" leaves just the crosshair following the mouse and shows the "
                  + "values while Ctrl (or Command) is held. \"Off\" still keeps the crosshair and the dots. The arrow keys always show it."],
  "t.tipclear":  ["吹き出しの背景をどれだけ透かすか。数字は透けても読めるようにしてあります。",
                  "How far to see through the box's background. The numbers stay readable."],
  "n.zoomspan":  ["表示範囲 {from} → {to}（全体の {pct}%）",
                  "Showing {from} → {to} ({pct}% of the range)"],
  "n.zoomdays":  ["表示中 {days}（{n} 区間・{pts} 点）",
                  "Showing {days} ({n} block(s), {pts} points)"],
  "n.zoom":      ["横（時間）はドラッグで範囲選択、Ctrl（⌘）または Shift ＋ホイール、キーボードは ＋ − と , . 。"
                  + "縦（値）は Alt ＋ホイール、または「縦 ＋／−」と ▲▼。Shift ＋ドラッグで拡大したまま上下左右に移動できます。"
                  + "ダブルクリックまたは「表示をリセット」（キーボードは 0）で既定に戻ります。"
                  + "時間の拡大は上の開始・終了と同じもので、表・CSV・保存した HTML にもそのまま反映されます。",
                  "Time: drag to select a range, Ctrl (⌘) or Shift + wheel, or + - and , . from the keyboard. "
                  + "Value: Alt + wheel, or the 縦 +/- and ▲▼ buttons. Shift + drag slides the frame in both "
                  + "directions without changing its size. A double-click or Reset view (0 from the keyboard) puts "
                  + "everything back. The time zoom is the same From/To as the boxes above, so the tables, the CSV "
                  + "and a saved copy all follow."],
  "t.zoom":      ["ドラッグで範囲選択、Shift＋ドラッグで移動、Ctrl/Shift＋ホイールで時間軸、Alt＋ホイールで縦軸、ダブルクリックでリセット",
                  "Drag to select, Shift+drag to pan, Ctrl/Shift+wheel for time, Alt+wheel for the value axis, double-click to reset"],
  "h.build":     ["追加グラフ",         "Extra graphs"],
  "b.gpng":      ["PNG",              "PNG"],
  "b.gjpg":      ["JPEG",             "JPEG"],
  "f.gcols":     ["並べ方",            "Arrangement"],
  "o.col1":      ["上下に並べる",       "One per row"],
  "o.col2":      ["横に2つずつ",        "Two per row"],
  "b.addgraph":  ["＋ グラフを追加",     "+ Add graph"],
  "b.resetgraphs":["既定に戻す",        "Back to default"],
  "f.addchan":   ["系列を追加",         "Add series"],
  "b.delgraph":  ["このグラフを削除",    "Remove this graph"],
  "b.savegraph": ["このグラフだけ HTML", "This graph as HTML"],
  "n.build":     ["「＋ グラフを追加」で必要な数だけグラフを増やせます。各グラフの「系列を追加」で項目を足し、"
                  + "PNG / JPEG / HTML はそのグラフだけを書き出します。時間軸・期間・号機の選択は上のグラフと共通です。"
                  + "各チップの ×倍率で系列ごとの高さを、◀ ▶ で描画順（右端が最前面）を変えられます。",
                  "Add as many graphs as you need, put any channel on any graph, and PNG / JPEG / HTML "
                  + "export just that one graph. The time axis, the period and the unit selection are shared with the chart "
                  + "above. On each chip, x sets how tall that series is drawn and ◀ ▶ move it in the drawing order - the "
                  + "rightmost chip is drawn on top."],
  "n.shared":    ["日射量・気温は全号機で同じ値なので1本の線で描き、どのグラフにも重ねられます。"
                  + "号機ごとに異なる項目は1グラフに1つだけです（追加すると新しいグラフになります）。",
                  "Irradiance and temperature are identical for every unit, so each is drawn as one line and can be put on any graph. "
                  + "A channel that differs per unit gets a graph of its own - adding a second one creates a new graph."],
  "n.newgraph":  ["号機ごとに異なる項目のため、新しいグラフを作成しました。",
                  "That channel differs per unit, so it went onto a new graph of its own."],
  "n.gempty":    ["系列がありません。「系列を追加」で選んでください。",
                  "No series yet - pick one with \"Add series\"."],
  "n.gpct":      ["単位が異なるため各項目の最大値に対する % で描いています（値はマウス／キーボードで確認できます）。",
                  "Units differ, so each channel is drawn as % of its own maximum - hover or use the arrow keys for real values."],
  "h.colors":    ["表の配色",         "Table colours"],
  "h.matrix":    ["時刻ごとの比較",     "Matrix"],
  "h.findings":  ["所見",             "Findings"],
  "h.diag":      ["号機別の診断",      "Per-unit diagnosis"],
  "h.scatter":   ["出力対日射量",      "Power vs irradiance"],
  "n.scatter":   ["同じ日射量でも出力が低い号機は直流側に問題があります。傾きの違いを見てください。",
                  "A unit that makes less power at the same irradiance has a DC-side problem - compare the slopes."],
  "t.attribution":["不具合の切り分け",  "Attribution"],
  "t.availability":["稼働状況",        "Availability"],
  "t.efficiency": ["変換効率",         "Efficiency"],
  "t.performance":["性能比 PR",        "Performance ratio"],
  "h.daily":     ["日別サマリー",      "Daily summary"],
  "h.zeros":     ["ゼロ値レポート",     "Zero report"],
  "h.alarms":    ["状態・警報ビット",   "Status / alarm bits"],
  "h.quality":   ["読み込み情報",      "Source files"],
  "t.worstn":    ["表示する号機の数",   "how many units"],
  "k.samples":   ["データ数",         "Samples"],
  "k.best":      ["平均が最大",        "Highest mean"],
  "k.worst":     ["平均が最小",        "Lowest mean"],
  "k.spread":    ["最大差",           "Spread"],
  "k.zeros":     ["ゼロのセル",        "Zero cells"],
  "m.time":      ["時刻",            "Time"],
  "m.spread":    ["差",              "spread"],
  "m.maxdev":    ["最大偏差",         "max |dev|"],
  "m.allday":    ["全日",            "All days"],
  "n.rows":      ["{shown} / {kept} 行を表示（範囲内 {range} 行）",
                  "{shown} / {kept} matching rows shown ({range} in range)"],
  "n.thin":      ["レポート用に 1:{step} に間引き",
                  "data thinned 1:{step} for the report"],
  "n.src":       ["元データ {rows} 行",  "{rows} source rows"],
  "n.plotted":   ["、1:{step} で描画",   ", plotted 1:{step}"],
  "n.inview":    ["表示中 {n} / {total} 点", "{n} of {total} points in view"],
  "n.hidden":    ["{n} 号機を非表示（チップまたは「すべて」で表示）",
                  "{n} unit(s) hidden — click a chip or \"All\" to show them"],
  "n.devfloor":  ["基準値がほぼ 0 の時刻は % を空欄にしています",
                  "% is blank where the fleet reference is near zero"],
  "n.nodev":     ["この項目に偏差データはありません。",
                  "No deviation data for this metric. "],
  "n.extra":     ["追加項目：点数が少なく、偏差表はありません（アプリで未選択）",
                  "extra channel: fewer points, no deviation frame (it was not selected in the app)"],
  "n.devtitle":  ["平均（{mode}）との差",  "deviation from fleet {mode}"],
  "n.apprule":   ["アプリのルール：{label}・{mode}",  "App rule: {label} · {mode}"],
  "n.norule":    ["アプリで配色ルールが無効のため既定の配色です",
                  "No colour-scale rule was enabled in the app - showing the default ramp"],
  "n.fixedat":   ["数値指定 {stops}",   "fixed steps {stops}"],
  "n.autoscale": ["表示範囲の最小値〜最大値で配色", "scaled between the smallest and largest value in view"],
  "n.barson":    ["データバー有効",      "data bars on"],
  "n.devcolor":  ["偏差表示では負に低い色、正に高い色を使います",
                  "deviation view always uses the low colour for negative and the high colour for positive."],
  "n.fromapp":   ["アプリ設定",         "from app settings"],
  "n.appdef":    ["アプリ既定",         "app default"],
  "n.nometric":  ["比較する項目が選択されていません", "No cross-unit metric selected"],
  "n.attribution":["直流側が低い＝アレイ・ストリング側の問題、変換効率が低い＝インバータの問題です。",
                  "DC side low = array / strings / shading / soiling; efficiency low = the inverter itself."],
  "n.truncated": ["先頭 {n} 行のみ表示（全 {total} 行）",
                  "first {n} of {total} rows shown"],
};
/* headers of the summary tables come from the analysis code in English */
const COLS = {
  "Entity":"号機", "Date":"日付", "Metric":"項目", "Unit":"単位", "Samples":"データ数",
  "Expected":"期待データ数", "Coverage %":"取得率 %", "Mean":"平均", "Max":"最大",
  "Min":"最小", "Total":"合計", "Total note":"合計の算出方法", "Zero samples":"ゼロ件数",
  "Zero %":"ゼロ率 %", "Missing":"欠測", "Negative":"負の値",
  "Column":"列", "Longest zero run":"連続ゼロ最長", "Raw column":"元の列名",
  "File":"ファイル", "Rows":"行数", "Columns":"列数", "Numeric columns":"数値列",
  "Text columns":"文字列列", "Time column":"時刻列", "Interval":"間隔",
  "Time gaps":"時刻の欠落", "Encoding":"文字コード", "Delimiter":"区切り文字",
  "Header row":"見出し行", "Notes":"備考",
  "Bit":"ビット", "Name":"名称", "Minutes on":"ON 時間（分）", "Share %":"割合 %",
  "Events":"回数", "First on":"最初の ON", "Last on":"最後の ON",
  "sum of differences":"差分の合計", "value x interval":"値 × 間隔",
  "AC kWh":"交流電力量 kWh", "DC kWh":"直流電力量 kWh",
  "AC vs fleet %":"交流 全体比 %", "DC side vs fleet %":"直流側 全体比 %",
  "AC per irradiance vs fleet %":"交流／日射 全体比 %",
  "Efficiency %":"変換効率 %", "Efficiency vs fleet pt":"変換効率 全体比 pt",
  "DC per irradiance":"直流／日射", "AC per irradiance":"交流／日射",
  "Samples used":"使用サンプル数", "Daylight samples":"日中サンプル数",
  "Verdict":"判定", "Usable-light samples":"日射のあるサンプル数",
  "Zero output":"出力ゼロ", "Missing data":"欠測", "Availability %":"稼働率 %",
  "Lost kWh vs fleet":"損失 kWh（全体比）", "Capacity kW":"定格 kW", "PR":"性能比 PR",
};
/* Sentences ("n.*") are never shown bilingually - a note reading
   "App rule: ... / アプリのルール：..." with bilingual values substituted into it
   is unreadable.  In "both" mode they fall back to English. */
const noteLang = () => (state.lang === "ja" ? "ja" : "en");
function tx(key, vars, lang){
  const e = STR[key];
  let use = lang || state.lang;
  if(!lang && use === "both" && key.startsWith("n.")) use = "en";
  let out;
  if(!e) out = key;
  else if(use === "ja") out = e[0];
  else if(use === "en") out = e[1];
  else out = (e[1] === e[0]) ? e[0] : `${e[1]} / ${e[0]}`;
  if(vars) for(const k in vars) out = out.split("{"+k+"}").join(vars[k]);
  return out;
}
const tcol = c => (state.lang === "ja" && COLS[c]) ? COLS[c] : String(c);
/* The app writes bilingual rule labels ("Colour scale / カラースケール").
   Show only the half that matches the chosen language. */
const HAS_CJK = /[\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]/;
function half(label, lang){
  const s = String(label||""); const i = s.indexOf(" / ");
  const use = lang || state.lang;
  if(i < 0 || use === "both") return s;
  const a = s.slice(0, i), b = s.slice(i + 3);
  const ja = HAS_CJK.test(b) && !HAS_CJK.test(a) ? b
           : HAS_CJK.test(a) && !HAS_CJK.test(b) ? a : b;
  const en = (ja === b) ? a : b;
  return use === "ja" ? ja : en;
}
const MODE_KEY = {off:"o.off", "2color":"o.c2", "3color":"o.c3", "5color":"o.c5"};
const modeName = m => MODE_KEY[m] ? tx(MODE_KEY[m], null, noteLang()) : String(m);
function applyLang(){
  document.documentElement.lang = (state.lang === "en") ? "en" : "ja";
  document.querySelectorAll("[data-t]").forEach(e => { e.textContent = tx(e.dataset.t); });
  document.querySelectorAll("[data-tp]").forEach(e => { e.placeholder = tx(e.dataset.tp); });
  document.querySelectorAll("[data-tt]").forEach(e => { e.title = tx(e.dataset.tt); });
  el("lang").value = state.lang;
}
const SERIES_VARS = ["--s1","--s2","--s3","--s4","--s5","--s6","--s7","--s8"];
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const el = id => document.getElementById(id);
const fmt = (v,d) => {
  if(v===null||v===undefined||Number.isNaN(v)) return "";
  const a=Math.abs(v);
  const dec = d!==undefined ? d : (a>=1000?1 : a>=100?1 : a>=10?2 : 3);
  return a>=1000 ? v.toLocaleString(undefined,{maximumFractionDigits:dec})
                 : Number(v.toFixed(dec)).toString();
};
/* 1-2-5 "nice" axis steps so tick labels stay readable */
function niceTicks(lo,hi,count){
  const span=hi-lo; if(!(span>0)) return [lo];
  const raw=span/count, mag=Math.pow(10,Math.floor(Math.log10(raw)));
  const norm=raw/mag;
  const step=(norm<=1?1:norm<=2?2:norm<=5?5:10)*mag;
  const start=Math.ceil(lo/step)*step, out=[];
  for(let v=start; v<=hi+step*1e-9; v+=step) out.push(Math.abs(v)<step*1e-9?0:v);
  return out;
}

/* ---------- colour engine ------------------------------------------------- #
   One source of truth: DATA.style comes straight from the colour-scale rule
   configured in the desktop app, so Excel, the PNGs and this page agree.
   The reader may then override it here without re-running the job.          */
const STYLE = Object.assign({mode:"2color", colors:["#FCFCFB","#184F95"], bounds:"auto",
  stops:[], reverse:false, zero_color:"", bars:false, bar_color:"#4a90d9",
  from_rule:false, controls:true, label:""}, (DATA && DATA.style) || {});

/* Sections the app left out are absent from the payload, so their cards go
   away entirely - the controls that only drive them go with them. */
const SECTIONS = new Set(DATA.sections || ["chart","matrix","daily","zeros","alarms","quality"]);
function applySections(){
  const cards = {chart:"chartCard", matrix:"matrixCard", daily:"dailyCard",
                 zeros:"zeroCard", alarms:"alarmCard", quality:"qualityCard"};
  for(const key in cards){
    const el2 = el(cards[key]);
    if(el2 && !SECTIONS.has(key)) el2.classList.add("hidden");
  }
  if(!SECTIONS.has("chart") && !SECTIONS.has("matrix")){
    el("colorCard").classList.add("hidden");
    document.querySelector(".controls").classList.add("hidden");
  } else if(!SECTIONS.has("matrix")){
    el("colorCard").classList.add("hidden");     /* it only paints the matrix */
  }
  /* a file written by "this graph as HTML" holds one graph and nothing else */
  if(DATA.graph_only){
    el("chartCard").classList.add("hidden");
    el("buildCard").classList.remove("hidden");
    el("buildBar").classList.add("hidden");   /* the zoom bar below it stays */
  }
}
function hex2rgb(h){
  h=String(h||"").replace("#","");
  if(h.length===3) h=h[0]+h[0]+h[1]+h[1]+h[2]+h[2];
  if(h.length===8) h=h.slice(2);
  const n=parseInt(h,16);
  return isFinite(n)? [n>>16&255, n>>8&255, n&255] : [255,255,255];
}
const rgb2hex = a => "#"+a.map(v=>Math.max(0,Math.min(255,v|0)).toString(16).padStart(2,"0")).join("");
function ramp(cols,t){
  t=Math.max(0,Math.min(1,t));
  if(!cols.length) return [255,255,255];
  if(cols.length===1) return hex2rgb(cols[0]);
  const p=t*(cols.length-1), i=Math.min(cols.length-2,Math.floor(p)), f=p-i;
  const A=hex2rgb(cols[i]), B=hex2rgb(cols[i+1]);
  return [0,1,2].map(k=>Math.round(A[k]+(B[k]-A[k])*f));
}
/* resample the configured palette to n colours, so switching 2/3/5 keeps
   the look the app defined instead of jumping to some other scheme */
function resample(cols,n){
  if(n<=1) return [cols[cols.length-1]||"#184F95"];
  const out=[]; for(let i=0;i<n;i++) out.push(rgb2hex(ramp(cols,i/(n-1))));
  return out;
}
function towardSurface(rgb,strength){
  const bg=hex2rgb((css("--surface")||"#ffffff"));
  return [0,1,2].map(k=>Math.round(bg[k]+(rgb[k]-bg[k])*strength));
}
/* Text colour on a coloured cell.
   The quick 0.299/0.587/0.114 brightness average puts WHITE text on mid-tone
   reds and greens (#F8696B, #63BE7B) where black actually reads far better -
   6.5:1 and 8.3:1 against 2.9:1 and 2.3:1 - and where Excel prints black, so
   the two outputs disagreed.  Measure real WCAG contrast instead and take the
   better of the two. */
function relLum(rgb){
  const f = v => { v /= 255; return v <= 0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); };
  return 0.2126*f(rgb[0]) + 0.7152*f(rgb[1]) + 0.0722*f(rgb[2]);
}
const INK_DARK_LUM = 0.00561;          /* #111111 */
function inkOn(rgb){
  const l = relLum(rgb);
  const vsDark  = (l + 0.05) / (INK_DARK_LUM + 0.05);
  const vsLight = 1.05 / (l + 0.05);
  return vsDark >= vsLight ? "#111111" : "#ffffff";
}
const N_OF = {off:0, "2color":2, "3color":3, "5color":5};

const state = {metric:0, view:"value", off:new Set(), rows:300, from:null, to:null,
               minspread:0, hidezero:false, scope:"chosen", mfilter:"", ufilter:"",
               days:new Set(), lang:(DATA.lang||"both"), diag:null,
               layout:"single", channels:new Set(),
               graphs:null, gcols:1, focus:"main", gnote:"", tipFrac:null,
               pinned:false, gpinned:false,
               tipMode:"always", tipClear:0,
               yzoom:1, ycenter:0.5, gheight:1, gain:{},
               cursor:null, cursorMs:null, gcursorMs:null,
               worstn:(DATA.worst_n||6),
               cmode:STYLE.mode, colors:{}, bounds:STYLE.bounds==="numbers"?"numbers":"auto",
               stops:(STYLE.stops||[]).slice(), strength:100,
               zeroOn:!!DATA.zero_highlight, zeroColor:STYLE.zero_color||"#E6E6E3",
               bars:!!STYLE.bars, barColor:STYLE.bar_color||"#4a90d9"};
/* one palette per mode, seeded from the app's own colours */
["2color","3color","5color"].forEach(m=>{
  state.colors[m] = (STYLE.mode===m && STYLE.colors.length===N_OF[m])
    ? STYLE.colors.slice() : resample(STYLE.colors, N_OF[m]);
});
if(STYLE.reverse) ["2color","3color","5color"].forEach(m=>state.colors[m].reverse());
const activeColors = () => state.colors[state.cmode] || state.colors["2color"];

/* A file produced by "Save this view" carries the reader's settings in the
   head; adopt them before anything is drawn. */
let RESTORED = false;
if(window.__STATE__){
  try{
    const sv = window.__STATE__;
    Object.assign(state, sv);
    state.off = new Set(sv.off || []);
    state.channels = new Set(sv.channels || []);
    /* 2.3.x saved one day as a string; a set of days replaced it */
    state.days = new Set(sv.days || (sv.day ? [sv.day] : []));
    RESTORED = true;
  }catch(err){ /* a corrupted block must not stop the report from opening */ }
}

/* ---------- init ---------- */
el("title").textContent = DATA.title || "DataScope report";
el("subtitle").textContent = DATA.subtitle || "";
el("meta").textContent = `${DATA.app} · generated ${DATA.generated}`;
document.title = DATA.title || "DataScope report";

/* the channel dropdown honours the app's selection, but every channel that
   travelled with the report can be brought back with "All channels" */
function buildMetricList(){
  const sel=el("metric"); sel.innerHTML="";
  const q=state.mfilter.toLowerCase();
  let n=0;
  DATA.metrics.forEach((m,i)=>{
    if(state.scope==="chosen" && m.chosen===false) return;
    const label=m.title + (m.unit? ` [${m.unit}]`:"") + (m.chosen===false? "  ·":"");
    if(q && !(label.toLowerCase().includes(q) || String(m.key).toLowerCase().includes(q))) return;
    const o=document.createElement("option"); o.value=i; o.textContent=label;
    sel.appendChild(o); n++;
  });
  if(!n && DATA.metrics.length){          /* filter matched nothing - show all */
    DATA.metrics.forEach((m,i)=>{
      const o=document.createElement("option"); o.value=i;
      o.textContent=m.title + (m.unit? ` [${m.unit}]`:""); sel.appendChild(o);
    });
  }
  const have=[...sel.options].map(o=>+o.value);
  if(!have.includes(state.metric) && have.length){
    state.metric = have[0];
    if(!RESTORED) autoSelect();
  }
  sel.value=state.metric;
}
if(!DATA.has_extra) el("scopeField").classList.add("hidden");
if(!DATA.metrics.length){ el("chartTitle").textContent=tx("n.nometric"); }

/* mean deviation per unit, used to pick "the interesting ones" */
function unitStats(m){
  const f = m.deviation || m.value;
  const out = {};
  m.entities.forEach(e=>{
    const s = f.series[e] || []; let n=0, sum=0;
    for(let i=0;i<s.length;i++){ const v=s[i]; if(v===null) continue; n++; sum+=v; }
    out[e] = n ? sum/n : 0;
  });
  return out;
}
/* Show the N units that deviate most.  N is the reader's choice: with 20 PCS
   the lines overlap, but which few matter depends on what you are chasing. */
function autoSelect(force){
  const m = DATA.metrics[state.metric]; if(!m) return;
  const n = Math.max(1, state.worstn|0);
  state.off.clear();
  if(m.entities.length <= n) return;
  if(!force && m.entities.length <= 8 && n >= 6) return;
  const stats = unitStats(m);
  const ranked = m.entities.slice().sort((a,b)=>Math.abs(stats[b])-Math.abs(stats[a]));
  ranked.slice(n).forEach(e=>state.off.add(e));
}
function syncWorstLabel(){
  el("worstn").value = state.worstn;
  el("worst").textContent = tx("b.worst").split("N").join(state.worstn);
}
function frame(){
  const m = DATA.metrics[state.metric]; if(!m) return null;
  const f = state.view==="deviation" ? (m.deviation||m.value) : m.value;
  return {m:m, f:f};
}
const DASHES = ["", "7 3", "2 3", "10 3 2 3"];
function entityIndex(name){
  const m = DATA.metrics[state.metric];
  let i = m ? m.entities.indexOf(name) : -1;
  if(i < 0){
    /* The channel on screen need not list this unit - a plant-wide channel has
       exactly one column, and a pruned export keeps only what it draws.  Fall
       back to the first channel that does know the unit, or every unit would
       collapse onto the first hue. */
    for(let k = 0; k < DATA.metrics.length; k++){
      const j = DATA.metrics[k].entities.indexOf(name);
      if(j >= 0){ i = j; break; }
    }
  }
  return i < 0 ? 0 : i;
}
function entityColor(name){
  return css(SERIES_VARS[entityIndex(name) % SERIES_VARS.length]);
}
/* past 8 units the hue repeats, so the line style carries the rest of the
   identity - it belongs to the unit, not to its rank, so filtering never
   repaints anything. */
function entityDash(name){
  return DASHES[Math.floor(entityIndex(name)/SERIES_VARS.length) % DASHES.length];
}
function chipMark(name){
  const d = entityDash(name), c = entityColor(name);
  if(!d) return `<span class="dot" style="background:${c}"></span>`;
  return `<svg width="16" height="9" style="flex:none"><line x1="0" y1="4.5" x2="16" y2="4.5"
    stroke="${c}" stroke-width="2.5" stroke-dasharray="${d}"/></svg>`;
}
function listedEntities(){
  const m = DATA.metrics[state.metric]; if(!m) return [];
  const q=state.ufilter.toLowerCase();
  const hit = m.entities.filter(e=>!q || String(e).toLowerCase().includes(q));
  return hit.length ? hit : m.entities;
}
function visibleEntities(){
  return listedEntities().filter(e=>!state.off.has(e));
}
/* ---------- days ----------
   A report can hold many daily files.  The timestamps are sorted, so one pass
   gives the day list and a day is a contiguous slice - no re-filtering. */
const dayOf = t => String(t||"").slice(0,10);
/* An empty set means every day.  Otherwise only the days the reader ticked -
   and they are drawn side by side, without the days in between. */
const dayOk = t => !state.days.size || state.days.has(dayOf(t));
const daysOn = () => [...state.days].sort();
function dayList(fr){
  const out=[], seen=new Set();
  for(let i=0;i<fr.time.length;i++){
    const d=dayOf(fr.time[i]);
    if(d && !seen.has(d)){ seen.add(d); out.push(d); }
  }
  return out;
}
function dayCount(fr,day){
  let n=0; for(let i=0;i<fr.time.length;i++) if(dayOf(fr.time[i])===day) n++;
  return n;
}
/* The OUTER bounds of what is on screen.  With days switched off the range is
   no longer contiguous, so every consumer also tests dayOk() per sample and the
   x axis is built from the segments that survive (see axisModel). */
function timeIdx(fr){
  let a=0,b=fr.time.length-1;
  if(state.days.size){
    while(a<=b && !dayOk(fr.time[a])) a++;
    while(b>=a && !dayOk(fr.time[b])) b--;
  }
  if(state.from){ while(a<=b && fr.time[a] < state.from) a++; }
  if(state.to){ while(b>=a && fr.time[b] > state.to) b--; }
  return [a,b];
}
/* Point the calendar at the data: the picker opens on a day that exists and
   cannot be dragged outside the loaded range. */
function syncRange(){
  const fr = frame(); if(!fr) return;
  const time = fr.f.time;
  if(!time.length) return;
  const on = daysOn();
  const lo = on.length ? on[0] + " 00:00:00" : time[0];
  const hi = on.length ? on[on.length-1] + " 23:59:59" : time[time.length-1];
  const val = t => String(t||"").slice(0,16).replace(" ", "T");
  ["from","to"].forEach(id=>{
    const e = el(id);
    e.min = val(lo); e.max = val(hi);
  });
}
/* ---------- the x axis, with the unselected days taken out ----------------
   Pick 22, 25 and 30 and you want those three days next to each other, not two
   weeks of blank paper with three thin stripes in it.  So the axis is a list of
   SEGMENTS - one per run of days that really is continuous - each given a share
   of the width in proportion to its own length, with a divider between them.

   Runs that are actually adjacent in time are merged, so "all days" and "one
   day" both come out as a single segment and behave exactly as before.       */
function axisModel(){
  const fr = frame(); if(!fr) return null;
  const t = fr.f.time;
  const [a, b] = timeIdx(fr.f);
  if(b < a) return null;
  const step = sampleMs();
  const runs = [];
  let cur = null;
  for(let i = a; i <= b; i++){
    const ts = t[i];
    if(!dayOk(ts)){ cur = null; continue; }
    const ms = epoch(ts);
    if(ms === null){ cur = null; continue; }
    if(cur && ms - cur.t1 <= step * 1.5){ cur.t1 = ms; cur.days.add(dayOf(ts)); }
    else { cur = {t0: ms, t1: ms, days: new Set([dayOf(ts)])}; runs.push(cur); }
  }
  if(!runs.length) return null;
  const total = runs.reduce((n, r) => n + Math.max(step, r.t1 - r.t0), 0);
  let acc = 0;
  runs.forEach(r => {
    const w = Math.max(step, r.t1 - r.t0) / total;
    r.w0 = acc; acc += w; r.w1 = acc;
    r.label = [...r.days].sort().map(d => d.slice(5)).join(" / ");
  });
  runs[runs.length - 1].w1 = 1;
  return {
    segs: runs,
    step: step,
    t0: runs[0].t0,
    t1: runs[runs.length - 1].t1,
    split: runs.length > 1,
    /* a millisecond in a skipped stretch collapses onto the next segment */
    frac: ms => {
      for(let i = 0; i < runs.length; i++){
        const r = runs[i];
        if(ms <= r.t0) return r.w0;
        if(ms <= r.t1) return r.w0 + (ms - r.t0) / Math.max(1, r.t1 - r.t0) * (r.w1 - r.w0);
      }
      return 1;
    },
    ms: f => {
      f = Math.max(0, Math.min(1, f));
      for(const r of runs){
        if(f <= r.w1) return r.t0 + (f - r.w0) / Math.max(1e-9, r.w1 - r.w0) * (r.t1 - r.t0);
      }
      return runs[runs.length - 1].t1;
    },
    /* pull a cursor out of a skipped stretch, in the direction it was moving */
    snap: (ms, dir) => {
      for(let i = 0; i < runs.length; i++){
        const r = runs[i];
        if(ms >= r.t0 && ms <= r.t1) return ms;
        if(ms < r.t0) return (dir >= 0 || i === 0) ? r.t0 : runs[i-1].t1;
      }
      return runs[runs.length - 1].t1;
    },
    /* Which segment a moment belongs to, and therefore whether a line between
       two points crosses a skipped stretch.  This has to be answered from the
       segments, not from a step comparison: every channel carries its own
       thinning, and a 4-minute extra channel measured against the 2-minute
       main channel would "break" between every single pair of points. */
    seg: ms => {
      for(let i = 0; i < runs.length; i++) if(ms <= runs[i].t1) return i;
      return runs.length - 1;
    },
    broken: function(msA, msB){ return this.seg(msA) !== this.seg(msB); }
  };
}
function dayChipRow(box){
  box.innerHTML = "";
  const fr = frame();
  if(!fr){ box.style.display = "none"; return; }
  const days = dayList(fr.f);
  if(days.length < 2){ box.style.display = "none"; state.days.clear(); return; }
  box.style.display = "";
  /* a day that the current channel does not have cannot stay selected */
  [...state.days].forEach(d => { if(!days.includes(d)) state.days.delete(d); });
  const all = document.createElement("button");
  all.className = "daytab" + (state.days.size ? "" : " on");
  all.innerHTML = tx("m.allday") + `<span class="n">${fr.f.time.length}</span>`;
  all.onclick = () => { state.days.clear(); dayChanged(); };
  box.appendChild(all);
  days.forEach(d => {
    const btn = document.createElement("button");
    btn.className = "daytab" + (state.days.has(d) ? " on" : "");
    btn.innerHTML = d.slice(5) + `<span class="n">${dayCount(fr.f, d)}</span>`;
    btn.title = tx("t.daytab");
    btn.onclick = ev => {
      if(ev.shiftKey || state.days.size === 0) {
        /* Shift, or the first pick from "all days", starts a fresh selection */
        state.days.clear(); state.days.add(d);
      } else if(state.days.has(d)) {
        state.days.delete(d);
      } else {
        state.days.add(d);
      }
      dayChanged();
    };
    box.appendChild(btn);
  });
}
/* A From/To left over from another day would filter the new selection down to
   nothing, so any zoom outside the chosen days is dropped. */
function dayChanged(){
  state.rows = 300;
  if(state.days.size){
    if(state.from && !state.days.has(state.from.slice(0,10))){ state.from = null; el("from").value = ""; }
    if(state.to   && !state.days.has(state.to.slice(0,10))){   state.to   = null; el("to").value = ""; }
  }
  render();
}
function drawDayTabs(){
  ["daytabs", "cdaytabs", "gdaytabs"].forEach(id => {
    const box = el(id); if(box) dayChipRow(box);
  });
}

/* ---------- controls ---------- */
function buildEntities(){
  const m=DATA.metrics[state.metric]; const box=el("entities"); box.innerHTML="";
  if(!m) return;
  listedEntities().forEach(e=>{
    const c=document.createElement("div"); c.className="chip"+(state.off.has(e)?" off":"");
    c.innerHTML=`${chipMark(e)}${e}`;
    c.onclick=()=>{ state.off.has(e)?state.off.delete(e):state.off.add(e); render(); };
    box.appendChild(c);
  });
}
el("metric").onchange = e => {
  state.metric = +e.target.value;
  if(state.layout !== "single" && !state.channels.size) state.channels.add(state.metric);
  autoSelect(); render();
};
el("lang").onchange = e => {
  state.lang = e.target.value;
  applyLang(); syncWorstLabel();
  if(STYLE.controls) syncColorInputs();
  drawTables(); buildMetricList(); render();
};
/* mirror the restored state into the controls, or the page would look right
   while the boxes above it showed defaults */
function syncControls(){
  el("layout").value = state.layout;
  el("view").value = state.view;
  el("scope").value = state.scope;
  el("mfilter").value = state.mfilter || "";
  el("ufilter").value = state.ufilter || "";
  el("minspread").value = state.minspread || "";
  el("hidezero").checked = !!state.hidezero;
  el("gcols").value = String(state.gcols || 1);
  el("tipmode").value = state.tipMode || "always";
  el("tipclear").value = Math.round((+state.tipClear || 0) * 100);
  el("gheight").value = Math.round(gh() * 100);
  if(state.from) el("from").value = String(state.from).slice(0,16).replace(" ","T");
  if(state.to)   el("to").value   = String(state.to).slice(0,16).replace(" ","T");
}
/* "Power + irradiance" is the pairing every performance question starts from,
   so it gets a button rather than two chip clicks.  The channel keys come from
   the role detection, not from hard-coded names. */
function metricIndexFor(key){
  if(!key) return -1;
  return DATA.metrics.findIndex(m => m.key === key);
}
el("pairBtn").onclick = () => {
  const roles = DATA.roles || {};
  const want = [metricIndexFor(roles.ac_power), metricIndexFor(roles.irradiance)]
                 .filter(i => i >= 0);
  if(!want.length) return;
  state.channels = new Set(want);
  if(state.layout === "single") state.layout = "stack";
  el("layout").value = state.layout;
  render();
};
el("chanNone").onclick = () => {
  state.channels = new Set([state.metric]);
  render();
};
el("layout").onchange = e => {
  state.layout = e.target.value;
  /* seed with the channel already on screen, so the first chip click adds a
     second channel instead of silently replacing the first */
  if(state.layout !== "single" && !state.channels.size) state.channels.add(state.metric);
  render();
};
el("scope").onchange = e => { state.scope=e.target.value; buildMetricList(); render(); };
el("mfilter").oninput = e => { state.mfilter=e.target.value.trim(); buildMetricList(); render(); };
el("ufilter").oninput = e => { state.ufilter=e.target.value.trim(); render(); };
el("worst").onclick = () => { autoSelect(true); render(); };
el("worstn").onchange = e => {
  state.worstn = Math.max(1, parseInt(e.target.value,10) || 6);
  syncWorstLabel(); autoSelect(true); render(); };
el("all").onclick = () => { state.off.clear(); render(); };
el("none").onclick = () => { listedEntities().forEach(e=>state.off.add(e)); render(); };
el("invert").onclick = () => {
  listedEntities().forEach(e=>{ state.off.has(e)?state.off.delete(e):state.off.add(e); });
  render(); };
el("view").onchange = e => { state.view=e.target.value; render(); };
/* <input type="datetime-local"> hands back "2026-08-06T11:00"; the data carries
   "2026-08-06 11:00:00".  Padding the seconds keeps the plain string compare
   working AND keeps the end minute inside the range. */
const pickFrom = v => v ? v.replace("T", " ") + ":00" : null;
const pickTo   = v => v ? v.replace("T", " ") + ":59" : null;
el("from").onchange = e => { state.from = pickFrom(e.target.value.trim()); render(); };
el("to").onchange = e => { state.to = pickTo(e.target.value.trim()); render(); };
el("clearrange").onclick = () => {
  state.from = state.to = null;
  el("from").value = ""; el("to").value = "";
  render();
};
el("minspread").onchange = e => { state.minspread = parseFloat(e.target.value)||0; render(); };
el("hidezero").onchange = e => { state.hidezero = e.target.checked; render(); };
el("reset").onclick = () => { state.worstn=(DATA.worst_n||6); syncWorstLabel();
  autoSelect(true); state.from=state.to=null; state.rows=300; state.days.clear();
  state.minspread=0; state.hidezero=false; state.ufilter=""; state.mfilter="";
  el("from").value=""; el("to").value=""; el("minspread").value=""; el("hidezero").checked=false;
  el("ufilter").value=""; el("mfilter").value="";
  buildMetricList(); render(); };

/* ---------- colour controls ---------- */
function buildSwatches(){
  const box=el("swatches"); box.innerHTML="";
  if(state.cmode==="off"){ box.innerHTML='<span class="small">—</span>'; return; }
  activeColors().forEach((c,i)=>{
    const inp=document.createElement("input");
    inp.type="color"; inp.value=c;
    inp.title=`Step ${i+1}`;
    inp.oninput=ev=>{ state.colors[state.cmode][i]=ev.target.value; drawScaleBar(); drawMatrix(); };
    box.appendChild(inp);
  });
}
function drawScaleBar(){
  const bar=el("scaleBar"); bar.innerHTML="";
  if(state.cmode==="off"){ bar.style.display="none"; return; }
  bar.style.display="";
  const cols=activeColors();
  const banded = state.bounds==="numbers" && state.stops.length>1;
  const n = banded ? cols.length : 24;
  for(let i=0;i<n;i++){
    const rgb = banded ? hex2rgb(cols[Math.min(cols.length-1,i)])
                       : ramp(cols, n===1?0:i/(n-1));
    const shown=towardSurface(rgb, Math.max(0.1,Math.min(1,state.strength/100)));
    const d=document.createElement("div");
    d.style.background=rgb2hex(shown); d.style.color=inkOn(shown);
    if(banded) d.textContent = fmt(state.stops[Math.min(state.stops.length-1,i)]);
    bar.appendChild(d);
  }
  el("colorNote").textContent =
    (STYLE.from_rule
      ? tx("n.apprule", {label:(half(STYLE.label, noteLang())
                                || tx("h.colors", null, noteLang())),
                         mode:modeName(STYLE.mode)})
      : tx("n.norule"))
    + " · " + (banded ? tx("n.fixedat", {stops:state.stops.map(v=>fmt(v)).join(", ")})
                      : tx("n.autoscale"))
    + (state.bars ? " · " + tx("n.barson") : "")
    + " · " + tx("n.devcolor");
}
function syncColorInputs(){
  el("cmode").value=state.cmode;
  el("cbounds").value=state.bounds;
  el("cstops").value=state.stops.map(v=>fmt(v)).join(", ");
  el("cstrength").value=state.strength;
  el("czero").value=/^#[0-9a-f]{6}$/i.test(state.zeroColor)?state.zeroColor:"#e6e6e3";
  el("czeroOn").checked=state.zeroOn;
  el("cbar").value=/^#[0-9a-f]{6}$/i.test(state.barColor)?state.barColor:"#4a90d9";
  el("cbarOn").checked=state.bars;
  el("styleSrc").textContent = STYLE.from_rule ? tx("n.fromapp") : tx("n.appdef");
  buildSwatches(); drawScaleBar();
}
if(STYLE.controls){
  el("colorCard").classList.remove("hidden");
  el("cmode").onchange = e => { state.cmode=e.target.value; syncColorInputs(); drawMatrix(); };
  el("cbounds").onchange = e => {
    state.bounds=e.target.value;
    if(state.bounds==="numbers" && state.stops.length<2){
      const fr=frame();
      if(fr){                       /* seed sensible steps from what is in view */
        const ents=visibleEntities(); const [a,b]=timeIdx(fr.f);
        let lo=Infinity,hi=-Infinity;
        ents.forEach(en=>{const s=fr.f.series[en];
          for(let i=a;i<=b;i++){const v=s[i]; if(v===null)continue;
            if(v<lo)lo=v; if(v>hi)hi=v;}});
        if(isFinite(lo)&&hi>lo){
          const n=activeColors().length, out=[];
          for(let i=0;i<n;i++) out.push(+(lo+(hi-lo)*i/(n-1)).toFixed(3));
          state.stops=out;
        }
      }
    }
    syncColorInputs(); drawMatrix(); };
  el("cstops").onchange = e => {
    state.stops = e.target.value.split(/[,;\s]+/).map(parseFloat)
      .filter(v=>isFinite(v)).sort((a,b)=>a-b);
    if(state.stops.length>1) state.bounds="numbers";
    syncColorInputs(); drawMatrix(); };
  el("cstrength").onchange = e => {
    state.strength=Math.max(10,Math.min(100,parseFloat(e.target.value)||100));
    syncColorInputs(); drawMatrix(); };
  el("czero").oninput = e => { state.zeroColor=e.target.value; state.zeroOn=true;
    el("czeroOn").checked=true; drawMatrix(); };
  el("czeroOn").onchange = e => { state.zeroOn=e.target.checked; drawMatrix(); };
  el("cbar").oninput = e => { state.barColor=e.target.value; state.bars=true;
    el("cbarOn").checked=true; drawScaleBar(); drawMatrix(); };
  el("cbarOn").onchange = e => { state.bars=e.target.checked; drawScaleBar(); drawMatrix(); };
  el("crev").onclick = () => { state.colors[state.cmode].reverse(); syncColorInputs(); drawMatrix(); };
  el("cfromapp").onclick = () => {
    state.cmode=STYLE.mode; state.bounds=STYLE.bounds==="numbers"?"numbers":"auto";
    state.stops=(STYLE.stops||[]).slice(); state.strength=100;
    state.zeroOn=!!DATA.zero_highlight; state.zeroColor=STYLE.zero_color||"#E6E6E3";
    state.bars=!!STYLE.bars; state.barColor=STYLE.bar_color||"#4a90d9";
    ["2color","3color","5color"].forEach(m=>{
      state.colors[m] = (STYLE.mode===m && STYLE.colors.length===N_OF[m])
        ? STYLE.colors.slice() : resample(STYLE.colors, N_OF[m]);
      if(STYLE.reverse) state.colors[m].reverse();
    });
    syncColorInputs(); drawMatrix(); };
}
el("more").onclick = () => { state.rows += 500; render(); };
el("theme").onclick = () => {
  const cur=document.documentElement.getAttribute("data-theme");
  const dark = cur ? cur==="dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.setAttribute("data-theme", dark?"light":"dark"); render();
};
el("csv").onclick = () => {
  const fr=frame(); if(!fr) return;
  const ents=visibleEntities(); const [a,b]=timeIdx(fr.f);
  let out=["time,"+ents.join(",")];
  for(let i=a;i<=b;i++){
    out.push([fr.f.time[i]].concat(ents.map(e=>{
      const v=fr.f.series[e][i]; return v===null?"":v; })).join(","));
  }
  const blob=new Blob([out.join("\n")],{type:"text/csv;charset=utf-8"});
  const a2=document.createElement("a"); a2.href=URL.createObjectURL(blob);
  a2.download=safeName(`${fr.m.title}_${state.view}`)
    + (state.days.size ? "_" + daysOn().join("_") : "") + ".csv"; a2.click();
};

/* ---------- chart ---------- */
/* ---------- multi-channel views -------------------------------------------
   Channels are sampled differently (an extra channel carries fewer points), so
   these views map x by TIMESTAMP, not by array index - otherwise two channels
   with different thinning would be drawn out of step with each other.        */
const epoch = t => { const s2 = String(t || "").replace(" ", "T");
                     const v = Date.parse(s2); return isFinite(v) ? v : null; };
function chanList(){
  return DATA.metrics.map((m, i) => i).filter(i => {
    const m = DATA.metrics[i];
    return state.scope === "all" || m.chosen !== false;
  });
}
function selectedChannels(){
  const avail = chanList();
  let out = [...state.channels].filter(i => avail.includes(i));
  if(!out.length) out = [state.metric].filter(i => avail.includes(i));
  return out.sort((a, b) => a - b);
}
function buildChannelChips(){
  const field = el("chanField");
  if(state.layout === "single"){ field.classList.add("hidden"); return; }
  field.classList.remove("hidden");
  const box = el("chanChips"); box.innerHTML = "";
  const sel = new Set(selectedChannels());
  chanList().forEach(i => {
    const m = DATA.metrics[i];
    const c = document.createElement("div");
    c.className = "chip" + (sel.has(i) ? "" : " off");
    c.textContent = m.title + (m.unit ? ` [${m.unit}]` : "");
    c.onclick = () => {
      if(state.channels.has(i)) state.channels.delete(i); else state.channels.add(i);
      render();
    };
    box.appendChild(c);
  });
}
/* the time window, in epoch ms, taken from the channel the user is on */
function windowMs(){
  const fr = frame(); if(!fr) return null;
  const [a, b] = timeIdx(fr.f);
  const t0 = epoch(fr.f.time[a]), t1 = epoch(fr.f.time[b]);
  return (t0 === null || t1 === null || t1 <= t0) ? null : [t0, t1];
}
function channelFrame(i){
  const m = DATA.metrics[i];
  const f = state.view === "deviation" ? (m.deviation || m.value) : m.value;
  return {m: m, f: f};
}
/* points of one channel inside the window, as [ms, value] per unit */
function channelPoints(i, win){
  const {m, f} = channelFrame(i);
  const ents = m.entities.filter(e => !state.off.has(e));
  const out = {};
  ents.forEach(e => {
    const s2 = f.series[e]; if(!s2) return;
    const pts = [];
    for(let k = 0; k < f.time.length; k++){
      if(!dayOk(f.time[k])) continue;
      const ms = epoch(f.time[k]);
      if(ms === null || ms < win[0] || ms > win[1]) continue;
      pts.push([ms, s2[k]]);
    }
    if(pts.length) out[e] = pts;
  });
  return {m: m, units: out};
}
/* ---------- the floating readout ------------------------------------------
   Three charts drive it (single, panelled, extra graphs), so it is built and
   placed in one place.  Two rules it has to obey:
     * every visible unit is listed - it used to stop at six, which is useless
       with twenty PCS on screen - so long lists become columns instead;
     * it must stay inside the plot area.  If it can reach the row of export
       buttons it will cover the button the reader is aiming at, and because it
       is pointer-events:none that looks exactly like a dead button.          */
const TIP_COL_ROWS = 8;      /* a column this tall still fits a 210 px panel */
/* The payload timestamps are naive local strings, so epoch() parses them as
   LOCAL time - which means formatting them back with toISOString() shifted
   every label by the browser's offset (nine hours in Japan).  Everything that
   turns a millisecond back into text goes through these. */
const pad2 = n => String(n).padStart(2, "0");
function fmtMs(ms, withDate, withSec){
  const d = new Date(Math.round(ms));
  let out = pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  if(withSec) out += ":" + pad2(d.getSeconds());
  if(!withDate) return out;
  return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate())
       + " " + out;
}
const stampMs   = ms => fmtMs(ms, true, false);
const shortMs   = ms => pad2(new Date(Math.round(ms)).getMonth() + 1) + "-"
                      + pad2(new Date(Math.round(ms)).getDate()) + " " + fmtMs(ms);
/* the string form the report compares against its own timestamps */
const msToKey   = ms => fmtMs(ms, true, true);
/* the sample closest to the cursor, in a [ms, value] list */
function nearestValue(pts, ms){
  let best = null, bd = Infinity;
  for(let k = 0; k < pts.length; k++){
    const d = Math.abs(pts[k][0] - ms);
    if(d < bd){ bd = d; best = pts[k]; }
  }
  return best ? best[1] : null;
}
function tipRow(color, label, value){
  const v = (value === null || value === undefined) ? "\u2014" : fmt(value);
  return `<tr><td><span class="dot2" style="background:${color}"></span>${label}</td>`
       + `<td class="v">${v}</td></tr>`;
}
function tipHtml(when, groups){
  const live = groups.filter(g => g.rows.length);
  const total = live.reduce((n, g) => n + g.rows.length + 1, 0);
  /* Wide beats tall: the box has to stay inside the plot area, so a long list
     is split across up to three columns rather than truncated. */
  const nCols = Math.max(1, Math.min(3, Math.ceil(total / TIP_COL_ROWS)));
  const perCol = Math.ceil(total / nCols);
  const cols = [];
  let cur = [], n = 0;
  live.forEach(g => {
    let rows = g.rows;
    while(rows.length){
      /* the last column takes whatever is left in one piece, or the group
         heading gets repeated two or three times down its side */
      const last = cols.length === nCols - 1;
      const take = rows.slice(0, last ? rows.length : Math.max(1, perCol - n - 1));
      rows = rows.slice(take.length);
      cur.push(`<div class="th">${g.title}</div><table>`
               + take.map(r => tipRow(r.color, r.label, r.value)).join("") + "</table>");
      n += take.length + 1;
      if(n >= perCol && cols.length < nCols - 1){ cols.push(cur); cur = []; n = 0; }
    }
  });
  if(cur.length) cols.push(cur);
  return `<b>${when}</b><div class="tcols">`
       + cols.map(c => `<div class="tcol">${c.join("")}</div>`).join("") + "</div>";
}
/* Whether the readout may show at all.  "ctrl" is for reading a dense chart
   without a box in the way: the crosshair still follows the mouse, the values
   appear only while Ctrl (or Command) is down.  A keypress is deliberate, so
   the arrow keys always bring it up unless it is switched off entirely. */
let CTRL_HELD = false;
function tipAllowed(fromKeyboard){
  if(state.tipMode === "off") return false;
  if(state.tipMode === "ctrl") return !!(fromKeyboard || CTRL_HELD);
  return true;
}
/* The alpha goes on the BACKGROUND, not the element: fading the whole box
   would fade the numbers, which are the reason it exists. */
function applyTipStyle(){
  const a = Math.max(0.15, 1 - (+state.tipClear || 0));
  const rgb = hex2rgb(css("--card") || "#ffffff");
  const bg = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${a.toFixed(2)})`;
  ["tip", "btip"].forEach(id => {
    const e = el(id); if(!e) return;
    e.style.background = bg;
    e.style.boxShadow = a < 0.9 ? "none" : "";
    e.style.borderColor = a < 0.9 ? css("--axis") : "";
  });
  ["tipmode", "gtipmode"].forEach(id => { const e = el(id); if(e) e.value = state.tipMode; });
  ["tipclear", "gtipclear"].forEach(id => {
    const e = el(id); if(e) e.value = Math.round((+state.tipClear || 0) * 100);
  });
}
function hideTips(){
  ["tip", "btip"].forEach(id => { const e = el(id); if(e) e.style.opacity = 0; });
}
addEventListener("keydown", ev => { if(ev.key === "Control" || ev.key === "Meta") CTRL_HELD = true; });
addEventListener("keyup", ev => {
  if(ev.key !== "Control" && ev.key !== "Meta") return;
  CTRL_HELD = false;
  if(state.tipMode === "ctrl") hideTips();
});
addEventListener("blur", () => { CTRL_HELD = false; });
/* Keyboard stepping reuses the height the pointer was last at, so switching
   from the mouse to the arrow keys does not make the box jump. */
/* The box is placed against the chart it is reading - measured, because a
   panel can be half-width and offset - and clamped inside that chart, which is
   what keeps it off the export buttons underneath. */
function placeTip(tipEl, svgEl, cardEl, fracOfSvg, clientY){
  const card = cardEl.getBoundingClientRect();
  const box = svgEl.getBoundingClientRect();
  const w = tipEl.offsetWidth || 180, h = tipEl.offsetHeight || 90;
  const x = (box.left - card.left) + fracOfSvg * box.width;
  const y0 = box.top - card.top;
  let left = x + 18;
  if(left + w > card.width - 6) left = x - w - 18;
  left = Math.max(2, Math.min(Math.max(2, card.width - w - 2), left));
  let top = (clientY === undefined)
    ? y0 + (state.tipFrac === null ? 0.08 : state.tipFrac) * box.height
    : clientY - card.top + 14;
  const lo = y0 + 2, hi = y0 + box.height - h - 2;
  top = (hi < lo) ? lo : Math.max(lo, Math.min(hi, top));
  if(clientY !== undefined && box.height > 0){
    state.tipFrac = Math.max(0, Math.min(1, (top - y0) / box.height));
  }
  tipEl.style.left = left + "px";
  tipEl.style.top = top + "px";
}
const NSVG = "http://www.w3.org/2000/svg";
function svgAdd(svg, tag, at){
  const e = document.createElementNS(NSVG, tag);
  for(const k in at) e.setAttribute(k, at[k]);
  svg.appendChild(e); return e;
}
function axisFrame(svg, W, H, L, R, T, B, lo, hi, ax, xticks){
  const y = v => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  niceTicks(lo, hi, 4).forEach(v => {
    svgAdd(svg, "line", {x1:L, x2:W-R, y1:y(v), y2:y(v),
                         stroke:css("--grid"), "stroke-width":1});
    const t2 = svgAdd(svg, "text", {x:L-8, y:y(v)+4, fill:css("--muted"),
                                    "font-size":10.5, "text-anchor":"end"});
    t2.textContent = fmt(v);
  });
  svgAdd(svg, "line", {x1:L, x2:W-R, y1:H-B, y2:H-B, stroke:css("--axis"), "stroke-width":1});
  const px = f => L + f * (W - L - R);
  /* A divider wherever days were left out, so nobody reads a jump as a real
     change over a few minutes. */
  if(ax && ax.split){
    ax.segs.forEach((r, i) => {
      if(i) svgAdd(svg, "line", {x1:px(r.w0), x2:px(r.w0), y1:T, y2:H-B,
        stroke:css("--axis"), "stroke-width":1, "stroke-dasharray":"3 3"});
      if(xticks){
        const t3 = svgAdd(svg, "text", {x:px((r.w0 + r.w1) / 2), y:H-10,
          fill:css("--ink2"), "font-size":10.5, "text-anchor":"middle",
          "font-weight":600});
        t3.textContent = r.label;
      }
    });
  }
  if(xticks && ax && !ax.split){
    for(let i = 0; i < 6; i++){
      const f = i / 5;
      const t2 = svgAdd(svg, "text", {x:px(f), y:H-10, fill:css("--muted"),
        "font-size":10.5, "text-anchor": i === 0 ? "start" : (i === 5 ? "end" : "middle")});
      t2.textContent = shortMs(ax.ms(f));
    }
  } else if(xticks && ax){
    /* enough room per segment for its own start and end */
    ax.segs.forEach(r => {
      const wide = (r.w1 - r.w0) * (W - L - R) > 150;
      [[r.w0, r.t0, "start"], [r.w1, r.t1, "end"]].forEach(([f, ms, anch], k) => {
        if(!wide && k) return;
        const t2 = svgAdd(svg, "text", {x:px(f), y:H-24, fill:css("--muted"),
          "font-size":10, "text-anchor":anch});
        t2.textContent = fmtMs(ms);
      });
    });
  }
  return y;
}
/* ---- stacked: one panel per channel, same time axis ---- */
function drawStack(cols){
  cols = Math.max(1, cols || 1);
  const wrap = el("stackwrap");
  wrap.innerHTML = "";
  wrap.style.display = cols > 1 ? "grid" : "block";
  wrap.style.gridTemplateColumns = cols > 1 ? `repeat(${cols}, 1fr)` : "";
  wrap.style.gap = cols > 1 ? "14px 16px" : "";
  const win = windowMs(); if(!win) return;
  const AX = axisModel(); if(!AX) return;
  const fr0 = frame(); if(!fr0) return;
  const [i0, i1] = timeIdx(fr0.f);
  const chans = selectedChannels();
  const outer = el("chartwrap").clientWidth;
  const W = Math.max(cols > 1 ? 380 : 680, (outer - (cols - 1) * 16) / cols);
  const L = 62, R = 16;
  const panels = [];
  chans.forEach((ci, idx) => {
    /* side by side every panel needs its own x axis; stacked only the last */
    const last = cols > 1 ? true : idx === chans.length - 1;
    const H = Math.round((cols > 1 ? 210 : (last ? 176 : 152)) * gh());
    const T = 18, B = last ? 34 : 10;
    const {m, units} = channelPoints(ci, win);
    const div = document.createElement("div"); div.className = "panel";
    const svg = document.createElementNS(NSVG, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("height", H);
    svg.setAttribute("preserveAspectRatio", "none");
    div.appendChild(svg);
    const cap = document.createElement("div"); cap.className = "cap";
    cap.textContent = m.title + (m.unit ? ` [${m.unit}]` : "");
    div.appendChild(cap);
    wrap.appendChild(div);

    let lo = Infinity, hi = -Infinity;
    Object.values(units).forEach(pts => pts.forEach(([, v]) => {
      if(v === null) return; if(v < lo) lo = v; if(v > hi) hi = v; }));
    if(!isFinite(lo)){ lo = 0; hi = 1; }
    if(lo === hi){ lo -= 1; hi += 1; }
    if(state.view === "deviation"){ const m2 = Math.max(Math.abs(lo), Math.abs(hi)); lo = -m2; hi = m2; }
    else if(lo >= 0){ lo = 0; }
    const pad = (hi - lo) * 0.06; hi += pad;
    const yv = yView(lo, hi); lo = yv[0]; hi = yv[1];
    const y = axisFrame(svg, W, H, L, R, T, B, lo, hi, AX, last);
    const x = ms => L + AX.frac(ms) * (W - L - R);
    Object.entries(units).forEach(([e, pts]) => {
      let d = "", pen = false, prev = null;
      pts.forEach(([ms, v]) => {
        if(prev !== null && AX.broken(prev, ms)) pen = false;   /* skipped days */
        prev = ms;
        if(v === null){ pen = false; return; }
        d += (pen ? "L" : "M") + x(ms).toFixed(1) + " " + y(v).toFixed(1) + " ";
        pen = true;
      });
      const at = {d:d, fill:"none", stroke:entityColor(e), "stroke-width":1.7,
                  "stroke-linejoin":"round"};
      const dash = entityDash(e); if(dash) at["stroke-dasharray"] = dash;
      svgAdd(svg, "path", at);
    });
    const cross = svgAdd(svg, "line", {x1:0, x2:0, y1:T, y2:H-B,
      stroke:css("--axis"), "stroke-width":1, opacity:0});
    const hit = svgAdd(svg, "rect", {x:L, y:T, width:W-L-R, height:H-T-B, fill:"transparent"});
    attachZoom(svg, hit, {L:L, R:R, T:T, B:B, W:W, H:H, pick: f => AX.ms(f)});
    panels.push({svg, cross, hit, x, W, units, m});
  });

  /* one crosshair across every panel, and one tooltip listing all channels */
  const tip = el("tip");
  function paintStack(ms, clientY, pan){
    if(!panels.length) return;
    ms = AX.snap(Math.max(AX.t0, Math.min(AX.t1, ms)), 1);
    const frac = AX.frac(ms);
    const groups = [];
    panels.forEach(q => {
      q.cross.setAttribute("x1", q.x(ms)); q.cross.setAttribute("x2", q.x(ms));
      q.cross.setAttribute("opacity", 1);
      const names = Object.keys(q.units);
      if(!names.length) return;
      groups.push({
        title: q.m.title + (q.m.unit ? ` [${q.m.unit}]` : ""),
        rows: names.map(e => ({label:e, color:entityColor(e),
                               value:nearestValue(q.units[e], ms)}))
      });
    });
    if(tipAllowed(clientY === undefined)){
      tip.innerHTML = tipHtml(stampMs(ms), groups);
      tip.style.opacity = 1;
      const at = pan || panels[0];
      placeTip(tip, at.svg, el("chartCard"),
               (L + frac * (at.W - L - R)) / at.W, clientY);
    } else tip.style.opacity = 0;
    state.cursorMs = ms;
    state.pinned = (clientY === undefined);
  }
  PAINT = {kind:"ms", lo:AX.t0, hi:AX.t1, fn:paintStack, step:AX.step,
           snap:AX.snap, get:() => state.cursorMs};
  panels.forEach(pan => {
    pan.hit.addEventListener("mousemove", ev => {
      CTRL_HELD = ev.ctrlKey || ev.metaKey;
      const r = pan.svg.getBoundingClientRect();
      const px = (ev.clientX - r.left) * (pan.W / r.width);
      const frac2 = Math.max(0, Math.min(1, (px - L) / (pan.W - L - R)));
      paintStack(AX.ms(frac2), ev.clientY, pan);
    });
    pan.hit.addEventListener("mouseleave", () => {
      if(state.pinned) return;
      tip.style.opacity = 0;
      panels.forEach(q => q.cross.setAttribute("opacity", 0));
    });
  });
  if(state.pinned && state.cursorMs !== null) paintStack(state.cursorMs);
  el("chartNote").textContent = tx("n.stack");
}
/* ---- overlaid: several channels on one chart ---- */
function drawOverlay(){
  const svg = el("chart"); svg.innerHTML = "";
  const win = windowMs(); if(!win) return;
  const AX = axisModel(); if(!AX) return;
  const chans = selectedChannels();
  const W = Math.max(680, el("chartwrap").clientWidth), H = Math.round(360 * gh());
  const L = 62, R = 16, T = 16, B = 34;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("height", H);

  const units = new Set(chans.map(i => DATA.metrics[i].unit || ""));
  const sameUnit = units.size === 1;
  const visible = (DATA.metrics[state.metric] || {entities: []})
                    .entities.filter(e => !state.off.has(e));
  const single = visible.length === 1;

  /* Dual axes are never acceptable, so mixed units are drawn as a share of
     each channel's own maximum.  With several units visible one line per
     channel (their mean) keeps the picture readable. */
  const lines = [];
  chans.forEach((ci, k) => {
    const {m, units: per} = channelPoints(ci, win);
    const names = Object.keys(per);
    if(!names.length) return;
    let pts;
    if(single){
      pts = per[names[0]];
    } else {
      const byMs = new Map();
      names.forEach(e => per[e].forEach(([ms, v]) => {
        if(v === null) return;
        const acc = byMs.get(ms) || [0, 0];
        byMs.set(ms, [acc[0] + v, acc[1] + 1]);
      }));
      pts = [...byMs.entries()].sort((a, b) => a[0] - b[0])
              .map(([ms, [sum, n]]) => [ms, sum / n]);
    }
    let peak = 0;
    pts.forEach(([, v]) => { if(v !== null && Math.abs(v) > peak) peak = Math.abs(v); });
    lines.push({title: m.title, unit: m.unit, pts: pts, peak: peak || 1,
                color: css(SERIES_VARS[k % SERIES_VARS.length])});
  });
  if(!lines.length) return;

  let lo = Infinity, hi = -Infinity;
  lines.forEach(l => l.pts.forEach(([, v]) => {
    if(v === null) return;
    const val = sameUnit ? v : (v / l.peak) * 100;
    if(val < lo) lo = val; if(val > hi) hi = val;
  }));
  if(!isFinite(lo)){ lo = 0; hi = 1; }
  if(state.view === "deviation"){ const m2 = Math.max(Math.abs(lo), Math.abs(hi)); lo = -m2; hi = m2; }
  else if(lo >= 0){ lo = 0; }
  hi += (hi - lo) * 0.06;
  const yv0 = yView(lo, hi); lo = yv0[0]; hi = yv0[1];

  const y = axisFrame(svg, W, H, L, R, T, B, lo, hi, AX, true);
  const x = ms => L + AX.frac(ms) * (W - L - R);
  lines.forEach(l => {
    let d = "", pen = false, prev = null;
    l.pts.forEach(([ms, v]) => {
      if(prev !== null && AX.broken(prev, ms)) pen = false;
      prev = ms;
      if(v === null){ pen = false; return; }
      const val = sameUnit ? v : (v / l.peak) * 100;
      d += (pen ? "L" : "M") + x(ms).toFixed(1) + " " + y(val).toFixed(1) + " ";
      pen = true;
    });
    svgAdd(svg, "path", {d:d, fill:"none", stroke:l.color, "stroke-width":2,
                         "stroke-linejoin":"round"});
  });
  const box = el("legend"); box.innerHTML = "";
  lines.forEach(l => {
    const c = document.createElement("div"); c.className = "chip";
    c.innerHTML = `<span class="dot" style="background:${l.color}"></span>${l.title}`
                + (sameUnit && l.unit ? ` [${l.unit}]` : "");
    box.appendChild(c);
  });
  el("chartTitle").textContent = lines.map(l => l.title).join(" · ")
    + (sameUnit ? (lines[0].unit ? ` [${lines[0].unit}]` : "") : " [%]");
  el("chartNote").textContent =
    (sameUnit ? "" : tx("n.overlayn") + " ")
    + (single ? tx("n.overlay1") : tx("n.overlaym"));
}
function drawChart(){
  const svg=el("chart"); if(!SECTIONS.has("chart")) return;
  const panelled = state.layout === "stack" || state.layout === "grid2";
  el("stackwrap").classList.toggle("hidden", !panelled);
  el("chartwrap").classList.toggle("hidden", panelled);
  if(panelled){ drawStack(state.layout === "grid2" ? 2 : 1); return; }
  if(state.layout === "overlay"){ drawOverlay(); return; }
  svg.innerHTML="";
  const fr=frame(); if(!fr){ return; }
  const ents=visibleEntities(); const [a,b]=timeIdx(fr.f);
  const n=b-a+1;
  const W=Math.max(680, el("chartwrap").clientWidth), H=Math.round(340*gh());
  const L=64,R=18,T=14,B=34;
  svg.setAttribute("viewBox",`0 0 ${W} ${H}`); svg.setAttribute("height",H);
  if(n<=0||!ents.length){ return; }
  /* Only the samples on the chosen days, packed together: the axis is a
     position in THIS list, so days nobody picked take up no width at all. */
  const AX=axisModel();
  const IDX=[];
  for(let i=a;i<=b;i++) if(dayOk(fr.f.time[i])) IDX.push(i);
  const N=IDX.length;
  if(!N||!AX){ el("chartNote").textContent = tx("n.noday"); return; }
  const posOf = new Map(); IDX.forEach((i,k)=>posOf.set(i,k));
  const segAt = k => AX.seg(epoch(fr.f.time[IDX[k]]));

  let lo=Infinity, hi=-Infinity;
  ents.forEach(e=>{ const s=fr.f.series[e];
    IDX.forEach(i=>{ const v=s[i]; if(v===null) return;
      if(v<lo)lo=v; if(v>hi)hi=v; }); });
  if(!isFinite(lo)){ lo=0; hi=1; }
  if(lo===hi){ lo-=1; hi+=1; }
  if(state.view==="deviation"){ const m=Math.max(Math.abs(lo),Math.abs(hi)); lo=-m; hi=m; }
  const nonneg = lo>=0;
  const pad=(hi-lo)*0.06; lo-=pad; hi+=pad;
  if(nonneg) lo=0;                       /* power/irradiance never goes below 0 */
  const yv1 = yView(lo, hi); lo = yv1[0]; hi = yv1[1];

  const x=k=>L+(k/Math.max(1,N-1))*(W-L-R);
  const y=v=>T+(1-(v-lo)/(hi-lo))*(H-T-B);
  const NS="http://www.w3.org/2000/svg";
  const add=(t,at)=>{const e=document.createElementNS(NS,t);
    for(const k in at) e.setAttribute(k,at[k]); svg.appendChild(e); return e;};

  /* grid + y ticks */
  niceTicks(lo,hi,5).forEach(v=>{
    const yy=y(v);
    add("line",{x1:L,x2:W-R,y1:yy,y2:yy,stroke:css("--grid"),"stroke-width":1});
    const t=add("text",{x:L-8,y:yy+4,fill:css("--muted"),"font-size":11,"text-anchor":"end"});
    t.textContent=fmt(v);
  });
  if(state.view==="deviation"){
    add("line",{x1:L,x2:W-R,y1:y(0),y2:y(0),stroke:css("--axis"),"stroke-width":1.5});
  }
  /* x ticks, and a divider wherever a day was left out */
  const xt=Math.min(7,N);
  for(let i=0;i<xt;i++){
    const k=Math.round(i*(N-1)/Math.max(1,xt-1));
    const label=(fr.f.time[IDX[k]]||"").slice(N>400?5:0,16);
    const t=add("text",{x:x(k),y:H-12,fill:css("--muted"),"font-size":11,
      "text-anchor": i===0?"start":(i===xt-1?"end":"middle")});
    t.textContent=label;
  }
  for(let k=1;k<N;k++){
    if(segAt(k)===segAt(k-1)) continue;
    add("line",{x1:x(k)-0.5,x2:x(k)-0.5,y1:T,y2:H-B,stroke:css("--axis"),
      "stroke-width":1,"stroke-dasharray":"3 3"});
    const lb=add("text",{x:x(k)+4,y:T+11,fill:css("--ink2"),"font-size":10.5,
      "font-weight":600,"text-anchor":"start"});
    lb.textContent=AX.segs[segAt(k)].label;
  }
  add("line",{x1:L,x2:W-R,y1:H-B,y2:H-B,stroke:css("--axis"),"stroke-width":1});

  /* series */
  ents.forEach(e=>{
    const s=fr.f.series[e]; let d="", pen=false;
    for(let k=0;k<N;k++){
      if(k && segAt(k)!==segAt(k-1)) pen=false;      /* skipped days between */
      const v=s[IDX[k]];
      if(v===null){ pen=false; continue; }
      d += (pen? "L":"M") + x(k).toFixed(1) + " " + y(v).toFixed(1) + " ";
      pen=true;
    }
    const at = {d:d,fill:"none",stroke:entityColor(e),"stroke-width":2,
      "stroke-linejoin":"round","stroke-linecap":"round"};
    const dash = entityDash(e);
    if(dash) at["stroke-dasharray"] = dash;
    add("path",at);
  });

  /* hover layer */
  const cross=add("line",{x1:0,x2:0,y1:T,y2:H-B,stroke:css("--axis"),
    "stroke-width":1,opacity:0});
  const dots=ents.map(e=>add("circle",{r:4,fill:entityColor(e),stroke:css("--surface"),
    "stroke-width":2,opacity:0}));
  const hit=add("rect",{x:L,y:T,width:W-L-R,height:H-T-B,fill:"transparent"});
  attachZoom(svg, hit, {L:L, R:R, T:T, B:B, W:W, H:H,
    pick: f => epoch(fr.f.time[IDX[Math.max(0, Math.min(N-1, Math.round(f*(N-1))))]])});
  const tip=el("tip");
  /* One painter, two drivers: the mouse and the arrow keys.  Keyboard stepping
     is the only way to land on an exact minute - a pixel is several samples
     wide once a day is on screen. */
  function paintSingle(pos, clientY){
    const k0 = Math.max(0, Math.min(N-1, Math.round(pos)));
    const i = IDX[k0];
    cross.setAttribute("x1",x(k0)); cross.setAttribute("x2",x(k0));
    cross.setAttribute("opacity",1);
    const rows=[];
    ents.forEach((e,k)=>{
      const v=fr.f.series[e][i];
      if(v===null){ dots[k].setAttribute("opacity",0); }
      else{ dots[k].setAttribute("cx",x(k0)); dots[k].setAttribute("cy",y(v));
            dots[k].setAttribute("opacity",1); }
      rows.push({label:e, color:entityColor(e), value:v});
    });
    const unit = state.view==="deviation" ? (fr.m.deviation_percent?"%":fr.m.unit) : fr.m.unit;
    if(tipAllowed(clientY === undefined)){
      tip.innerHTML = tipHtml(fr.f.time[i],
        [{title: fr.m.title + (unit ? ` [${unit}]` : ""), rows: rows}]);
      tip.style.opacity=1;
      placeTip(tip, svg, el("chartCard"), x(k0)/W, clientY);
    } else tip.style.opacity=0;
    state.cursor = i;
    /* only the keyboard PINS the readout; a hover must let go on the way out,
       or the box sits over whatever the reader is trying to click next */
    state.pinned = (clientY === undefined);
  }
  PAINT = {kind:"single", lo:0, hi:N-1, fn:paintSingle,
           get:() => (state.cursor === null ? null
                      : (posOf.has(state.cursor) ? posOf.get(state.cursor) : null))};
  hit.addEventListener("mousemove",ev=>{
    CTRL_HELD = ev.ctrlKey || ev.metaKey;
    const r=svg.getBoundingClientRect();
    const px=(ev.clientX-r.left)*(W/r.width);
    paintSingle(Math.round((px-L)/(W-L-R)*(N-1)), ev.clientY);
  });
  hit.addEventListener("mouseleave",()=>{
    if(state.pinned) return;                   /* a keyboard cursor stays put */
    tip.style.opacity=0; cross.setAttribute("opacity",0);
    dots.forEach(d=>d.setAttribute("opacity",0));
  });
  if(state.pinned && state.cursor !== null && posOf.has(state.cursor))
    paintSingle(posOf.get(state.cursor));
}

/* ---------- graph builder --------------------------------------------------
   The reader adds as many graphs as the question needs.  Two kinds of channel
   behave differently, and that difference is the whole point:

     * a per-unit channel (交流電力, 直流電力 ...) is one line per PCS, so it
       owns its graph - a second such channel would need the unit palette
       twice and nobody could tell the lines apart;
     * a plant-wide channel (日射量, 気温) is byte-identical in all 20 PCS
       blocks, so it is drawn once, in its own colour, and can be dropped onto
       any graph as an extra line.

   Every graph shares the time axis, the period and the unit selection of the
   chart above, which is what makes the patterns comparable.                 */
const WEATHER_VARS = ["--w1","--w2","--w3","--w4"];
const isShared = i => !!(DATA.metrics[i] && DATA.metrics[i].shared);
function chanLabel(i){
  const m = DATA.metrics[i]; if(!m) return "";
  return m.title + (m.unit ? ` [${m.unit}]` : "");
}
/* Graphs travel as channel KEYS, not indexes, so the app can add or drop
   channels without a saved graph pointing at the wrong series. */
function defaultGraphs(){
  const out = [];
  (DATA.default_graphs || []).forEach(g => {
    const ch = (g.channels || []).map(k => metricIndexFor(k)).filter(i => i >= 0);
    if(ch.length) out.push({channels: ch});
  });
  return out;
}
function graphList(){
  if(!Array.isArray(state.graphs)) state.graphs = defaultGraphs();
  return state.graphs;
}
const allChanList = () => DATA.metrics.map((m, i) => i);
/* A plant-wide channel keeps its colour by position among the plant-wide
   channels of its own graph, so adding a per-unit channel repaints nothing. */
function sharedColor(g, ci){
  const k = (g.channels || []).filter(isShared).indexOf(ci);
  return css(WEATHER_VARS[(k < 0 ? 0 : k) % WEATHER_VARS.length]);
}
function graphSeries(g, win){
  const out = [];
  if(!win) return out;
  (g.channels || []).forEach(ci => {
    const m = DATA.metrics[ci]; if(!m) return;
    if(isShared(ci)){
      const {f} = channelFrame(ci);
      const e = m.entities[0];
      const s = f ? f.series[e] : null; if(!s) return;
      const pts = [];
      for(let k = 0; k < f.time.length; k++){
        if(!dayOk(f.time[k])) continue;
        const ms = epoch(f.time[k]);
        if(ms === null || ms < win[0] || ms > win[1]) continue;
        pts.push([ms, s[k]]);
      }
      if(pts.length) out.push({ci:ci, label:m.title, unit:m.unit || "",
        color:sharedColor(g, ci), dash:"", width:2.4, pts:pts, shared:true});
    } else {
      const {units} = channelPoints(ci, win);
      Object.keys(units).forEach(e => out.push({ci:ci, label:e, unit:m.unit || "",
        color:entityColor(e), dash:entityDash(e), width:1.8,
        pts:units[e], shared:false}));
    }
  });
  return out;
}
/* Mixed units are drawn as a share of each channel's own maximum - dual axes
   are never acceptable - so the maximum has to be stated wherever the lines
   are named: the note in the page and the legend in the exported image. */
function graphModel(g, win){
  const series = graphSeries(g, win);
  const sameUnit = new Set(series.map(s => s.unit)).size <= 1;
  const peak = {};
  series.forEach(s => {
    let p = peak[s.ci] || 0;
    s.pts.forEach(([, v]) => { if(v !== null && Math.abs(v) > p) p = Math.abs(v); });
    peak[s.ci] = p || 1;
  });
  return {series:series, sameUnit:sameUnit, peak:peak};
}
/* How tall one series is drawn.  Two channels on one graph can differ by a
   factor of a hundred - 気温 barely moves next to 日射量 - so each channel can
   be given a multiplier.  It is keyed by the channel KEY, not its index, so it
   survives a pruned export and a saved view.  Anything other than x1 is
   written next to the name everywhere the series is named: a line drawn three
   times too tall and not labelled as such is a lie. */
function gainOf(ci){
  const m = DATA.metrics[ci];
  if(!m) return 1;
  const g = +(state.gain || {})[m.key];
  return (isFinite(g) && g > 0) ? g : 1;
}
function setGain(ci, value){
  const m = DATA.metrics[ci]; if(!m) return;
  const g = parseFloat(value);
  if(!state.gain) state.gain = {};
  if(!isFinite(g) || g <= 0 || g === 1) delete state.gain[m.key];
  else state.gain[m.key] = Math.max(0.01, Math.min(100, g));
  render();
}
const gainTag = ci => gainOf(ci) === 1 ? "" : ` \u00d7${fmt(gainOf(ci))}`;
function refText(m, peak, ci){
  const tag = (ci === undefined) ? "" : gainTag(ci);
  return `${m.title}${tag} ${fmt(peak)}${m.unit ? " " + m.unit : ""} = 100%`;
}
function graphTitle(g){
  const names = (g.channels || []).map(i => chanLabel(i) + gainTag(i)).filter(Boolean);
  return names.length ? names.join(" ・ ") : tx("n.gempty");
}
/* one legend entry per line, in drawing order, for the exported image */
function graphLegend(g){
  const seen = new Set(), out = [];
  const mod = graphModel(g, windowMs());
  mod.series.forEach(s => {
    const key = s.label + "|" + s.color;
    if(seen.has(key)) return;
    seen.add(key);
    const label = (!mod.sameUnit && s.shared)
      ? refText(DATA.metrics[s.ci], mod.peak[s.ci], s.ci)
      : s.label + (s.shared ? gainTag(s.ci) : "");
    out.push({label:label, color:s.color, dash:s.dash});
  });
  return out;
}
/* A per-unit channel gets a graph of its own: that is what the reader asked
   for, so adding one to an occupied graph creates the new graph instead of
   quietly producing an unreadable picture. */
function addSeries(gi, i){
  const graphs = graphList();
  const g = graphs[gi]; if(!g) return;
  if(!isShared(i) && (g.channels || []).some(c => !isShared(c))){
    graphs.splice(gi + 1, 0, {channels:[i]});
    state.gnote = tx("n.newgraph");
  } else {
    g.channels = [...(g.channels || []), i];
  }
  render();
}
function graphBar(g, gi){
  const bar = document.createElement("div"); bar.className = "gbar";
  (g.channels || []).forEach((ci, pos) => {
    const chip = document.createElement("span"); chip.className = "gchip";
    const sw = document.createElement("span"); sw.className = "sw";
    sw.style.background = isShared(ci) ? sharedColor(g, ci) : css("--axis");
    chip.appendChild(sw);
    chip.appendChild(document.createTextNode(chanLabel(ci)));
    /* how tall this series is drawn */
    const gi2 = document.createElement("input");
    gi2.type = "number"; gi2.className = "gain";
    gi2.min = "0.05"; gi2.max = "100"; gi2.step = "0.5";
    gi2.value = String(gainOf(ci));
    gi2.title = tx("t.gain");
    gi2.onchange = ev => { setGain(ci, ev.target.value); ev.target.blur(); };
    chip.appendChild(document.createTextNode(" \u00d7"));
    chip.appendChild(gi2);
    /* where it sits in the drawing order - the last one is drawn on top */
    const mk = (label, delta, enabled) => {
      const b = document.createElement("button");
      b.className = "x"; b.textContent = label;
      b.title = tx(delta < 0 ? "t.movedown" : "t.moveup");
      b.disabled = !enabled;
      b.onclick = () => {
        const arr = g.channels;
        arr.splice(pos + delta, 0, arr.splice(pos, 1)[0]);
        state.gnote = ""; render();
      };
      chip.appendChild(b);
    };
    mk("\u25c0", -1, pos > 0);
    mk("\u25b6", 1, pos < (g.channels.length - 1));
    const x = document.createElement("button");
    x.className = "x"; x.textContent = "×"; x.title = tx("b.delseries");
    x.onclick = () => {
      g.channels = (g.channels || []).filter(c => c !== ci);
      state.gnote = ""; render();
    };
    chip.appendChild(x);
    bar.appendChild(chip);
  });
  const sel = document.createElement("select");
  const head = document.createElement("option");
  head.value = ""; head.textContent = tx("f.addchan"); sel.appendChild(head);
  allChanList().forEach(i => {
    if((g.channels || []).includes(i)) return;
    const o = document.createElement("option");
    o.value = String(i);
    o.textContent = chanLabel(i) + (isShared(i) ? "  ○" : "");
    sel.appendChild(o);
  });
  sel.onchange = () => {
    const i = parseInt(sel.value, 10);
    sel.blur();
    if(isFinite(i)) addSeries(gi, i);
  };
  bar.appendChild(sel);
  const sp = document.createElement("span"); sp.className = "sp"; bar.appendChild(sp);
  [["b.gpng", () => exportGraph(gi, "png")],
   ["b.gjpg", () => exportGraph(gi, "jpg")],
   ["b.savegraph", () => saveGraphOnly(gi)]].forEach(([k, fn]) => {
    const b = document.createElement("button");
    b.textContent = tx(k); b.onclick = fn; bar.appendChild(b);
  });
  const del = document.createElement("button");
  del.textContent = "×"; del.title = tx("b.delgraph");
  del.onclick = () => { graphList().splice(gi, 1); state.gnote = ""; render(); };
  bar.appendChild(del);
  return bar;
}
let GPANELS = [];
let GPAINT = null;
function drawGraphs(){
  const card = el("buildCard");
  if(!SECTIONS.has("chart")){ card.classList.add("hidden"); return; }
  card.classList.remove("hidden");
  const wrap = el("graphwrap");
  wrap.innerHTML = "";
  GPANELS = []; GPAINT = null;
  const graphs = graphList();
  const cols = (+state.gcols === 2) ? 2 : 1;
  wrap.classList.toggle("two", cols === 2);
  el("gnote").textContent = state.gnote || "";
  state.gnote = "";
  const win = windowMs();
  const AX = axisModel();
  const outer = wrap.clientWidth || card.clientWidth || 900;
  const W = Math.max(cols > 1 ? 360 : 640, (outer - (cols - 1) * 18) / cols - 28);
  const H = Math.round((cols > 1 ? 208 : 236) * gh());
  const L = 62, R = 16, T = 18, B = 34;
  graphs.forEach((g, gi) => {
    const box = document.createElement("div"); box.className = "gbox";
    wrap.appendChild(box);
    const cap = document.createElement("div");
    cap.className = "small";
    cap.style.fontWeight = "600"; cap.style.marginBottom = "4px";
    /* an empty graph says so once, in the placeholder - not twice */
    cap.textContent = (g.channels || []).length ? graphTitle(g) : `#${gi + 1}`;
    box.appendChild(cap);
    const mod = graphModel(g, win);
    const series = mod.series;
    if(!series.length || !win || !AX){
      const p = document.createElement("div");
      p.className = "small"; p.textContent = tx("n.gempty");
      box.appendChild(p);
      GPANELS.push(null);
      box.appendChild(graphBar(g, gi));
      return;
    }
    const sameUnit = mod.sameUnit, peak = mod.peak;
    /* The axis comes from the REAL data; the multiplier is applied only when
       the line is drawn, and it amplifies the series around ITS OWN MEAN.

       Two things that do not work: letting a x4 series stretch the axis (that
       shrinks everything else instead of enlarging that one), and multiplying
       from zero (a channel sitting at 60-100% of its own maximum - a
       temperature - simply leaves the top of the frame).  Growing in place is
       what makes a nearly flat line readable.  Anything that still runs off
       the edge is clipped. */
    const base  = s => v => sameUnit ? v : (v / peak[s.ci]) * 100;
    const mid = {};
    series.forEach(s => {
      if(mid[s.ci] !== undefined) return;
      const f0 = base(s);
      let n = 0, sum = 0;
      series.filter(q => q.ci === s.ci).forEach(q => q.pts.forEach(([, v]) => {
        if(v === null) return; n++; sum += f0(v);
      }));
      mid[s.ci] = n ? sum / n : 0;
    });
    const scale = s => v => {
      const g2 = gainOf(s.ci), b = base(s)(v);
      return g2 === 1 ? b : mid[s.ci] + (b - mid[s.ci]) * g2;
    };
    let lo = Infinity, hi = -Infinity;
    series.forEach(s => { const f2 = base(s);
      s.pts.forEach(([, v]) => { if(v === null) return;
        const q = f2(v); if(q < lo) lo = q; if(q > hi) hi = q; }); });
    if(!isFinite(lo)){ lo = 0; hi = 1; }
    if(lo === hi){ lo -= 1; hi += 1; }
    if(state.view === "deviation"){
      const m2 = Math.max(Math.abs(lo), Math.abs(hi)); lo = -m2; hi = m2;
    } else if(lo >= 0){ lo = 0; }
    hi += (hi - lo) * 0.06;
    const yv = yView(lo, hi); lo = yv[0]; hi = yv[1];

    const svg = document.createElementNS(NSVG, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("height", H);
    svg.setAttribute("preserveAspectRatio", "none");
    svg.style.display = "block"; svg.style.width = "100%"; svg.style.height = "auto";
    box.appendChild(svg);
    const clipId = `gclip${gi}_${Math.random().toString(36).slice(2, 8)}`;
    const cp = svgAdd(svg, "clipPath", {id: clipId});
    const cr = document.createElementNS(NSVG, "rect");
    cr.setAttribute("x", L); cr.setAttribute("y", T);
    cr.setAttribute("width", Math.max(0, W - L - R));
    cr.setAttribute("height", Math.max(0, H - T - B));
    cp.appendChild(cr);
    const y = axisFrame(svg, W, H, L, R, T, B, lo, hi, AX, true);
    const x = ms => L + AX.frac(ms) * (W - L - R);
    if(state.view === "deviation" && lo < 0){
      svgAdd(svg, "line", {x1:L, x2:W-R, y1:y(0), y2:y(0),
                           stroke:css("--axis"), "stroke-width":1.4});
    }
    series.forEach(s => {
      const f2 = scale(s);
      let d = "", pen = false, prev = null;
      s.pts.forEach(([ms, v]) => {
        if(prev !== null && AX.broken(prev, ms)) pen = false;
        prev = ms;
        if(v === null){ pen = false; return; }
        d += (pen ? "L" : "M") + x(ms).toFixed(1) + " " + y(f2(v)).toFixed(1) + " ";
        pen = true;
      });
      const at = {d:d, fill:"none", stroke:s.color, "stroke-width":s.width,
                  "stroke-linejoin":"round", "clip-path":`url(#${clipId})`};
      if(s.dash) at["stroke-dasharray"] = s.dash;
      svgAdd(svg, "path", at);
    });
    const cross = svgAdd(svg, "line", {x1:0, x2:0, y1:T, y2:H-B,
      stroke:css("--axis"), "stroke-width":1, opacity:0});
    const hit = svgAdd(svg, "rect", {x:L, y:T, width:W-L-R, height:H-T-B,
      fill:"transparent"});
    attachZoom(svg, hit, {L:L, R:R, T:T, B:B, W:W, H:H, pick: f => AX.ms(f)});
    GPANELS.push({svg, cross, hit, x, W, series, sameUnit, gi});
    box.appendChild(graphBar(g, gi));
    if(!sameUnit){
      const seen = new Set(), refs = [];
      series.forEach(s => {
        if(seen.has(s.ci)) return;
        seen.add(s.ci);
        refs.push(refText(DATA.metrics[s.ci], peak[s.ci], s.ci));
      });
      const n = document.createElement("div");
      n.className = "small"; n.style.marginTop = "4px";
      n.textContent = tx("n.gpct") + "  " + refs.join(" ・ ");
      box.appendChild(n);
    } else {
      /* same unit everywhere, so the axis is real values - but a multiplied
         series is not, and that has to be said */
      const scaled = [...new Set(series.filter(s => gainOf(s.ci) !== 1).map(s => s.ci))];
      if(scaled.length){
        const n = document.createElement("div");
        n.className = "small"; n.style.marginTop = "4px";
        n.textContent = tx("n.gain", {list: scaled.map(
          ci => DATA.metrics[ci].title + gainTag(ci)).join(" ・ ")});
        box.appendChild(n);
      }
    }
  });

  const live = GPANELS.filter(Boolean);
  if(!live.length || !win || !AX) return;
  const tip = el("btip");
  function paintGraphs(ms, clientY, pan){
    ms = AX.snap(Math.max(AX.t0, Math.min(AX.t1, ms)), 1);
    const groups = [];
    live.forEach(q => {
      q.cross.setAttribute("x1", q.x(ms)); q.cross.setAttribute("x2", q.x(ms));
      q.cross.setAttribute("opacity", 1);
      let cur = null;
      q.series.forEach(s => {
        if(!cur || cur.ci !== s.ci){
          const m = DATA.metrics[s.ci];
          cur = {ci:s.ci, title:m.title + (m.unit ? ` [${m.unit}]` : ""), rows:[]};
          groups.push(cur);
        }
        cur.rows.push({label:s.label, color:s.color,
                       value:nearestValue(s.pts, ms)});
      });
    });
    if(tipAllowed(clientY === undefined)){
      tip.innerHTML = tipHtml(stampMs(ms), groups);
      tip.style.opacity = 1;
      const gf = AX.frac(ms);
      const at = pan || live[0];
      placeTip(tip, at.svg, el("buildCard"), (L + gf * (at.W - L - R)) / at.W, clientY);
    } else tip.style.opacity = 0;
    state.gcursorMs = ms;
    state.gpinned = (clientY === undefined);
  }
  GPAINT = {kind:"ms", lo:AX.t0, hi:AX.t1, fn:paintGraphs, step:AX.step,
            snap:AX.snap, get:() => state.gcursorMs};
  live.forEach(pan => {
    pan.hit.addEventListener("mousemove", ev => {
      state.focus = "build";
      CTRL_HELD = ev.ctrlKey || ev.metaKey;
      const r = pan.svg.getBoundingClientRect();
      const px = (ev.clientX - r.left) * (pan.W / r.width);
      const f2 = Math.max(0, Math.min(1, (px - L) / (pan.W - L - R)));
      paintGraphs(AX.ms(f2), ev.clientY, pan);
    });
    pan.hit.addEventListener("mouseleave", () => {
      if(state.gpinned) return;
      tip.style.opacity = 0;
      live.forEach(q => q.cross.setAttribute("opacity", 0));
    });
  });
  if(state.gpinned && state.gcursorMs !== null) paintGraphs(state.gcursorMs);
}
el("addGraph").onclick = () => {
  const graphs = graphList();
  graphs.push({channels: state.metric >= 0 ? [state.metric] : []});
  state.gnote = ""; render();
};
el("resetGraphs").onclick = () => {
  state.graphs = defaultGraphs(); state.gnote = ""; render();
};
el("gcols").onchange = e => {
  state.gcols = (+e.target.value === 2) ? 2 : 1;
  e.target.blur(); render();
};
async function exportGraph(gi, kind){
  const pan = GPANELS[gi];
  if(!pan || !pan.svg) return;
  const cv = await svgToCanvas(pan.svg, 2, graphLegend(graphList()[gi]));
  const type = kind === "jpg" ? "image/jpeg" : "image/png";
  const ext = kind === "jpg" ? "jpg" : "png";
  const name = safeName(graphTitle(graphList()[gi]));
  cv.toBlob(bl => { if(bl) download(bl, `${name}_${stamp()}.${ext}`); },
            type, kind === "jpg" ? 0.92 : undefined);
}

/* ---------- zoom -----------------------------------------------------------
   Zoom is not a second viewport.  It writes the report's own From / To, so one
   gesture narrows the single chart, the panels, every extra graph, the KPI
   tiles, the matrix and the CSV export together - and the pickers above show
   exactly what is on screen.  That also means a zoomed view survives "save
   this view" and "this graph only" without any extra plumbing.

   The day tab stays the outer bound: zooming inside a day cannot wander into
   the next one.                                                             */
function fullRangeMs(){
  const fr = frame(); if(!fr) return null;
  const time = fr.f.time; if(!time.length) return null;
  let lo = 0, hi = time.length - 1;
  if(state.days.size){
    while(lo <= hi && !dayOk(time[lo])) lo++;
    while(hi >= lo && !dayOk(time[hi])) hi--;
  }
  if(hi <= lo) return null;
  const t0 = epoch(time[lo]), t1 = epoch(time[hi]);
  return (t0 === null || t1 === null || t1 <= t0) ? null : [t0, t1];
}
function sampleMs(){
  const fr = frame(); if(!fr) return 60000;
  const t = fr.f.time;
  if(t.length < 2) return 60000;
  const d = epoch(t[1]) - epoch(t[0]);
  return (d && d > 0) ? d : 60000;
}
/* three samples is the floor: below that there is no line left to look at */
const minSpanMs = () => 3 * sampleMs();
/* What is drawn: snapped to the samples that exist. */
const viewMs = () => windowMs();
const zoomed = () => !!(state.from || state.to);
/* What was asked for.  The drawn window is snapped inwards to real samples, so
   feeding it back into the next zoom or pan would shave a sample off every
   time; the arithmetic uses the requested edges instead. */
function askedMs(){
  const full = fullRangeMs(); if(!full) return null;
  const t0 = state.from ? epoch(state.from) : null;
  const t1 = state.to   ? epoch(state.to)   : null;
  return [t0 === null ? full[0] : t0, t1 === null ? full[1] : t1];
}

function setWindowMs(t0, t1){
  const full = fullRangeMs(); if(!full) return;
  const whole = full[1] - full[0];
  let span = Math.max(minSpanMs(), Math.min(whole, t1 - t0));
  t0 = Math.max(full[0], Math.min(full[1] - span, t0));
  t1 = t0 + span;
  const all = (t0 <= full[0] + 1) && (t1 >= full[1] - 1);
  state.from = all ? null : msToKey(t0);
  state.to   = all ? null : msToKey(t1);
  el("from").value = state.from ? state.from.slice(0, 16).replace(" ", "T") : "";
  el("to").value   = state.to   ? state.to.slice(0, 16).replace(" ", "T") : "";
  state.rows = 300;
  render();
}
function zoomBy(factor, anchorMs){
  const cur = askedMs(), full = fullRangeMs();
  if(!cur || !full) return;
  const span = cur[1] - cur[0];
  const want = Math.max(minSpanMs(), Math.min(full[1] - full[0], span * factor));
  /* keep whatever is under the pointer (or under the cursor) where it is */
  const anchor = (anchorMs === null || anchorMs === undefined)
    ? (cur[0] + cur[1]) / 2
    : Math.max(cur[0], Math.min(cur[1], anchorMs));
  const rel = span > 0 ? (anchor - cur[0]) / span : 0.5;
  setWindowMs(anchor - rel * want, anchor - rel * want + want);
}
function panBy(frac){
  const cur = askedMs(); if(!cur) return;
  const span = cur[1] - cur[0];
  setWindowMs(cur[0] + frac * span, cur[1] + frac * span);
}
function zoomReset(){
  if(!zoomed()) return;
  state.from = state.to = null;
  el("from").value = ""; el("to").value = "";
  state.rows = 300;
  render();
}
/* where the reader is looking, for keyboard zoom */
function cursorAnchor(){
  if(state.focus === "build" && state.gcursorMs !== null) return state.gcursorMs;
  if(state.cursorMs !== null) return state.cursorMs;
  if(state.cursor !== null){
    const fr = frame();
    if(fr) return epoch(fr.f.time[state.cursor]);
  }
  return null;
}
/* A wheel sends a burst of events; re-rendering an 8 MB report on each one
   would stutter, so they are multiplied together and applied once per frame. */
let yFactor = 0, yAnchor = null, yRaf = 0;
function wheelYZoom(factor, fy){
  yFactor = yFactor ? yFactor * factor : factor;
  yAnchor = fy;
  if(yRaf) return;
  yRaf = requestAnimationFrame(() => {
    const f = yFactor, a = yAnchor;
    yFactor = 0; yAnchor = null; yRaf = 0;
    yZoomBy(f, a);
  });
}
let zoomFactor = 0, zoomAnchor = null, zoomRaf = 0;
function wheelZoom(factor, anchorMs){
  zoomFactor = zoomFactor ? zoomFactor * factor : factor;
  zoomAnchor = anchorMs;
  if(zoomRaf) return;
  zoomRaf = requestAnimationFrame(() => {
    const f = zoomFactor, a = zoomAnchor;
    zoomFactor = 0; zoomAnchor = null; zoomRaf = 0;
    zoomBy(f, a);
  });
}
/* Drag a range, Shift-drag to pan, Ctrl/Shift-wheel for time, Alt-wheel for the
   value axis, double-click for the default view.  `pick(fraction of the plot)
   -> ms` is all that differs between the index-based single chart and the
   timestamp-based panels.

   The gesture lives at module level, not in one <svg>'s closure: panning
   re-renders, a re-render REPLACES the svg and its listeners, and a drag
   tracked on the element itself therefore died after its first move.  One pair
   of window listeners owns every drag instead. */
let DRAG = null;
function fracOf(svg, geom, clientX){
  const r = svg.getBoundingClientRect();
  if(!r.width) return 0;
  const px = (clientX - r.left) * (geom.W / r.width);
  return Math.max(0, Math.min(1, (px - geom.L) / Math.max(1, geom.W - geom.L - geom.R)));
}
function fracYOf(svg, geom, clientY){
  const r = svg.getBoundingClientRect();
  if(!r.height) return 0.5;
  const py = (clientY - r.top) * (geom.H / r.height);
  return Math.max(0, Math.min(1, (py - geom.T) / Math.max(1, geom.H - geom.T - geom.B)));
}
addEventListener("mousemove", ev => {
  if(!DRAG) return;
  if(DRAG.mode === "pan"){
    ev.preventDefault();
    if(DRAG.w && DRAG.t1 > DRAG.t0){
      const dt = -(ev.clientX - DRAG.x) / DRAG.w * (DRAG.t1 - DRAG.t0);
      state.from = msToKey(DRAG.t0 + dt);
      state.to   = msToKey(DRAG.t1 + dt);
    }
    if(DRAG.h && DRAG.z < 1){
      const dy = (ev.clientY - DRAG.y) / DRAG.h * DRAG.z;
      state.ycenter = clampC(DRAG.c + dy, DRAG.z);
    }
    renderFast();
  } else if(DRAG.mode === "band" && DRAG.rect){
    ev.preventDefault();
    const g = DRAG.geom;
    const f1 = fracOf(DRAG.svg, g, ev.clientX);
    DRAG.f1 = f1;
    DRAG.rect.setAttribute("x", g.L + Math.min(DRAG.f0, f1) * (g.W - g.L - g.R));
    DRAG.rect.setAttribute("width", Math.abs(f1 - DRAG.f0) * (g.W - g.L - g.R));
  }
});
addEventListener("mouseup", () => {
  if(!DRAG) return;
  const d = DRAG; DRAG = null;
  document.body.style.cursor = "";
  if(d.mode === "pan"){
    const cur = askedMs();
    if(cur) setWindowMs(cur[0], cur[1]);   /* clamp, sync the pickers, full render */
    else render();
    return;
  }
  if(d.rect) d.rect.remove();
  if(d.f1 === undefined || Math.abs(d.f1 - d.f0) < 0.012) return;   /* a click */
  const t0 = d.geom.pick(Math.min(d.f0, d.f1));
  const t1 = d.geom.pick(Math.max(d.f0, d.f1));
  if(t0 !== null && t1 !== null) setWindowMs(t0, t1);
});
function attachZoom(svg, hit, geom){
  hit.addEventListener("mousedown", ev => {
    if(ev.button !== 0) return;
    if(ev.shiftKey){
      const cur = askedMs(), r = svg.getBoundingClientRect();
      DRAG = {mode:"pan", x:ev.clientX, y:ev.clientY, w:r.width, h:r.height,
              t0: cur ? cur[0] : 0, t1: cur ? cur[1] : 0, c: yc(), z: yz()};
      document.body.style.cursor = "grabbing";
      ev.preventDefault();
      return;
    }
    const f0 = fracOf(svg, geom, ev.clientX);
    const rect = svgAdd(svg, "rect", {x: geom.L + f0 * (geom.W - geom.L - geom.R),
      y: geom.T, width: 0, height: Math.max(0, geom.H - geom.T - geom.B),
      fill: css("--accent"), opacity: 0.15, stroke: css("--accent"),
      "stroke-width": 1, "pointer-events": "none"});
    DRAG = {mode:"band", svg:svg, geom:geom, f0:f0, rect:rect};
    /* NOT preventDefault(): suppressing the default mousedown also suppresses
       the dblclick that follows, and dblclick is the way back to the default
       view.  Text selection is stopped with CSS instead. */
  });
  hit.addEventListener("dblclick", ev => { ev.preventDefault(); resetView(); });
  /* plain wheel keeps scrolling the page - this report is tall */
  hit.addEventListener("wheel", ev => {
    const val = ev.altKey;                       /* Alt = the value axis */
    if(!val && !(ev.ctrlKey || ev.metaKey || ev.shiftKey)) return;
    ev.preventDefault();
    if(val) wheelYZoom(ev.deltaY > 0 ? 1.3 : 1 / 1.3, fracYOf(svg, geom, ev.clientY));
    else wheelZoom(ev.deltaY > 0 ? 1.3 : 1 / 1.3, geom.pick(fracOf(svg, geom, ev.clientX)));
  }, {passive: false});
}
/* ---------- the value axis, and how tall the panel is ---------------------
   The time axis is shared, but the value axis cannot be: one panel is kW, the
   next is °C.  So the zoom is stored as a fraction OF EACH PANEL'S OWN auto
   range (`yzoom`) plus where the middle of that fraction sits (`ycenter`),
   which means one setting works on every panel whatever its unit.
   yzoom 1 / ycenter 0.5 is exactly the automatic range, i.e. the old
   behaviour.                                                               */
const clampZ = z => Math.max(0.02, Math.min(1, +z || 1));
const clampC = (c, z) => Math.max(z / 2, Math.min(1 - z / 2, isFinite(c) ? c : 0.5));
const yz = () => clampZ(state.yzoom);
const yc = () => clampC(state.ycenter === undefined ? 0.5 : state.ycenter, yz());
/* auto range in, shown range out */
function yView(lo, hi){
  const z = yz();
  if(z >= 1) return [lo, hi];
  const full = hi - lo;
  const mid = lo + full * yc();
  const half = full * z / 2;
  return [mid - half, mid + half];
}
function yZoomBy(factor, fy){
  const z0 = yz(), c0 = yc();
  const rel = (fy === null || fy === undefined) ? 0.5 : (1 - fy);
  const anchor = (c0 - z0 / 2) + rel * z0;      /* what the pointer is over */
  const z1 = clampZ(z0 * factor);
  state.yzoom = z1;
  state.ycenter = clampC(anchor - (rel - 0.5) * z1, z1);
  render();
}
function yPanBy(frac){
  const z = yz();
  state.ycenter = clampC(yc() + frac * z, z);
  render();
}
const yzoomed = () => yz() < 0.999;
/* how tall a panel is drawn, as a multiple of its default */
const gh = () => Math.max(0.6, Math.min(2.5, +state.gheight || 1));
function setHeight(pct){
  state.gheight = Math.max(0.6, Math.min(2.5, (parseInt(pct, 10) || 100) / 100));
  ["gheight", "ggheight"].forEach(id => {
    const e = el(id); if(e) e.value = Math.round(gh() * 100);
  });
  render();
}
/* one button back to the default view: time, value and height */
function resetView(){
  state.from = state.to = null;
  el("from").value = ""; el("to").value = "";
  state.yzoom = 1; state.ycenter = 0.5; state.gheight = 1;
  ["gheight", "ggheight"].forEach(id => { const e = el(id); if(e) e.value = 100; });
  state.rows = 300;
  render();
}
/* Shift-drag slides the frame without changing its size.  The matrix is left
   alone while the pointer is down - redrawing 300 rows x 20 cells per frame is
   what would make it feel like treacle - and one full render lands on release. */
let FAST = false;
function renderFast(){
  FAST = true;
  try { render(); } finally { FAST = false; }
}
function drawZoomBars(){
  const cur = viewMs(), full = fullRangeMs(), ax = axisModel();
  const fr = frame();
  let pts = 0;
  if(fr && ax){
    const [a, b] = timeIdx(fr.f);
    for(let i = a; i <= b; i++) if(dayOk(fr.f.time[i])) pts++;
  }
  /* With days left out, "40% of the range" would be meaningless - say which
     blocks are on screen and how many readings they hold. */
  const label = (ax && ax.split)
    ? tx("n.zoomdays", {days: ax.segs.map(g => g.label).join(" · "),
                        n: ax.segs.length, pts: pts})
    : ((cur && full && full[1] > full[0])
        ? tx("n.zoomspan", {from: shortMs(cur[0]), to: shortMs(cur[1]),
            pct: Math.max(1, Math.round(((cur[1] - cur[0]) / (full[1] - full[0])) * 100))})
        : "");
  ["zspan", "gzspan"].forEach(id => { const e = el(id); if(e) e.textContent = label; });
  const dirty = zoomed() || yzoomed() || gh() !== 1;
  ["zall", "gzall"].forEach(id => { const e = el(id); if(e) e.disabled = !dirty; });
  ["yup", "gyup", "ydown", "gydown"].forEach(id => {
    const e = el(id); if(e) e.disabled = !yzoomed();   /* nothing to move into */
  });
  ["gheight", "ggheight"].forEach(id => {
    const e = el(id); if(e) e.value = Math.round(gh() * 100);
  });
}
/* A <select> or a slider keeps the keyboard focus, and then the arrow keys
   change ITS value instead of moving the cursor along the chart - which is how
   the readout stopped responding to the arrows after switching its mode.  Every
   control in these bars hands the keyboard back when it is done. */
const giveBack = ev => { if(ev && ev.target && ev.target.blur) ev.target.blur(); };
[["gheight", "ggheight"]].forEach(pair => {
  pair.forEach(id => {
    const e = el(id); if(!e) return;
    e.oninput = ev => setHeight(ev.target.value);   /* live while dragging */
    e.onchange = giveBack;                          /* released - hand it back */
  });
});
[["tipmode", "gtipmode"], ["tipclear", "gtipclear"]].forEach(pair => {
  pair.forEach(id => {
    const e = el(id); if(!e) return;
    e.oninput = ev => {
      if(id.indexOf("mode") >= 0) state.tipMode = ev.target.value;
      else state.tipClear = (parseInt(ev.target.value, 10) || 0) / 100;
      applyTipStyle();
      if(state.tipMode === "off") hideTips();
    };
    e.onchange = giveBack;
  });
});
[["zin",  "gzin",   () => zoomBy(0.6, cursorAnchor())],
 ["zout", "gzout",  () => zoomBy(1 / 0.6, cursorAnchor())],
 ["zleft", "gzleft", () => panBy(-0.25)],
 ["zright", "gzright", () => panBy(0.25)],
 ["zall", "gzall",  () => resetView()],
 ["yin",  "gyin",   () => yZoomBy(0.6, null)],
 ["yout", "gyout",  () => yZoomBy(1 / 0.6, null)],
 ["yup",   "gyup",   () => yPanBy(0.25)],
 ["ydown", "gydown", () => yPanBy(-0.25)]
].forEach(row => {
  [row[0], row[1]].forEach(id => { const e = el(id); if(e) e.onclick = row[2]; });
});

/* ---------- legend + kpis ---------- */
function drawLegend(){
  const box=el("legend"); box.innerHTML="";
  const m=DATA.metrics[state.metric]; if(!m) return;
  m.entities.forEach(e=>{
    const c=document.createElement("div"); c.className="chip"+(state.off.has(e)?" off":"");
    c.innerHTML=`${chipMark(e)}${e}`;
    c.onclick=()=>{ state.off.has(e)?state.off.delete(e):state.off.add(e); render(); };
    box.appendChild(c);
  });
  const rl=el("ruleLegend"); rl.innerHTML="";
  (DATA.legend||[]).forEach(r=>{
    const s=document.createElement("div");
    s.innerHTML=`<span class="sw" style="background:${r.color}"></span>${half(r.label)}`;
    rl.appendChild(s);
  });
}
function drawKpis(){
  const box=el("kpis"); box.innerHTML="";
  const fr=frame(); if(!fr) return;
  const ents=visibleEntities(); const [a,b]=timeIdx(fr.f);
  const stats=ents.map(e=>{
    const s=fr.f.series[e]; let n=0,sum=0,mx=-Infinity,mn=Infinity,z=0;
    for(let i=a;i<=b;i++){const v=s[i];
      if(v===null||!dayOk(fr.f.time[i]))continue; n++;sum+=v;
      if(v>mx)mx=v; if(v<mn)mn=v; if(v===0)z++;}
    return {e:e,n:n,mean:n?sum/n:null,max:n?mx:null,min:n?mn:null,zero:z};
  }).filter(s=>s.n);
  if(!stats.length) return;
  const unit = state.view==="deviation" ? (fr.m.deviation_percent?"%":fr.m.unit) : fr.m.unit;
  const best=stats.reduce((p,c)=>c.mean>p.mean?c:p);
  const worst=stats.reduce((p,c)=>c.mean<p.mean?c:p);
  const spread = best.mean-worst.mean;
  const items=[
    [tx("k.samples"), stats.reduce((s,c)=>s+c.n,0), ""],
    [tx("k.best"), `${best.e}: ${fmt(best.mean)}`, unit],
    [tx("k.worst"), `${worst.e}: ${fmt(worst.mean)}`, unit],
    [tx("k.spread"), fmt(spread), unit],
    [tx("k.zeros"), stats.reduce((s,c)=>s+c.zero,0), ""],
  ];
  items.forEach(([k,v,u])=>{
    const d=document.createElement("div"); d.className="kpi";
    d.innerHTML=`<div class="k">${k}</div><div class="v">${v}</div><div class="u">${u||"&nbsp;"}</div>`;
    box.appendChild(d);
  });
}

/* ---------- matrix table ---------- */
/* position of a value inside the current scale, 0..1 */
function scalePos(v,lo,hi,dev){
  if(dev){
    const m=Math.max(Math.abs(lo),Math.abs(hi))||1;
    return 0.5 + 0.5*Math.max(-1,Math.min(1,v/m));
  }
  if(state.bounds==="numbers" && state.stops.length>1){
    const s=state.stops;
    if(v<=s[0]) return 0;
    if(v>=s[s.length-1]) return 1;
    for(let i=0;i<s.length-1;i++){
      if(v<=s[i+1]) return (i + (v-s[i])/((s[i+1]-s[i])||1)) / (s.length-1);
    }
    return 1;
  }
  return hi>lo ? (v-lo)/(hi-lo) : 0;
}
/* which of the discrete bands a value falls in (Excel's 5-band behaviour) */
function bandIndex(v){
  const s=state.stops, n=activeColors().length;
  if(!s.length) return 0;
  let i=0;
  for(let k=0;k<s.length;k++){ if(v>=s[k]) i=k; }
  return Math.min(n-1, i);
}
function heat(v,lo,hi,dev){
  if(v===null || state.cmode==="off") return "";
  const cols=activeColors();
  const st=Math.max(0.1,Math.min(1,state.strength/100));
  /* fixed numeric steps paint solid bands, exactly like the Excel rule */
  const banded = !dev && state.bounds==="numbers" && state.stops.length>1;
  const rgb = banded ? hex2rgb(cols[bandIndex(v)])
                     : ramp(cols, scalePos(v,lo,hi,dev));
  const shown=towardSurface(rgb,st);
  const base=`background:${rgb2hex(shown)};color:${inkOn(shown)}`;
  if(!state.bars) return base;
  const p=Math.round(scalePos(v,lo,hi,dev)*100);
  return `background:linear-gradient(90deg, ${state.barColor} 0 ${p}%,`
       + ` ${rgb2hex(shown)} ${p}% 100%);color:${inkOn(shown)}`;
}
/* The matrix gradient may be pinned to fixed kW thresholds, which say nothing
   about a percentage or a point difference, so the diagnostic tables get their
   own scale: deviation columns diverge around zero, the rest run min->max. */
function heatLocal(v, lo, hi, diverging){
  if(v === null || state.cmode === "off") return "";
  const cols = activeColors();
  const st = Math.max(0.1, Math.min(1, state.strength / 100));
  let t;
  if(diverging){
    const m = Math.max(Math.abs(lo), Math.abs(hi)) || 1;
    t = 0.5 + 0.5 * Math.max(-1, Math.min(1, v / m));
  } else {
    t = hi > lo ? (v - lo) / (hi - lo) : 0.5;
  }
  const shown = towardSurface(ramp(cols, t), st);
  return `background:${rgb2hex(shown)};color:${inkOn(shown)}`;
}
function drawMatrix(){
  const t=el("matrix"); if(!SECTIONS.has("matrix")) return; t.innerHTML="";
  const fr=frame(); if(!fr){ return; }
  const ents=visibleEntities(); const [a,b]=timeIdx(fr.f);
  const dev = state.view==="deviation" && fr.m.deviation;
  let lo=Infinity,hi=-Infinity;
  ents.forEach(e=>{const s=fr.f.series[e];
    for(let i=a;i<=b;i++){const v=s[i];
      if(v===null||!dayOk(fr.f.time[i]))continue;
      if(v<lo)lo=v; if(v>hi)hi=v;}});
  const head=document.createElement("tr");
  head.innerHTML=`<th>${tx("m.time")}</th>`+ents.map(e=>`<th>${e}</th>`).join("")
    + `<th>${dev?tx("m.maxdev"):tx("m.spread")}</th>`;
  t.appendChild(head);

  let shown=0, kept=0, inrange=0;
  for(let i=a;i<=b;i++){
    if(!dayOk(fr.f.time[i])) continue;
    inrange++;
    let mn=Infinity,mx=-Infinity,mad=0,allzero=true,any=false;
    ents.forEach(e=>{ const v=fr.f.series[e][i];
      if(v===null) return; any=true; if(v!==0) allzero=false;
      if(v<mn)mn=v; if(v>mx)mx=v; mad=Math.max(mad,Math.abs(v)); });
    const spread = (isFinite(mn)&&isFinite(mx)) ? mx-mn : 0;
    const metricVal = dev ? mad : spread;
    if(!any) continue;
    if(state.hidezero && allzero) continue;
    if(state.minspread && metricVal < state.minspread) continue;
    kept++;
    if(shown>=state.rows) continue;
    shown++;
    const tr=document.createElement("tr");
    let cells=`<td>${fr.f.time[i]||""}</td>`;
    ents.forEach(e=>{
      const v=fr.f.series[e][i];
      let cls="", style="";
      if(v===null) cls="nan";
      else if(v===0 && state.zeroOn){
        const z=hex2rgb(state.zeroColor);
        style=`background:${rgb2hex(z)};color:${inkOn(z)}`;
      }
      else style=heat(v,lo,hi,dev);
      cells+=`<td class="${cls}" style="${style}">${v===null?"—":fmt(v)}</td>`;
    });
    cells+=`<td>${fmt(metricVal)}</td>`;
    tr.innerHTML=cells; t.appendChild(tr);
  }
  el("matrixNote").textContent =
    (state.days.size ? daysOn().join(" · ") + " · " : "") +
    tx("n.rows", {shown:shown, kept:kept, range:inrange}) +
    (fr.f.step>1 ? " · " + tx("n.thin", {step:fr.f.step}) : "");
  el("more").style.display = shown<kept ? "" : "none";
}

/* ---------- simple tables ---------- */
function simpleTable(id,payload,cardId){
  const t=el(id); if(!t) return;
  if(!payload){ if(cardId) el(cardId).classList.add("hidden"); return; }
  t.innerHTML="";
  const head=document.createElement("tr");
  head.innerHTML=payload.columns.map(c=>`<th>${tcol(c)}</th>`).join("");
  t.appendChild(head);
  payload.rows.forEach(r=>{
    const tr=document.createElement("tr");
    tr.innerHTML=r.map(v=>`<td>${v===null?"":(typeof v==="number"?fmt(v):tcol(v))}</td>`).join("");
    t.appendChild(tr);
  });
}

/* ---------- render ---------- */
function render(){
  const fr=frame();
  if(fr){
    const unit = state.view==="deviation"
      ? (fr.m.deviation_percent?"%":fr.m.unit) : fr.m.unit;
    el("chartTitle").textContent = fr.m.title + (unit? ` [${unit}]`:"") +
      (state.view==="deviation"
        ? " — " + tx("n.devtitle", {mode:fr.m.reference_mode}) : "");
    const hidden = fr.m.entities.length - visibleEntities().length;
    const [ia,ib] = timeIdx(fr.f);
    const inView = Math.max(0, ib-ia+1);
    const narrowed = inView < fr.f.time.length;
    el("chartNote").textContent =
      (state.view==="deviation" && !fr.m.deviation ? tx("n.nodev") : "") +
      (state.days.size ? daysOn().join(" · ") + " · " : "") +
      (narrowed ? tx("n.inview", {n:inView, total:fr.f.time.length}) + " · " : "") +
      tx("n.src", {rows:fr.f.rows_total}) +
      (fr.f.step>1 ? tx("n.plotted", {step:fr.f.step}) : "") +
      (hidden>0 ? " · " + tx("n.hidden", {n:hidden}) : "") +
      (state.view==="deviation" ? " · " + tx("n.devfloor") : "") +
      (fr.m.chosen===false ? " · " + tx("n.extra") : "");
  }
  drawDayTabs(); syncRange();
  if(!FAST){ drawFindings(); drawDiagTable(); drawScatter(); }
  buildChannelChips();
  buildEntities(); drawKpis(); drawChart(); drawGraphs(); drawZoomBars();
  applyTipStyle();
  if(state.layout === "single") drawLegend();
  if(!FAST) drawMatrix();
  if(STYLE.controls && !FAST) drawScaleBar();
}
applyLang();
applySections();
syncWorstLabel();
if(!RESTORED) autoSelect();     /* a saved view keeps the units it was saved with */
buildMetricList();
syncControls();                 /* ... and its controls have to show them */
if(STYLE.controls) syncColorInputs();
/* ---------- diagnostics ---------- */
const DIAG_TABS = [["attribution","t.attribution"], ["availability","t.availability"],
                   ["efficiency","t.efficiency"], ["performance","t.performance"]];
function availableDiag(){ return DIAG_TABS.filter(([k]) => DATA[k] && DATA[k].rows.length); }
function drawFindings(){
  const card = el("findingsCard");
  const lines = DATA.findings || [];
  if(!lines.length){ card.classList.add("hidden"); return; }
  card.classList.remove("hidden");
  const box = el("findings"); box.innerHTML = "";
  lines.forEach(line => {
    const li = document.createElement("li");
    li.textContent = line;
    if(/OK|中央値に近い|tracks the fleet/.test(line)) li.className = "ok";
    box.appendChild(li);
  });
  el("findingsNotes").textContent = (DATA.notes || []).join(" · ");
}
function drawDiagTable(){
  const avail = availableDiag();
  const card = el("diagCard");
  if(!avail.length){ card.classList.add("hidden"); return; }
  card.classList.remove("hidden");
  if(!state.diag || !avail.some(([k]) => k === state.diag)) state.diag = avail[0][0];
  const tabs = el("diagTabs"); tabs.innerHTML = "";
  avail.forEach(([key, label]) => {
    const b = document.createElement("button");
    b.className = "daytab" + (state.diag === key ? " on" : "");
    b.textContent = tx(label);
    b.onclick = () => { state.diag = key; drawDiagTable(); };
    tabs.appendChild(b);
  });
  const payload = DATA[state.diag];
  const t = el("diagTable"); t.innerHTML = "";
  const head = document.createElement("tr");
  head.innerHTML = payload.columns.map(c => `<th>${tcol(c)}</th>`).join("");
  t.appendChild(head);
  payload.rows.forEach(r => {
    const tr = document.createElement("tr");
    tr.innerHTML = r.map((v, i) => {
      const col = payload.columns[i];
      let style = "";
      if(typeof v === "number" && /vs fleet|Availability|PR|Efficiency %|Lost kWh/.test(col)){
        const vals = payload.rows.map(x => x[i]).filter(n => typeof n === "number");
        const lo = Math.min(...vals), hi = Math.max(...vals);
        /* "vs fleet" is a signed deviation: zero is the neutral middle.
           Lost kWh is the other way round - more is worse. */
        const dev = /vs fleet/.test(col);
        const worse = /Lost kWh/.test(col);
        if(dev)        style = heatLocal(v, lo, hi, true);
        else if(worse) style = heatLocal(-v, -hi, -lo, false);
        else if(hi > lo) style = heatLocal(v, lo, hi, false);
      }
      const txt = v === null ? "" : (typeof v === "number" ? fmt(v) : half(tcol(v)));
      return `<td style="${style}">${txt}</td>`;
    }).join("");
    t.appendChild(tr);
  });
  el("diagNote").textContent =
    state.diag === "attribution"
      ? tx("n.attribution")
      : (payload.truncated ? tx("n.truncated", {n: payload.rows.length,
                                                total: payload.rows_total}) : "");
}
/* ---------- scatter: power against irradiance ---------- */
function drawScatter(){
  const card = el("scatterCard");
  const data = DATA.scatter;
  if(!data || !data.series){ card.classList.add("hidden"); return; }
  card.classList.remove("hidden");
  const svg = el("scatter"); svg.innerHTML = "";
  const units = Object.keys(data.series).filter(u => !state.off.has(u));
  const W = Math.max(680, el("scatterwrap").clientWidth), H = 360;
  const L = 60, R = 16, T = 12, B = 38;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("height", H);
  let xmax = 0, ymax = 0;
  units.forEach(u => data.series[u].forEach(([x, y]) => {
    if(x > xmax) xmax = x; if(y > ymax) ymax = y; }));
  if(!(xmax > 0 && ymax > 0)) return;
  const x = v => L + (v / xmax) * (W - L - R);
  const y = v => T + (1 - v / ymax) * (H - T - B);
  const NS = "http://www.w3.org/2000/svg";
  const add = (t, at) => { const e = document.createElementNS(NS, t);
    for(const k in at) e.setAttribute(k, at[k]); svg.appendChild(e); return e; };
  niceTicks(0, ymax, 5).forEach(v => {
    add("line", {x1:L, x2:W-R, y1:y(v), y2:y(v), stroke:css("--grid"), "stroke-width":1});
    const t2 = add("text", {x:L-8, y:y(v)+4, fill:css("--muted"), "font-size":11,
                            "text-anchor":"end"}); t2.textContent = fmt(v);
  });
  niceTicks(0, xmax, 5).forEach(v => {
    const t2 = add("text", {x:x(v), y:H-14, fill:css("--muted"), "font-size":11,
                            "text-anchor":"middle"}); t2.textContent = fmt(v);
  });
  add("line", {x1:L, x2:W-R, y1:H-B, y2:H-B, stroke:css("--axis"), "stroke-width":1});
  units.forEach(u => {
    const c = entityColor(u);
    const pts = data.series[u];
    let d = "";
    for(let i = 0; i < pts.length; i++){
      const [px, py] = pts[i];
      d += `M${x(px).toFixed(1)} ${y(py).toFixed(1)}h0.9`;
    }
    add("path", {d:d, stroke:c, "stroke-width":2.4, "stroke-linecap":"round",
                 fill:"none", opacity:0.55});
  });
}
/* ---------- exports ---------------------------------------------------------
   The report is one offline file, so everything here is done in the browser:
   no library, no server, no network.                                        */
function download(blob, name){
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}
const stamp = () => new Date().toISOString().slice(0,16).replace(/[:T]/g, "-");
/* Strip only what a filesystem rejects: \w would delete every Japanese
   character and turn "⑩交流電力 [kW]" into "_kW_". */
const safeName = t => String(t || "chart").replace(/[\\/:*?"<>|]+/g, "")
                        .replace(/\s+/g, "_").replace(/_+/g, "_")
                        .replace(/^_|_$/g, "").slice(0, 80) || "chart";

/* --- the page, exactly as it looks now --- */
function serialiseState(){
  const out = {};
  for(const k in state){
    const v = state[k];
    out[k] = (v instanceof Set) ? [...v] : v;
  }
  return out;
}
function saveView(){
  const clone = document.documentElement.cloneNode(true);
  /* drop the rendered content - the script rebuilds all of it on load, and
     keeping it would bloat the file every time it is saved again */
  ["matrix","chart","stackwrap","graphwrap","btip","gnote","daily","zeros",
   "alarms","quality","diagTable","scatter","entities","legend","chanChips",
   "daytabs","diagTabs","kpis","findings","swatches","scaleBar",
   "ruleLegend"].forEach(id => {
    const e = clone.querySelector("#" + id); if(e) e.innerHTML = "";
  });
  const old = clone.querySelector("#savedState"); if(old) old.remove();
  const sc = document.createElement("script");
  sc.id = "savedState";
  sc.textContent = "window.__STATE__=" + JSON.stringify(serialiseState()) + ";";
  clone.querySelector("head").appendChild(sc);
  const html = "<!DOCTYPE html>\n" + clone.outerHTML;
  download(new Blob([html], {type:"text/html;charset=utf-8"}),
           `${safeName(DATA.title || "report")}_${stamp()}.html`);
}

/* --- charts as a raster image --- */
/* Who is which colour has to travel with the picture - a PNG pasted into a
   report with no legend is unreadable. */
function legendItems(){
  if(state.layout === "overlay"){
    return selectedChannels().map((ci, k) => ({
      label: DATA.metrics[ci].title + (DATA.metrics[ci].unit ? ` [${DATA.metrics[ci].unit}]` : ""),
      color: css(SERIES_VARS[k % SERIES_VARS.length]), dash: ""
    }));
  }
  return visibleEntities().map(e => ({label:e, color:entityColor(e), dash:entityDash(e)}));
}
/* Japanese labels are twice as wide as latin ones and a reference line like
   "日射量 1.216 kW/m2 = 100%" is wider still, so the columns are measured, not
   assumed - a legend that overlaps itself is worse than no legend. */
function textWidth(s2, size){
  let w = 0;
  for(const ch of String(s2)) w += HAS_CJK.test(ch) ? size : size * 0.56;
  return w;
}
function appendLegend(clone, W, H, items){
  if(!items || !items.length) return H;
  const size = 12, lh = 20, pad = 10, left = 62, gap = 22, mark = 26;
  const rows = [[]];
  let x = left;
  items.forEach(it => {
    const w = mark + textWidth(it.label, size);
    if(x > left && x + w > W - 16){ rows.push([]); x = left; }
    rows[rows.length - 1].push({it:it, x:x});
    x += w + gap;
  });
  let y = H + pad + 12;
  rows.forEach(row => {
    row.forEach(cell => {
      const ln = document.createElementNS(NSVG, "line");
      ln.setAttribute("x1", cell.x); ln.setAttribute("x2", cell.x + 20);
      ln.setAttribute("y1", y - 4); ln.setAttribute("y2", y - 4);
      ln.setAttribute("stroke", cell.it.color); ln.setAttribute("stroke-width", 3);
      if(cell.it.dash) ln.setAttribute("stroke-dasharray", cell.it.dash);
      clone.appendChild(ln);
      const tx2 = document.createElementNS(NSVG, "text");
      tx2.setAttribute("x", cell.x + mark); tx2.setAttribute("y", y);
      tx2.setAttribute("font-size", size);
      tx2.setAttribute("fill", css("--ink") || "#111");
      tx2.textContent = cell.it.label;
      clone.appendChild(tx2);
    });
    y += lh;
  });
  return H + pad + rows.length * lh + 6;
}
async function svgToCanvas(svgEl, scale, legend){
  const vb = (svgEl.getAttribute("viewBox") || "0 0 800 300").split(/\s+/).map(Number);
  const W = vb[2];
  let H = vb[3];
  const clone = svgEl.cloneNode(true);
  H = appendLegend(clone, W, H, legend);
  clone.setAttribute("viewBox", `0 0 ${W} ${H}`);
  clone.setAttribute("xmlns", NSVG);
  clone.setAttribute("width", W); clone.setAttribute("height", H);
  clone.setAttribute("style", 'font-family:system-ui,"Segoe UI","Yu Gothic",sans-serif');
  const bg = document.createElementNS(NSVG, "rect");
  bg.setAttribute("x", 0); bg.setAttribute("y", 0);
  bg.setAttribute("width", W); bg.setAttribute("height", H);
  bg.setAttribute("fill", css("--surface") || "#ffffff");
  clone.insertBefore(bg, clone.firstChild);
  const xml = new XMLSerializer().serializeToString(clone);
  const img = new Image();
  await new Promise((ok, no) => {
    img.onload = ok; img.onerror = no;
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(xml);
  });
  const cv = document.createElement("canvas");
  cv.width = Math.round(W * scale); cv.height = Math.round(H * scale);
  const ctx = cv.getContext("2d");
  ctx.fillStyle = css("--surface") || "#ffffff";
  ctx.fillRect(0, 0, cv.width, cv.height);
  ctx.drawImage(img, 0, 0, cv.width, cv.height);
  return cv;
}
async function exportChart(kind){
  const scale = 2;                       /* readable when pasted into a report */
  const svgs = (state.layout === "stack" || state.layout === "grid2")
    ? [...document.querySelectorAll("#stackwrap svg")]
    : [el("chart")];
  const items = legendItems();
  const cans = [];
  for(let i = 0; i < svgs.length; i++){
    const sv = svgs[i];
    if(!sv || !sv.getAttribute("viewBox")) continue;
    /* one legend for the whole image, under the last panel */
    cans.push(await svgToCanvas(sv, scale, i === svgs.length - 1 ? items : null));
  }
  if(!cans.length) return;
  let out = cans[0];
  if(cans.length > 1){                   /* stack the panels into one image */
    out = document.createElement("canvas");
    out.width = Math.max(...cans.map(c => c.width));
    out.height = cans.reduce((n, c) => n + c.height, 0);
    const ctx = out.getContext("2d");
    ctx.fillStyle = css("--surface") || "#ffffff";
    ctx.fillRect(0, 0, out.width, out.height);
    let y = 0;
    cans.forEach(c => { ctx.drawImage(c, 0, y); y += c.height; });
  }
  const type = kind === "jpg" ? "image/jpeg" : "image/png";
  const ext = kind === "jpg" ? "jpg" : "png";
  const title = safeName(el("chartTitle").textContent);
  out.toBlob(bl => { if(bl) download(bl, `${title}_${stamp()}.${ext}`); },
             type, kind === "jpg" ? 0.92 : undefined);
}

/* --- whichever table is on screen --- */
function tableToCsv(tableId, name){
  const rows = [...document.querySelectorAll(`#${tableId} tr`)].map(tr =>
    [...tr.children].map(td => {
      const t = (td.textContent || "").replace(/"/g, '""').trim();
      return /[",\n]/.test(t) ? `"${t}"` : t;
    }).join(","));
  if(!rows.length) return;
  download(new Blob(["\ufeff" + rows.join("\n")], {type:"text/csv;charset=utf-8"}),
           `${safeName(name)}_${stamp()}.csv`);
}

/* ---------- keyboard cursor ----------
   A pixel is several samples wide once a whole day is on screen, so the mouse
   cannot land on an exact minute.  The arrow keys can. */
let PAINT = null;
/* Two cards can hold charts, so the keys drive the one the mouse was last in.
   Each painter carries its own cursor accessor and its own sample step - one
   press has to be one reading, whichever card is active. */
function activePaint(){
  return state.focus === "build" ? (GPAINT || PAINT) : (PAINT || GPAINT);
}
function cursorStep(dir, mult){
  const P = activePaint(); if(!P) return;
  const cur = P.get();
  if(P.kind === "single"){
    P.fn((cur === null ? Math.round((P.lo + P.hi) / 2) : cur) + dir * mult);
  } else {
    let ms = (cur === null ? (P.lo + P.hi) / 2 : cur) + dir * mult * (P.step || 60000);
    if(P.snap) ms = P.snap(ms, dir);
    P.fn(ms);
  }
}
function cursorEnd(which){
  const P = activePaint(); if(!P) return;
  P.fn(which === "start" ? P.lo : P.hi);
}
function cursorClear(){
  state.cursor = null; state.cursorMs = null; state.gcursorMs = null;
  state.pinned = false; state.gpinned = false;
  el("tip").style.opacity = 0;
  el("btip").style.opacity = 0;
  drawChart(); drawGraphs();
}
el("chartCard").addEventListener("mouseenter", () => { state.focus = "main"; });
el("buildCard").addEventListener("mouseenter", () => { state.focus = "build"; });
addEventListener("keydown", ev => {
  const t = ev.target;
  if(t && /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName)) return;   /* let them type */
  const mult = ev.shiftKey ? 10 : (ev.ctrlKey || ev.metaKey ? 60 : 1);
  if(ev.key === "ArrowLeft"){ cursorStep(-1, mult); ev.preventDefault(); }
  else if(ev.key === "ArrowRight"){ cursorStep(1, mult); ev.preventDefault(); }
  else if(ev.key === "Home"){ cursorEnd("start"); ev.preventDefault(); }
  else if(ev.key === "End"){ cursorEnd("end"); ev.preventDefault(); }
  else if(ev.key === "Escape"){ cursorClear(); }
  else if(ev.key === "+" || ev.key === "="){ zoomBy(0.6, cursorAnchor()); ev.preventDefault(); }
  else if(ev.key === "-" || ev.key === "_"){ zoomBy(1/0.6, cursorAnchor()); ev.preventDefault(); }
  else if(ev.key === "0"){ resetView(); ev.preventDefault(); }
  else if(ev.key === "]"){ yZoomBy(0.6, null); ev.preventDefault(); }
  else if(ev.key === "["){ yZoomBy(1/0.6, null); ev.preventDefault(); }
  else if(ev.key === "PageUp"){ yPanBy(0.25); ev.preventDefault(); }
  else if(ev.key === "PageDown"){ yPanBy(-0.25); ev.preventDefault(); }
  else if(ev.key === "," || ev.key === "<"){ panBy(-0.25); ev.preventDefault(); }
  else if(ev.key === "." || ev.key === ">"){ panBy(0.25); ev.preventDefault(); }
});

/* Only the chart on screen, and only the data behind it.  The point is a small
   file: the pruned payload replaces the original one rather than sitting beside
   it. */
/* Which rows of a frame are on screen: the chosen days, inside the zoom. */
function keepRows(f){
  const idx = [];
  for(let k = 0; k < f.time.length; k++){
    const t = f.time[k];
    if(!dayOk(t)) continue;
    if(state.from && t < state.from) continue;
    if(state.to && t > state.to) continue;
    idx.push(k);
  }
  return idx;
}
/* A summary table that carries a Date column is cut to the same days - a
   one-day file whose 日別サマリー still listed two days would be a lie. */
function cutTable(tp){
  if(!tp || !tp.rows.length) return tp;
  const i = tp.columns.findIndex(c => /^(date|日付)$/i.test(String(c)));
  if(i < 0) return tp;
  const rows = tp.rows.filter(r => {
    const d = String(r[i] === null ? "" : r[i]).slice(0, 10);
    if(!d) return true;
    if(!dayOk(d)) return false;
    if(state.from && d < state.from.slice(0,10)) return false;
    if(state.to && d > state.to.slice(0,10)) return false;
    return true;
  });
  return {columns:tp.columns, rows:rows, truncated:tp.truncated, rows_total:rows.length};
}
function pruneMetric(m, ents){
  const cut = f => {
    if(!f) return null;
    const idx = keepRows(f);
    const series = {};
    ents.forEach(e => {
      if(f.series[e]) series[e] = idx.map(k => f.series[e][k]);
    });
    return {time:idx.map(k => f.time[k]), columns:ents.slice(), series:series,
            step:f.step, rows_total:idx.length};
  };
  return {key:m.key, title:m.title, unit:m.unit, entities:ents.slice(),
          shared:!!m.shared,
          value:cut(m.value), deviation:cut(m.deviation),
          deviation_percent:m.deviation_percent, reference_mode:m.reference_mode,
          cells:m.cells, chosen:m.chosen};
}
/* One file with one chart in it.  The pruned payload REPLACES the original -
   the point is a small file, not a copy sitting beside the full one. */
function writeSlim(slim, st, name){
  const clone = document.documentElement.cloneNode(true);
  ["matrix","chart","stackwrap","graphwrap","btip","gnote","daily","zeros",
   "alarms","quality","diagTable","scatter","entities","legend","chanChips",
   "daytabs","diagTabs","kpis","findings","swatches","scaleBar",
   "ruleLegend"].forEach(id => {
    const e = clone.querySelector("#" + id); if(e) e.innerHTML = "";
  });
  /* drop the full payload: it is one line, so this is exact */
  let replaced = false;
  clone.querySelectorAll("script").forEach(sc => {
    if(replaced || !sc.textContent.includes("const __PAYLOAD =")) return;
    sc.textContent = sc.textContent.split("\n").map(line =>
      line.startsWith("const __PAYLOAD =") ? "const __PAYLOAD = null;" : line
    ).join("\n");
    replaced = true;
  });
  clone.querySelectorAll("#savedState,#slimData").forEach(e => e.remove());
  const head = clone.querySelector("head");
  const d1 = document.createElement("script");
  d1.id = "slimData";
  d1.textContent = "window.__DATA_OVERRIDE__=" + JSON.stringify(slim) + ";";
  head.appendChild(d1);
  const d2 = document.createElement("script");
  d2.id = "savedState";
  d2.textContent = "window.__STATE__=" + JSON.stringify(st) + ";";
  head.appendChild(d2);
  const html = "<!DOCTYPE html>\n" + clone.outerHTML;
  download(new Blob([html], {type:"text/html;charset=utf-8"}),
           `${safeName(name)}_${stamp()}.html`);
}
function slimBase(extra){
  return Object.assign({
    app:DATA.app, title:DATA.title, subtitle:DATA.subtitle, generated:DATA.generated,
    lang:DATA.lang, worst_n:DATA.worst_n, zero_highlight:DATA.zero_highlight,
    legend:DATA.legend, style:DATA.style, has_extra:false, roles:DATA.roles || {},
    sections:["chart"], default_graphs:[],
    daily:null, zeros:null, quality:null, alarms:null,
    findings:[], notes:[], attribution:null, availability:null,
    efficiency:null, performance:null, scatter:null
  }, extra || {});
}
function saveChartOnly(){
  const chans = state.layout === "single" ? [state.metric] : selectedChannels();
  const ents = visibleEntities();
  if(!chans.length || !ents.length) return;
  const slim = slimBase({metrics: chans.map(i => pruneMetric(DATA.metrics[i], ents))});
  const st = serialiseState();
  st.metric = 0;                            /* indexes shifted after pruning */
  st.channels = chans.map((_, k) => k);
  st.off = [];                              /* every unit kept is a unit shown */
  st.scope = "all";
  st.graphs = [];                           /* the old indexes mean nothing now */
  st.gnote = "";
  st.from = null; st.to = null; st.days = [];   /* the rows are already cut */
  writeSlim(slim, st, chans.map(i => DATA.metrics[i].title).join("_"));
}
/* Only one of the extra graphs, with only the series it draws.  A plant-wide
   channel needs one column, not twenty identical ones. */
function saveGraphOnly(gi){
  const g = graphList()[gi];
  if(!g || !(g.channels || []).length) return;
  const ents = visibleEntities();
  const chans = g.channels.slice();
  const slim = slimBase({
    graph_only: true,
    metrics: chans.map(i => {
      const m = DATA.metrics[i];
      const keep = isShared(i) ? m.entities.slice(0, 1)
                               : (ents.length ? ents : m.entities);
      return pruneMetric(m, keep);
    })
  });
  const st = serialiseState();
  const perUnit = chans.findIndex(i => !isShared(i));
  st.metric = perUnit < 0 ? 0 : perUnit;
  st.channels = [];
  st.layout = "single";
  st.graphs = [{channels: chans.map((_, k) => k)}];
  st.gcols = 1;
  st.off = [];
  st.scope = "all";
  st.focus = "build";
  st.gnote = "";
  st.from = null; st.to = null; st.days = [];   /* the rows are already cut */
  writeSlim(slim, st, graphTitle(g));
}

/* The whole report - every card, channel and unit - holding only the days and
   the period on screen.  This is the answer to "send me just those days". */
function saveWindowOnly(){
  const slim = {};
  for(const k in DATA) slim[k] = DATA[k];
  slim.metrics = DATA.metrics.map(m => pruneMetric(m, m.entities));
  ["daily", "zeros", "alarms", "quality"].forEach(k => { slim[k] = cutTable(DATA[k]); });
  const st = serialiseState();
  st.from = null; st.to = null; st.days = [];
  st.rows = 300;
  const tag = (state.days.size ? daysOn().map(d => d.slice(5).replace("-", "")).join("_")
                               : (state.from ? state.from.slice(0,10) : "all"));
  writeSlim(slim, st, (DATA.title || "report") + "_" + tag);
}
el("windowBtn").onclick = () => saveWindowOnly();
el("chartOnlyBtn").onclick = () => saveChartOnly();
el("pngBtn").onclick = () => exportChart("png");
el("jpgBtn").onclick = () => exportChart("jpg");
el("printBtn").onclick = () => window.print();
el("saveBtn").onclick = () => saveView();
el("diagCsv").onclick = () => tableToCsv("diagTable", state.diag || "diagnostics");

function drawTables(){
  simpleTable("daily",DATA.daily,"dailyCard");
  simpleTable("zeros",DATA.zeros,"zeroCard");
  simpleTable("alarms",DATA.alarms,"alarmCard");
  simpleTable("quality",DATA.quality,"qualityCard");
}
drawTables();
render();
addEventListener("resize",()=>{drawChart(); drawGraphs(); drawScatter();});
</script>
</body>
</html>
"""
