"""DataScope entry point.

    python main.py                 launch the GUI
    python main.py --cli FILES...  run a profile without the GUI
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# make "core", "ui", "workers" importable when frozen or run from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent))


DICT_HINTS = ("項目名称", "項目名", "名称一覧", "dictionary", "dict", "item_name",
              "itemname", "columns", "mapping", "定義")


def find_dictionary(folders: list["Path"]) -> "Path | None":
    """Look for the column dictionary spreadsheet beside the data."""
    seen: set[Path] = set()
    for folder in folders:
        if folder in seen or not folder.is_dir():
            continue
        seen.add(folder)
        for pattern in ("*.xlsx", "*.xls", "*.xlsm", "*.csv"):
            for cand in sorted(folder.glob(pattern)):
                if cand.name.startswith("~$"):
                    continue
                low = cand.name.lower()
                if any(h.lower() in low for h in DICT_HINTS):
                    return cand
    return None


def run_cli(argv: list[str]) -> int:
    from core import pipeline
    from core.loader import discover_files
    from core.profile_io import load_profile, new_profile

    ap = argparse.ArgumentParser(prog="DataScope", description="SCADA/CSV -> Excel/HTML")
    ap.add_argument("--cli", action="store_true")
    ap.add_argument("inputs", nargs="*", help="files or folders")
    ap.add_argument("--profile", help="profile .json")
    ap.add_argument("--dict", dest="dictionary",
                    help="column dictionary file; omit or pass 'auto' to search "
                         "the input folder for one (CSV項目名称.xlsx etc.)")
    ap.add_argument("--out", help="output folder")
    ap.add_argument("--metrics", help="comma separated raw column names to compare")
    ap.add_argument("--no-html", action="store_true")
    ap.add_argument("--png", action="store_true")
    ap.add_argument("--only", choices=("all", "html", "analysis", "cleaned"),
                    help="which files to write: all (default), html, analysis or cleaned")
    ap.add_argument("--scope", metavar="cleaned,analysis,html",
                    help="content of each output: selected|visible|all, comma separated "
                         "in that order, e.g. 'visible,selected,all'")
    ap.add_argument("--worst", type=int, metavar="N",
                    help="how many units the HTML report opens with (default 6)")
    ap.add_argument("--lang", choices=("ja", "en", "both"),
                    help="language of the HTML report's interface (default ja)")
    ap.add_argument("--sections", metavar="chart,matrix,daily,zeros,alarms,quality",
                    help="which blocks the HTML report contains (default: all)")
    args = ap.parse_args(argv)

    profile = load_profile(Path(args.profile)) if args.profile else new_profile("cli")
    if args.dictionary and args.dictionary.lower() != "auto":
        profile.dictionary.path = args.dictionary
    if args.out:
        profile.output.out_dir = args.out
    if args.metrics:
        profile.analysis.metrics = [m.strip() for m in args.metrics.split(",") if m.strip()]
    if args.no_html:
        profile.output.write_html = False
    if args.png:
        profile.output.write_png = True
    if args.only:
        o = profile.output
        combos = {                 # cleaned, analysis, html, png
            "all": (True, True, True, o.write_png),
            "html": (False, False, True, False),
            "analysis": (False, True, False, False),
            "cleaned": (True, False, False, False),
        }
        (o.write_excel, o.combined_workbook, o.write_html, o.write_png) = combos[args.only]
    if args.scope:
        parts = [p.strip().lower() for p in args.scope.split(",")]
        names = ("cleaned_scope", "analysis_scope", "html_scope")
        for name, value in zip(names, parts):
            if value in {"selected", "visible", "all"}:
                setattr(profile.output, name, value)
            elif value:
                print(f"ignoring unknown scope '{value}' for {name}")
    if args.worst:
        profile.output.html_worst_n = max(1, args.worst)
    if args.sections:
        from core.models import HTML_SECTIONS
        want = [x.strip().lower() for x in args.sections.split(",") if x.strip()]
        bad = [x for x in want if x not in HTML_SECTIONS]
        if bad:
            print(f"unknown section(s): {', '.join(bad)}; "
                  f"valid: {', '.join(HTML_SECTIONS)}")
        keep = [x for x in want if x in HTML_SECTIONS]
        if keep:
            profile.output.html_sections = keep
    if args.lang:
        profile.output.html_language = args.lang
    elif profile.output.html_language == "auto":
        profile.output.html_language = "ja"      # no GUI to follow in --cli mode

    files: list[Path] = []
    folders: list[Path] = []
    for raw in args.inputs:
        p = Path(raw)
        if p.is_dir():
            folders.append(p)
            files.extend(discover_files(p))
        else:
            files.append(p)
    if not files:
        print("no input files")
        return 2

    # find the dictionary next to the data if it was not given
    if not profile.dictionary.path:
        search = folders or [f.parent for f in files]
        found = find_dictionary(search)
        if found is not None:
            profile.dictionary.path = str(found)
            print(f"dictionary: {found}")
        else:
            print("dictionary: none found - columns keep their original names")

    # keep the output folder out of the input list on repeated runs
    out_dir = Path(profile.output.out_dir) if profile.output.out_dir else None
    if out_dir is not None:
        files = [f for f in files if out_dir.resolve() not in f.resolve().parents]

    res = pipeline.run_job(
        files, profile,
        progress=lambda pct, msg: print(f"[{pct:3d}%] {msg}", flush=True),
    )
    for m in res.messages:
        print(m)
    print("\nOutput:")
    for p in res.outputs:
        print(" ", p)
    return 0


def run_gui() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle

    from ui.main_window import MainWindow

    class TooltipStyle(QProxyStyle):
        """Hold the pointer for a second before the help appears, and leave it
        on screen long enough to read a paragraph."""

        def styleHint(self, hint, option=None, widget=None, data=None):  # noqa: N802
            if hint == QStyle.SH_ToolTip_WakeUpDelay:
                return 1000          # ms before it shows
            if hint == QStyle.SH_ToolTip_FallAsleepDelay:
                return 400
            return super().styleHint(hint, option, widget, data)

    if hasattr(Qt, "AA_EnableHighDpiScaling"):          # Qt5 compatibility, harmless on Qt6
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setStyle(TooltipStyle(app.style()))
    app.setApplicationName("DataScope")
    app.setOrganizationName("DataScope")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    if "--cli" in sys.argv:
        raise SystemExit(run_cli(sys.argv[1:]))
    raise SystemExit(run_gui())
