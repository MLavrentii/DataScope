"""
====================================================================
 DataScope - one-click run (PyCharm friendly)
====================================================================

 1. Open the DataScope folder in PyCharm.
 2. Install the packages:  pandas  openpyxl  matplotlib
       PyCharm usually offers this in a yellow bar at the top.
       Otherwise: View > Tool Windows > Terminal, then
           python -m pip install pandas openpyxl matplotlib
 3. Edit DATA_FOLDER below to point at your data.
 4. Right-click this file > Run 'RUN_ME'.

 The graphical application is main.py - run that instead if you
 prefer the window with tabs.  This file does the same work without
 any user interface, which is handy for repeat runs and for PyCharm's
 console output.
====================================================================
"""

from __future__ import annotations

from pathlib import Path

# ============================ SETTINGS ============================ #

# Folder holding the SCADA exports (001_PCS監視_*.csv etc.).
# Use a raw string (r"...") so backslashes need no escaping.
DATA_FOLDER = r"C:\Users\ミンジアシビッリ　ラブレンチー\Desktop\シムレイション\青森米田\simulation\claude用"

# Where to write the results.  "" = a DataScope_out folder inside DATA_FOLDER.
OUT_FOLDER = ""

# Column dictionary.  "" = look for CSV項目名称.xlsx inside DATA_FOLDER.
DICTIONARY = ""

# Profile with the Aomori layout + colour rules.  "" = auto-detect everything.
PROFILE = "profiles/aomori_pcs.json"

# Only analyse files whose name contains this text.  "" = every file.
# Examples:  "001_PCS監視"   "2026-08"   "001_PCS監視_2026-08"
ONLY_FILES_CONTAINING = "001_PCS監視"

# Channels to compare across the 20 units (raw keys, see the Columns list
# printed below).  Leave empty to let DataScope choose the most variable ones.
#   Data01 直流電力 kW      Data04 直流電圧 V     Data10 交流電力 kW
#   Data05 直流電流 A       Data06 交流電力量 kWh  Data13 INV力率
METRICS = ["Data10", "Data06", "Data04", "Data13"]

MAKE_EXCEL = True        # <source>_cleaned.xlsx, one sheet per PCS
MAKE_HTML = True         # interactive single-file report
MAKE_PNG = True          # matplotlib charts, incl. the per-unit ranking
MAKE_ANALYSIS = True     # combined workbook: matrix + deviation + summary

# ================= nothing below needs editing ==================== #

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    missing = []
    for mod in ("pandas", "numpy", "openpyxl", "matplotlib"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        print("Missing packages:", " ".join(missing))
        print("Install them, then run again:")
        print(f"    {sys.executable} -m pip install " + " ".join(missing))
        return 2

    from core import pipeline
    from core.loader import discover_files
    from core.profile_io import load_profile, new_profile

    here = Path(__file__).resolve().parent
    data = Path(DATA_FOLDER)
    if not data.exists():
        print(f"DATA_FOLDER does not exist:\n    {data}")
        print("Edit DATA_FOLDER at the top of this file.")
        return 2

    prof_path = (here / PROFILE) if PROFILE else None
    if prof_path is not None and prof_path.exists():
        profile = load_profile(prof_path)
        print(f"profile   : {prof_path.name}  ({profile.name})")
    else:
        profile = new_profile("auto")
        print("profile   : none - everything auto-detected")

    if DICTIONARY:
        profile.dictionary.path = DICTIONARY
    else:
        import main as cli_main            # reuse the dictionary finder

        found = cli_main.find_dictionary([data])
        if found is not None:
            profile.dictionary.path = str(found)
    print(f"dictionary: {profile.dictionary.path or '(none - raw column names)'}")

    profile.output.out_dir = OUT_FOLDER or str(data / "DataScope_out")
    profile.output.write_excel = MAKE_EXCEL
    profile.output.write_html = MAKE_HTML
    profile.output.write_png = MAKE_PNG
    profile.output.combined_workbook = MAKE_ANALYSIS
    if METRICS:
        profile.analysis.metrics = list(METRICS)

    files = discover_files(data)
    if ONLY_FILES_CONTAINING:
        files = [f for f in files if ONLY_FILES_CONTAINING in f.name]
    if not files:
        print(f"\nNo matching files in {data}")
        if ONLY_FILES_CONTAINING:
            print(f"ONLY_FILES_CONTAINING = {ONLY_FILES_CONTAINING!r} - try \"\"")
        return 2

    print(f"input     : {len(files)} file(s) from {data}")
    print(f"output    : {profile.output.out_dir}")
    print("-" * 70)

    result = pipeline.run_job(
        files, profile,
        progress=lambda pct, msg: print(f"[{pct:3d}%] {msg}", flush=True),
    )

    print("-" * 70)
    for line in result.messages:
        print(line)

    if result.analysis is not None and result.analysis.daily is not None:
        daily = result.analysis.daily
        if not daily.empty and "Total" in daily.columns:
            print("\nRanking (total per unit, worst first):")
            for metric in daily["Metric"].dropna().unique()[:2]:
                sub = daily[daily["Metric"] == metric]
                totals = sub.groupby("Entity")["Total"].sum(min_count=1).dropna()
                if totals.empty:
                    continue
                mean = totals.mean()
                print(f"\n  {metric}   (fleet mean {mean:,.1f})")
                for unit, value in totals.sort_values().items():
                    dev = (value - mean) / (abs(mean) + 1e-9) * 100.0
                    mark = "  <<<" if dev < -3 else ""
                    print(f"    {unit:<8} {value:>12,.1f}   {dev:+6.1f} %{mark}")

    print("\nFiles written:")
    for path in result.outputs:
        print("  ", path)
    print("\nOpen the PCS_report_*.html first - it is the interactive one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
