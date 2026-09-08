"""Hover help.

One entry per field: what it does, and a concrete example from the kind of
data this tool is built for.  Kept apart from :mod:`ui.i18n` because these are
paragraphs, not labels, and they are looked up the same way.
"""

from __future__ import annotations

from .i18n import language

EN: dict[str, str] = {
    # ---- input -------------------------------------------------------- #
    "profile": "A profile stores everything on these five tabs: how files are read, "
               "the colour rules, which channels are compared and what gets written.\n\n"
               "Example: 'Aomori Yoneda PCS' knows that one file holds 20 units in "
               "689 columns.\nSave your own with 'Save as…' and reuse it next month.",
    "dict": "Spreadsheet that translates raw column codes into readable names.\n\n"
            "Example: CSV項目名称.xlsx turns PCS01_Data10 into ⑩交流電力 [kW].\n"
            "Three tables side by side on one sheet are handled; the CSV名称 / 項目名称 / "
            "単位 / 計測値種別 columns are found automatically.\n"
            "Leave empty and columns keep their original names.",
    "keepraw": "Writes the original code in a second header row, under the readable "
               "name.\n\nExample: row 1 = ⑩交流電力 [kW], row 2 = PCS01_Data10.\n"
               "Keep it on — it is how you check a value against the source file.",
    "files": "The SCADA exports to process. Drag a folder in.\n\n"
             "Two shapes work:\n"
             "  • one file per day holding every unit (001_PCS監視_2026-08-06.csv)\n"
             "  • one file per unit\n\n"
             "The dictionary file and previous *_cleaned.xlsx output are skipped "
             "automatically. A day exported as both .csv and .xlsx is read once.",
    "scan": "Reads the files and fills the Columns tab — nothing is written.\n\n"
            "Takes about 25 seconds per 689-column day. Do this before setting rules "
            "or charts, so the fields can offer the real channel names.",

    "autoload": "On the next start the file list, the dictionary, the output folder "
                "and the profile you were editing all come back - including rule and "
                "chart changes you never saved to a profile file.\n\n"
                "Stored in %APPDATA%\\DataScope\\session.json.",
    "autoscan": "Also reads those files immediately when the window opens, so the "
                "Columns tab is ready.\n\nCosts about 25 seconds per 689-column day, "
                "in the background - leave it off if you usually change the file list "
                "first.",
    "forget": "Clears the remembered files, folders and working profile, so the next "
              "start is empty. Profiles you saved with 'Save as…' are not touched.",

    # ---- columns ------------------------------------------------------ #
    "col.include": "Unticked columns are left out of everything: the Excel output, the "
                   "colour rules, the comparison and the charts.",
    "col.name": "Readable name used in the output header. Editing here beats the "
                "dictionary.\n\nExample: change ⑩交流電力 to 'AC power' for an English "
                "report. In a wide file the edit applies to that channel on all 20 units.",
    "col.unit": "Shown in the header and on chart axes, and used to decide which series "
                "may share one chart.\n\nExample: kW, kWh, V, A, ℃, kW/m2",
    "col.decimals": "Digits after the decimal point in Excel. Empty = chosen from the "
                    "data.\n\nExample: 0 for counters, 3 for 力率.",
    "col.filter": "Only columns whose name contains one of these stay visible. "
                  "Comma separated; matches the raw code and the readable name.\n\n"
                  "Example: Data10, Data06, 電圧  →  keeps ⑩交流電力, ⑥交流電力量, "
                  "④直流電圧 and ⑦交流電圧.\nPress ▾ to pick from what your files "
                  "actually contain.",
    "col.exclude": "Columns containing any of these are hidden, even if the box above "
                   "matched them.\n\nExample: ANN_, 通信異常  →  drops the 362 status "
                   "bits and the comms counters.",
    "col.keepfirst": "The timestamp / label column is never hidden — every other value "
                     "is read against it.",

    "col.keep": "Hides every column except the ones ticked right now.\n\n"
                "This is the manual route: tick the handful you care about (Ctrl+click "
                "the rows, or tick them one by one), press this, and everything else "
                "disappears - no pattern needed.",
    "col.invert": "Swaps ticked and unticked. Handy after 'Keep only ticked' when you "
                  "realise you wanted the other set.",
    "col.first": "Hides everything except the label / time column, so you can build a "
                 "selection up from nothing instead of paring 689 columns down.",
    "col.buttons": "Select all / none / numeric-only across every column at once. The "
                   "label / time column always stays visible.",

    # ---- rules -------------------------------------------------------- #
    "rules.kind": "Zero, blank, negative, threshold, between, gradient, data bar, "
                  "top/bottom, duplicate and text become real Excel conditional "
                  "formatting — they survive your sorting and editing.\n\n"
                  "Stuck value, sigma outlier and sudden jump cannot be expressed in "
                  "Excel, so they are computed here and painted as fixed colours.",
    "rules.scope": "Which columns this rule paints.\n\n"
                   "• All numeric columns — the usual choice\n"
                   "• Selected columns only — exactly what is ticked on the Columns tab\n"
                   "• Pattern — a regular expression on the name, e.g. 温度|TEMP",
    "rules.pick": "Copies whatever is ticked on the Columns tab into this rule and "
                  "switches the scope to 'Selected columns only'.",
    "rules.pattern": "Regular expression matched against the column name.\n\n"
                     "Examples:\n  通信異常    every comms-error counter\n"
                     "  Data1[034]  Data10, Data13 and Data14\n"
                     "  ^PCS0[1-5]  units 1 to 5 only",
    "rules.value": "The number to compare against.\n\n"
                   "Example: rule 'less than 0.9' on INV力率 marks every minute the "
                   "inverter ran at poor power factor.",
    "rules.value2": "Second number. Its meaning depends on the rule type:\n\n"
                    "• Between — the upper bound\n"
                    "• Outlier — how many sigma (4 is a good start)\n"
                    "• Stuck value — how many identical rows in a row (30 = half an "
                    "hour at 1-minute data)\n"
                    "• Sudden jump — the biggest step you accept between two rows",
    "rules.tolerance": "Treats near-zero as zero, for noisy sensors.\n\n"
                       "Example: 0.001 marks 0.0004 kW as zero; 0 marks only exact 0.",
    "rules.text": "Plain text the cell must contain (not a regular expression).\n\n"
                  "Example: 故障 on a status column marks every fault row.",
    "rules.rank": "How many cells to mark. With 'N is a percentage' ticked, 10 means "
                  "the top 10 %.\n\nExample: bottom 5 % of ⑩交流電力 shows the worst "
                  "minutes of the day.",
    "rules.fill": "Cell background for the cells this rule matches.",
    "rules.font": "Text colour for those cells. Dark red on light red reads well; "
                  "white on a strong fill works for alarms.",
    "rules.mode": "2 colours — smooth low→high gradient\n"
                  "3 colours — low → middle → high\n"
                  "5 bands — five fixed colours, one per value range\n\n"
                  "Excel's own gradient stops at three colours, so 5 bands is written "
                  "as five range rules. Use bands when you want 'green above 90, red "
                  "below 50' rather than a smooth blend.",
    "rules.bounds": "Where the colours start and stop.\n\n"
                    "• Column minimum and maximum — rescales itself to each column\n"
                    "• Percentiles — same, but ignores extreme outliers\n"
                    "• These numbers — your own fixed thresholds, so two files are "
                    "coloured on the same scale and can be compared",
    "rules.stops": "Your thresholds, low to high, comma separated.\n\n"
                   "Example for ⑩交流電力 on a 100 kW unit: 0, 25, 50, 75, 90\n"
                   "→ below 25 red, 25-50 orange, 50-75 yellow, 75-90 light green, "
                   "90 and above green.",

    # ---- analysis ----------------------------------------------------- #
    "an.metrics": "Channels compared between units at the same timestamp. Each one "
                  "gets a time × unit sheet plus a deviation sheet.\n\n"
                  "Good picks: ⑩交流電力 (kW, instant), ⑥交流電力量 (kWh per minute), "
                  "④直流電圧, INV力率.\nShared sensors such as 気温 and 日射量 are the "
                  "same value copied to all 20 units — comparing them says nothing.",
    "an.devmode": "What each unit is measured against, minute by minute.\n\n"
                  "• mean — the fleet average (default)\n"
                  "• median — ignores one broken unit dragging the average down\n"
                  "• max / best — measures everyone against the strongest unit",
    "an.floor": "Below dawn and after dusk the fleet average is near zero, and dividing "
                "by it turns a 0.001 kW difference into ±2000 %.\n\n"
                "Cells whose reference is below this share of the day's maximum are "
                "left blank instead. 2 % is a good default; 0 disables it.",
    "an.resample": "Averages the data onto a coarser grid before comparing.\n\n"
                   "Example: 5min turns 1440 rows per day into 288 — faster, smaller "
                   "files, and it lets files logged at different intervals be compared.",
    "an.skipshared": "Keeps sensors marked 共通 (one physical sensor copied to every "
                     "unit) out of the comparison, because their deviation is always 0.",

    # ---- charts ------------------------------------------------------- #
    "ch.title": "Name shown on the chart and used in the PNG file name.\n\n"
                "Example: 'AC power — all units'",
    "ch.x": "The horizontal axis. Normally the timestamp.\n\n"
            "Pick a data column instead and you get an X-versus-Y scatter — "
            "X = ③日射量(傾斜), Y = ⑩交流電力 draws the power-versus-irradiance cloud, "
            "where a weak unit sits visibly below the others.",
    "ch.y": "The values drawn. Press this button to take whatever is ticked in the "
            "channel list above.",
    "ch.group": "How many charts to produce.\n\n"
                "• One chart with everything — a single busy chart\n"
                "• One chart per channel — all 20 units on it (best for spotting a "
                "weak unit)\n"
                "• One chart per unit — all channels of that unit together\n"
                "• One chart per name pattern — you decide the grouping below",
    "ch.patterns": "One chart per entry, holding every column whose raw code or "
                   "readable name contains that text. Comma separated.\n\n"
                   "Example: Data10, Data06  →  two charts, one of AC power and one of "
                   "AC energy.\nExample: 交流電力, 直流電圧  →  same idea in Japanese.\n"
                   "Press ▾ to pick from the channels found in your files.",
    "ch.type": "Line for anything against time. Scatter when X is a data column. Bar "
               "for a handful of values such as daily totals.",
    "ch.norm": "Draws every series as a percentage of its own maximum.\n\n"
               "Use it to put channels with different units on one chart honestly — "
               "kW and kWh and ℃ together. Without it, series with different units are "
               "split into separate charts automatically.",

    # ---- output ------------------------------------------------------- #
    "out.dir": "Where results are written. Empty = beside the source files.\n\n"
               "Your source files are never modified.",
    "out.suffix": "Added to each source file name.\n\n"
                  "Example: _cleaned → 001_PCS監視_2026-08-06_cleaned.xlsx",
    "out.excel": "One workbook per source file. For a wide file: one sheet per unit "
                 "(PCS01…PCS20) plus PLANT and STATUS_ALARMS.",
    "out.combined": "One workbook comparing all units: the time × unit matrices, the "
                    "deviation sheets, the daily summary and the zero report.",
    "out.html": "A single self-contained HTML file — filters, hover charts, a colour "
                "matrix and CSV export. Works offline and can be emailed as one file.",
    "out.png": "Renders the charts as PNG images too, including the ranked bar chart "
               "that shows every unit against the fleet mean.",
    "out.charts": "Adds native Excel charts. They stay linked to the data, so they "
                  "update if you edit the numbers.",
    "out.split": "For a file holding every unit: one readable sheet per unit instead of "
                 "one sheet with 689 columns.",
    "out.points": "How many points per series survive into the HTML report. Beyond "
                  "this the data is thinned (with a visible note) so the page stays "
                  "quick.\n\nExample: 4000 covers three days of 1-minute data.",
    "out.htmlcolor": "Adds a \"Table colours\" panel to the HTML report: steps (2/3/5), "
                     "each colour, fixed thresholds, strength, the zero colour and data "
                     "bars - all starting from the rule you set on the Rules tab.\n\n"
                     "Nothing is sent anywhere; the page just repaints its own table.\n"
                     "\"Back to app setting\" undoes any experiment.\n\n"
                     "Turn this off for a report that must look exactly as generated.",
    "out.preset": "Sets the four tick boxes above in one click.\n\n"
                  "\u2022 Everything - cleaned workbooks + analysis workbook + HTML + PNGs\n"
                  "\u2022 HTML only - fastest: nothing is written to Excel at all\n"
                  "\u2022 Analysis only - one PCS_analysis_*.xlsx, no per-file workbooks\n"
                  "\u2022 Cleaned only - just the translated <source>_cleaned.xlsx files\n\n"
                  "The four outputs are independent, so any other combination works too - "
                  "for example analysis + HTML with no cleaned files.",
    "out.scope.cleaned": "How much of each source file lands in <source>_cleaned.xlsx.\n\n"
                         "\u2022 Selected metrics only - just the channels ticked on the "
                         "Analysis tab (plus the time column). Small file, quick to open.\n"
                         "\u2022 All visible columns - whatever the Columns tab left "
                         "visible. This is the normal choice.\n"
                         "\u2022 Everything in the file - ignores the column filter, so "
                         "hidden columns come back. Use it when you want an archive copy.\n\n"
                         "Example: filter set to \"Data\" -> \"All visible\" writes the 16 "
                         "PCS channels, \"Everything\" also writes the 362 ANN_* bits.",
    "out.scope.analysis": "How many channels get their own sheets in PCS_analysis_*.xlsx.\n\n"
                          "Each channel costs three sheets (values, deviation, chart), so "
                          "\"Everything\" on a 22-channel export means 66 sheets and a slow "
                          "file. \"Selected metrics only\" is the sensible default.\n\n"
                          "The cap below stops an accidental 600-sheet workbook.",
    "out.scope.html": "How much data travels inside the HTML report.\n\n"
                      "Leave this on \"Everything in the file\": the report is interactive, "
                      "so you pick the channel, the units and the time range inside the "
                      "page (Channel list -> All channels). Nothing is recomputed - one run "
                      "gives you every view.\n\n"
                      "Choose \"Selected metrics only\" when you need to email a small file, "
                      "or when the report must show one channel and nothing else.",
    "out.anmax": "Upper limit for the analysis workbook when its content is not "
                 "\"Selected metrics only\".\n\n"
                 "12 channels \u2248 36 sheets, which Excel still opens comfortably. "
                 "Channels left out are named in the log. Set 0 for no limit.",
    "out.worst": "With 20 PCS every line overlaps, so the report opens showing only the N "
                 "units that deviate most from the fleet.\n\n"
                 "This is just the starting view: inside the page a number box next to "
                 "\"Worst N\" changes it at any time (3, 4, 6, 10 \u2026), and All / None / "
                 "Invert or a click on a unit chip override it completely.",
    "an.period": "A performance test usually covers a few hours, not a whole day.\n\n"
                 "With this on, every output is trimmed to the window below before "
                 "anything is written - cleaned workbooks, the analysis workbook, the "
                 "HTML report and the charts all cover exactly that period, and the "
                 "daily totals and rankings are computed from it alone.\n\n"
                 "Off (default) = the whole content of the files.",
    "an.period.range": "Click the arrow for a month calendar; type or use the arrow keys "
                       "on the hour and minute fields.\n\n"
                       "After reading the files the calendar is clamped to the period they "
                       "actually cover, so you cannot pick a date with no data. Read the "
                       "files first (1. Input) and the two boxes start on the true first "
                       "and last timestamp.\n\n"
                       "Both ends are inclusive: 11:00 → 13:00 on minute data keeps 121 "
                       "rows, 11:00 and 13:00 included.",
    "an.period.all": "Snap both boxes back to the first and last timestamp in the loaded "
                     "files - the quickest way to undo a narrow window without "
                     "unticking the box.",
    "files.order": "The order in this list is the order the files are read and written "
                   "in, and it is remembered with the session.\n\n"
                   "\u2022 drag a row up or down, or use \u25b2 \u25bc for the selected rows\n"
                   "\u2022 Sort by name compares the digits as numbers, so "
                   "001_PCS\u76e3\u8996_2026-07-13 comes before 2026-08-06, and file2 "
                   "before file10\n"
                   "\u2022 'keep in name order' also sorts anything you add or drop in "
                   "later\n\n"
                   "The analysis groups by the timestamps inside the data, so the order "
                   "changes no result - it decides which cleaned workbook is written "
                   "first and how the log reads.",
    "out.features": "The extra analysis, and which file each result lands in.\n\n"
                    "\u2022 Fault attribution - splits each unit's shortfall into DC side "
                    "(array, strings, shading, soiling) and inverter side. On your "
                    "08-06/07 data this separates PCS01 (-13 % AC, -9 % on the DC side, "
                    "normal inverter -> array problem) from PCS03 (-8 % AC, DC side "
                    "normal, efficiency -1.2 pt -> the inverter). Two different site "
                    "visits, and the old report could not tell them apart.\n"
                    "\u2022 Availability - minutes with usable light but no output, priced "
                    "in kWh against what the fleet median made in the same minutes.\n"
                    "\u2022 Efficiency - DC to AC conversion, median per unit.\n"
                    "\u2022 Performance ratio - needs the nameplate capacity below.\n"
                    "\u2022 Findings - the same conclusions as short sentences.\n"
                    "\u2022 Irradiance + temperature graph - the extra graph the "
                    "report opens with, on the same time axis as the power chart, so "
                    "you can line the patterns up. Irradiance and temperature are "
                    "identical in all 20 PCS blocks, so each is one line in its own "
                    "colour and can be dropped onto any graph in the page. HTML "
                    "only.\n"
                    "\u2022 Scatter - power against irradiance as an X-Y cloud; a low "
                    "slope is a DC problem you can see at a glance, but it hides the "
                    "time of day, which is why the graph above replaced it as the "
                    "default. HTML only.\n"
                    "\u2022 Long-format table - one row per time/unit/metric, for "
                    "PivotTables. Excel only, and it can be large.\n\n"
                    "Nothing is computed unless a box in its row is ticked. In the report itself "
                    "the reader can add as many graphs as they like on top of these.",
    "an.stable": "A cloud edge crossing 20 inverters over a few seconds makes every "
                 "one of them read differently for reasons that have nothing to do "
                 "with their health, and at dawn a 2 % difference is a rounding "
                 "error on a tiny number.\n\n"
                 "With this on, units are compared only in minutes where the light "
                 "is above the threshold below AND steady across the window - on "
                 "your two-day data that is 850 of 1484 daylight minutes.\n\n"
                 "Off = every daylight minute, which is noisier.",
    "an.minirr": "Minutes below this irradiance are not used for comparison. Same "
                 "unit as the irradiance channel - for 日射量(傾斜) in kW/m2, 0.4 "
                 "means 400 W/m2.\n\n"
                 "Raise it for a stricter test, lower it if a cloudy period leaves "
                 "too few samples (the sample count is shown in the result table).",
    "an.stabletol": "How much the irradiance may swing across the window and still "
                    "count as steady. 5 % is a reasonable clear-sky test.\n\n"
                    "Set 0 to switch the steadiness test off and keep only the "
                    "minimum-irradiance filter.",
    "an.capacity": "The array's DC nameplate per unit, used for PR = energy / "
                   "(irradiation x capacity).\n\n"
                   "Use the DC nameplate of the array, not the inverter's AC rating - "
                   "they differ by 1.1-1.3x on most plants. Entering the AC rating "
                   "gives a PR above 1, which is impossible; the app warns you if "
                   "that happens.\n\n"
                   "0 = skip the performance ratio.",
    "out.sections": "Which blocks the report contains.\n\n"
                    "Unticking one does more than hide it: that data never enters the "
                    "file, so the report gets smaller. Measured on a two-day run: all "
                    "sections 7.36 MB, dropping the four tables 7.26 MB, keeping only "
                    "the tables 0.10 MB - the minute-by-minute series behind the chart "
                    "and the matrix are what make the file big.\n\n"
                    "Typical use: a report to hand to someone who only needs the "
                    "comparison - keep グラフ and 時刻ごとの比較, drop 読み込み情報 "
                    "and 状態・警報ビット.\n\n"
                    "At least one section is always kept; unticking everything falls "
                    "back to all of them.",
    "out.htmllang": "Language of the report's own interface - the labels, buttons, "
                    "table headers and notes inside the .html file.\n\n"
                    "\u2022 Same as this app - a Japanese session produces a Japanese "
                    "report (this is the default)\n"
                    "\u2022 \u65e5\u672c\u8a9e only / English only - fixed, whatever the app is set to\n"
                    "\u2022 \u65e5\u672c\u8a9e + English - the bilingual labels older reports used\n\n"
                    "Whoever opens the file can still change it with the \"Language\" box "
                    "in the top-left corner of the page, so this only sets what they see "
                    "first.\n\n"
                    "Channel names always come from CSV\u9805\u76ee\u540d\u79f0.xlsx and are "
                    "therefore already Japanese; this setting does not touch them.",
    "out.htmlextra": "Upper limit for the channels the HTML report carries beyond the "
                     "analysis set, so a 689-column file cannot produce a 100 MB page.\n\n"
                     "40 covers a typical PCS export (about 22 channels per unit). "
                     "Set 0 to carry none - the report then holds only the analysis "
                     "channels, whatever the scope above says.",
    "out.maxcf": "Excel slows down badly with conditional formatting on hundreds of "
                 "columns. This caps how many columns per sheet get colour rules.",
    "run": "Reads the files, writes every output ticked on this tab, and lists the "
           "results in the Log tab. Cancel stops cleanly at the next file.",
}

JA: dict[str, str] = {
    "profile": "5つのタブの設定（読み込み方法・色ルール・比較項目・出力）をまとめて保存します。\n\n"
               "例：「青森米田 PCS」は 1ファイルに20号機×689列が入っていることを知っています。\n"
               "「名前を付けて保存」で自分用を作れば来月もそのまま使えます。",
    "dict": "列コードを読める名称に変換する表です。\n\n"
            "例：CSV項目名称.xlsx が PCS01_Data10 → ⑩交流電力 [kW] に変換します。\n"
            "1シートに3つの表が横並びでも対応。CSV名称／項目名称／単位／計測値種別 は自動判定します。\n"
            "空欄なら元の列名のままです。",
    "keepraw": "読める名称の下（2行目）に元のコードを残します。\n\n"
               "例：1行目 ⑩交流電力 [kW]、2行目 PCS01_Data10。\n"
               "元ファイルと突き合わせるときに必要なので、有効のままを推奨します。",
    "files": "処理する SCADA 出力。フォルダごとドラッグできます。\n\n"
             "対応する形：\n  • 1日1ファイルに全号機（001_PCS監視_2026-08-06.csv）\n"
             "  • 号機ごとに1ファイル\n\n"
             "辞書ファイルと過去の *_cleaned.xlsx は自動で除外。同じ日の .csv と .xlsx は"
             "片方だけ読みます。",
    "scan": "ファイルを読み「列」タブを埋めます。出力はしません。\n\n"
            "689列の1日分でおよそ25秒。先にこれを実行すると、各入力欄が実際の項目名を"
            "候補として出せるようになります。",
    "autoload": "次回起動時に、ファイル一覧・辞書・出力フォルダ・編集中のプロファイルを"
                "そのまま復元します（プロファイルに保存していないルールやグラフの変更も含む）。\n\n"
                "保存先：%APPDATA%\\DataScope\\session.json",
    "autoscan": "起動時にそのファイルの読み込みまで自動で行い、「列」タブをすぐ使える状態に"
                "します。\n\n689列の1日分で約25秒（バックグラウンド処理）。毎回ファイルを"
                "入れ替える場合は無効のままが快適です。",
    "forget": "記憶しているファイル・フォルダ・作業中プロファイルを消去し、次回は空の状態で"
              "起動します。「名前を付けて保存」したプロファイルは消えません。",
    "col.include": "チェックを外した列は、Excel出力・色ルール・比較・グラフのすべてから外れます。",
    "col.name": "出力ヘッダーに使う名称。ここでの編集は辞書より優先されます。\n\n"
                "例：⑩交流電力 → AC power。横長ファイルでは20号機すべてに適用されます。",
    "col.unit": "ヘッダーとグラフ軸に表示され、同じグラフに載せてよいかの判定にも使います。\n\n"
                "例：kW, kWh, V, A, ℃, kW/m2",
    "col.decimals": "Excel での小数桁。空欄ならデータから自動決定。\n\n例：カウンタは0、力率は3。",
    "col.filter": "ここに書いた文字を含む列だけ表示します。カンマ区切り、元コードと表示名の"
                  "両方を検索します。\n\n"
                  "例：Data10, Data06, 電圧 → ⑩交流電力・⑥交流電力量・④直流電圧・⑦交流電圧 が残ります。\n"
                  "▾ を押すと実際のファイルにある項目から選べます。",
    "col.exclude": "ここに書いた文字を含む列は、上の条件に一致しても非表示にします。\n\n"
                   "例：ANN_, 通信異常 → 362個の状態ビットと通信異常カウンタを除外。",
    "col.keepfirst": "時刻・名称の列は常に残します。他のすべての値はこの列を基準に読むためです。",
    "col.keep": "いまチェックされている列だけを残し、他をすべて非表示にします。\n\n"
                "パターンを書かずに手動で選ぶ方法です。必要な列にチェックを入れて"
                "このボタンを押すだけです。",
    "col.invert": "チェックの有無を入れ替えます。「チェックのみ残す」の後で、"
                  "逆の方が欲しかったと気づいたときに便利です。",
    "col.first": "名称・時刻の列以外をすべて非表示にします。689列から削るのではなく、"
                 "ゼロから選び足したいときに使います。",
    "col.buttons": "全列を一括で 全選択／解除／数値列のみ に切り替えます。"
                   "名称・時刻の列は常に残ります。",
    "rules.kind": "ゼロ・空欄・負値・しきい値・範囲・グラデーション・データバー・上位下位・重複・"
                  "文字列は Excel の条件付き書式になります（並べ替えや編集をしても有効）。\n\n"
                  "固着・外れ値・急変は Excel では表現できないため、本アプリで計算し固定色で塗ります。",
    "rules.scope": "このルールを適用する列。\n\n• 数値列すべて — 通常はこれ\n"
                   "• 選択した列のみ — 「列」タブでチェックした列\n"
                   "• パターン — 列名の正規表現、例：温度|TEMP",
    "rules.pick": "「列」タブでチェック中の列をこのルールに取り込み、適用範囲を「選択した列のみ」に"
                  "切り替えます。",
    "rules.pattern": "列名に対する正規表現。\n\n例：\n  通信異常    通信異常カウンタすべて\n"
                     "  Data1[034]  Data10・Data13・Data14\n  ^PCS0[1-5]  1〜5号機のみ",
    "rules.value": "比較する数値。\n\n例：INV力率に「0.9 未満」を設定すると、力率が悪かった"
                   "分がすべて色付きになります。",
    "rules.value2": "2つ目の数値。ルール種類で意味が変わります。\n\n"
                    "• 範囲内 — 上限\n• 外れ値 — 何σか（まず4）\n"
                    "• 固着 — 同じ値が何行続いたら（1分データで30＝30分）\n"
                    "• 急変 — 許容する1行あたりの最大変化量",
    "rules.tolerance": "ノイズのあるセンサー用に、ほぼ0を0とみなします。\n\n"
                       "例：0.001 なら 0.0004 kW もゼロ扱い。0 なら完全な0のみ。",
    "rules.text": "セルに含まれる文字列（正規表現ではありません）。\n\n例：状態列に「故障」。",
    "rules.rank": "色を付けるセル数。「％として扱う」を有効にすると 10＝上位10％。\n\n"
                  "例：⑩交流電力の下位5％で、その日の最悪の分が分かります。",
    "rules.fill": "一致したセルの背景色。",
    "rules.font": "そのセルの文字色。薄い赤地に濃い赤が読みやすく、警報は濃い地に白が有効です。",
    "rules.mode": "2色 — 小→大のなめらかなグラデーション\n3色 — 小→中→大\n"
                  "5段階 — 範囲ごとに固定色\n\n"
                  "Excel のグラデーションは3色までなので、5段階は5つの範囲ルールとして書き出します。"
                  "「90以上は緑、50未満は赤」のように区切りたいときは5段階を選びます。",
    "rules.bounds": "色の始点と終点の決め方。\n\n• 列の最小値・最大値 — 列ごとに自動調整\n"
                    "• パーセンタイル — 同上、ただし極端な外れ値を無視\n"
                    "• 指定した数値 — 固定のしきい値。別ファイルでも同じ基準で色が付くので比較できます",
    "rules.stops": "しきい値を小さい順にカンマ区切りで。\n\n"
                   "例：100kW機の⑩交流電力なら 0, 25, 50, 75, 90\n"
                   "→ 25未満=赤、25〜50=橙、50〜75=黄、75〜90=薄緑、90以上=緑。",
    "an.metrics": "同じ時刻で号機間を比較する項目。項目ごとに 時刻×号機 の表と偏差表を作ります。\n\n"
                  "おすすめ：⑩交流電力（kW・瞬時）、⑥交流電力量（kWh・1分差分）、④直流電圧、INV力率。\n"
                  "気温・日射量は全号機に同じ値がコピーされているため比較しても意味がありません。",
    "an.devmode": "各号機を何と比べるか（1分ごと）。\n\n• mean — 全号機平均（既定）\n"
                  "• median — 1台の不調が平均を引き下げる影響を受けにくい\n"
                  "• max / best — 最も good な号機を基準にする",
    "an.floor": "日の出前・日没後は全号機平均がほぼ0で、そこで割ると 0.001kW の差が ±2000% に"
                "なります。\n\n基準値が最大値のこの割合未満の場合は空欄にします。既定2%、0で無効。",
    "an.resample": "比較前に粗い時間間隔へ平均化します。\n\n"
                   "例：5min なら1日1440行が288行に。処理が速く、ファイルも小さく、"
                   "記録間隔が違うファイル同士も比較できます。",
    "an.skipshared": "備考に「共通」とある（1つのセンサーを全号機にコピーしている）項目を比較から"
                     "外します。偏差が常に0になるためです。",
    "ch.title": "グラフに表示され、PNG のファイル名にも使われます。\n\n例：「交流電力 — 全号機」",
    "ch.x": "横軸。通常は時刻です。\n\nデータ列を選ぶとX対Yの散布図になります。"
            "X＝③日射量(傾斜)、Y＝⑩交流電力 にすると日射に対する出力の分布が描かれ、"
            "出力の低い号機が下側にはっきり離れて見えます。",
    "ch.y": "描く値。このボタンで、上の一覧でチェックした項目を取り込みます。",
    "ch.group": "グラフを何枚作るか。\n\n• すべてを1つに — 1枚に全部\n"
                "• 項目ごとに1枚 — 20号機を重ねる（不調な号機を見つけるのに最適）\n"
                "• 号機ごとに1枚 — その号機の全項目\n• 名前パターンごとに1枚 — 下で指定",
    "ch.patterns": "指定した文字を含む列を1枚にまとめます。カンマ区切りで、1項目につき1枚。\n\n"
                   "例：Data10, Data06 → 交流電力のグラフと交流電力量のグラフの2枚。\n"
                   "例：交流電力, 直流電圧 でも同じことができます。\n"
                   "▾ を押すとファイル内の項目から選べます。",
    "ch.type": "時系列は折れ線、Xがデータ列のときは散布図、日次合計など少数の値は棒。",
    "ch.norm": "各系列を自身の最大値に対する％で描きます。\n\n"
               "単位の違う項目（kW と kWh と ℃）を1枚に正しく並べたいときに使います。"
               "無効の場合、単位が違う系列は自動的に別グラフに分かれます。",
    "out.dir": "結果の出力先。空欄なら元ファイルと同じ場所。\n\n元ファイルは変更しません。",
    "out.suffix": "各ファイル名に付ける接尾辞。\n\n例：_cleaned → 001_PCS監視_2026-08-06_cleaned.xlsx",
    "out.excel": "ファイルごとの Excel。横長ファイルなら号機ごとのシート（PCS01〜PCS20）に"
                 "PLANT と STATUS_ALARMS が付きます。",
    "out.combined": "全号機を比較するまとめ Excel：時刻×号機の表、偏差表、日次サマリー、ゼロ値レポート。",
    "out.html": "1ファイルで完結する HTML。フィルタ・ホバーグラフ・色付き表・CSV出力付き。"
                "オフラインで開け、そのままメール添付できます。",
    "out.png": "グラフを PNG でも出力します。全号機を平均と比べる順位グラフも含みます。",
    "out.charts": "Excel 内にネイティブのグラフを作ります。数値を編集すると連動して更新されます。",
    "out.split": "全号機が入ったファイルを、689列の1シートではなく号機ごとの読みやすいシートに"
                 "分けます。",
    "out.points": "HTML レポートで1系列あたりに残す点数。これを超えると間引きます（注記が出ます）。\n\n"
                  "例：4000 は1分データ約3日分に相当します。",
    "out.maxcf": "数百列に条件付き書式を入れると Excel が重くなります。1シートあたりの上限です。",
    "out.htmlcolor": "HTML レポートに「表の配色」パネルを追加します。段階（2/3/5色）、各色、"
                     "しきい値、濃度、ゼロ値の色、データバーを、ルールタブの設定を初期値として"
                     "ページ内で変更できます。\n\n"
                     "データは送信されません。表を再描画するだけです。\n"
                     "「Back to app setting」で元の設定に戻せます。\n\n"
                     "生成時のままの見た目を保ちたい場合はオフにしてください。",
    "out.preset": "上の4つのチェックを1クリックで設定します。\n\n"
                  "\u2022 すべて：整形ブック＋分析ブック＋HTML＋PNG\n"
                  "\u2022 HTML のみ：最速。Excel は一切作成しません\n"
                  "\u2022 分析ブックのみ：PCS_analysis_*.xlsx だけ\n"
                  "\u2022 整形ブックのみ：翻訳済みの <source>_cleaned.xlsx だけ\n\n"
                  "4つの出力は独立しているので、他の組み合わせも自由です"
                  "（例：分析ブック＋HTML、整形ブックなし）。",
    "out.scope.cleaned": "<source>_cleaned.xlsx にどれだけ含めるかを決めます。\n\n"
                         "\u2022 選択した項目のみ：分析タブで選んだ項目＋時刻列だけ。軽いファイル。\n"
                         "\u2022 表示中のすべての列：列タブで非表示にしなかった列。通常はこれ。\n"
                         "\u2022 ファイル内のすべて：列フィルタを無視し、非表示の列も含めます。"
                         "保存用のコピーに向きます。\n\n"
                         "例：フィルタ「Data」の場合、「表示中」は16項目、"
                         "「すべて」は362個の ANN_* ビットも書き出します。",
    "out.scope.analysis": "PCS_analysis_*.xlsx に何項目分のシートを作るかを決めます。\n\n"
                          "1項目につき3シート（実測値・偏差・グラフ）増えるため、"
                          "22項目で「すべて」を選ぶと66シートになり動作が重くなります。"
                          "通常は「選択した項目のみ」が適切です。\n\n"
                          "下の上限で、うっかり600シートになるのを防ぎます。",
    "out.scope.html": "HTML レポートに同梱するデータ量です。\n\n"
                      "「ファイル内のすべて」を推奨します。レポートは対話的なので、"
                      "項目・号機・期間はページ内で選べます（項目一覧 → 全項目）。"
                      "再計算は不要で、1回の実行であらゆる表示が可能です。\n\n"
                      "メールで送る小さいファイルが必要な場合や、1項目だけを見せたい場合は"
                      "「選択した項目のみ」にしてください。",
    "out.anmax": "分析ブックの内容が「選択した項目のみ」以外のときの上限です。\n\n"
                 "12項目 \u2248 36シートで、Excel でも快適に開けます。"
                 "除外された項目はログに表示されます。0 で無制限。",
    "out.worst": "20号機では線が重なるため、レポートは平均から差の大きい N 号機だけを"
                 "表示して開きます。\n\n"
                 "これは初期表示にすぎません。ページ内の「Worst N」横の数値ボックスで"
                 "いつでも変更でき（3、4、6、10 …）、All / None / Invert や"
                 "号機チップのクリックで自由に切り替えられます。",
    "an.period": "性能試験は通常、1日全体ではなく数時間が対象です。\n\n"
                 "オンにすると、書き出す前にすべての出力を下の期間で切り取ります。"
                 "整形ブック・分析ブック・HTML レポート・グラフのすべてがその期間だけを"
                 "対象とし、日別合計や順位もその範囲のみで計算されます。\n\n"
                 "オフ（既定）＝ファイルの全内容。",
    "an.period.range": "矢印をクリックすると月カレンダーが開きます。時・分は入力または"
                       "矢印キーで変更できます。\n\n"
                       "ファイル読み込み後はカレンダーが実データの期間に制限されるため、"
                       "データのない日付は選べません。先に読み込む（1. 入力）と、"
                       "最初と最後の時刻が初期値になります。\n\n"
                       "両端を含みます：分データで 11:00 → 13:00 は 121 行"
                       "（11:00 と 13:00 を含む）。",
    "an.period.all": "両方の欄を読み込んだファイルの最初と最後の時刻に戻します。"
                     "チェックを外さずに期間指定を取り消す最短の方法です。",
    "files.order": "この一覧の並び順で読み込み・書き出しを行います。並び順はセッションに保存されます。\n\n"
                   "・行をドラッグして上下に移動、または選択して ▲ ▼\n"
                   "・「名前順に並べ替え」は数字を数値として比較するので、"
                   "001_PCS監視_2026-07-13 が 2026-08-06 より先、file2 が file10 より先になります\n"
                   "・「常に名前順」にすると、後から追加したファイルも自動で並びます\n\n"
                   "解析はデータ内のタイムスタンプで日をまとめるため、並び順で結果は変わりません。"
                   "どのクリーン済みブックが先に書かれるか、ログの読みやすさに影響します。",
    "out.features": "追加分析と、その結果をどのファイルに入れるかを指定します。\n\n"
                    "\u2022 不具合の切り分け：不足分を直流側（アレイ・ストリング・影・汚れ）と"
                    "インバータ側に分けます。8/6-7 のデータでは PCS01（交流 -13 %、直流側 -9 %、"
                    "効率は正常 → アレイ側）と PCS03（交流 -8 %、直流側は正常、効率 -1.2 pt "
                    "→ インバータ）を区別できます。対処がまったく異なる2件です。\n"
                    "\u2022 稼働状況：日射があるのに出力がない時間と、その損失 kWh。\n"
                    "\u2022 変換効率：号機別の直流→交流効率（中央値）。\n"
                    "\u2022 性能比 PR：下の定格容量が必要です。\n"
                    "\u2022 所見：同じ結論を短い文章で。\n"
                    "\u2022 散布図：出力対日射量。傾きが低い号機は直流側の問題です（HTML のみ）。\n"
                    "\u2022 縦持ちデータ：1行＝時刻×号機×項目。ピボット用（Excel のみ、容量大）。\n\n"
                    "チェックが1つも入っていない分析は計算されません。",
    "an.stable": "雲の端が数秒で20台のインバータを通過すると、健全性とは無関係な差が出ます。"
                 "また日の出直前は小さな値に対する2 %差など誤差にすぎません。\n\n"
                 "オンにすると、下のしきい値以上かつ判定窓内で日射が安定している分のみで"
                 "比較します。2日分のデータでは日中1484分のうち850分が対象になります。\n\n"
                 "オフ＝日中すべて（ばらつきが大きくなります）。",
    "an.minirr": "この日射量未満の時刻は比較に使いません。単位は日射量チャンネルと同じで、"
                 "日射量(傾斜) が kW/m2 なら 0.4 = 400 W/m2 です。\n\n"
                 "厳しくするなら上げ、曇天でサンプルが不足するなら下げてください"
                 "（サンプル数は結果表に表示されます）。",
    "an.stabletol": "判定窓内で日射量が何 % まで変動しても「安定」とみなすかです。"
                    "5 % は晴天判定として妥当な値です。\n\n"
                    "0 にすると安定判定を無効化し、最小日射量のみで絞り込みます。",
    "an.capacity": "アレイの直流定格容量です。PR ＝ 発電量 ÷（日射量 × 定格）に使います。\n\n"
                   "インバータの交流定格ではなく、アレイの直流定格を入力してください"
                   "（多くの設備で 1.1〜1.3 倍違います）。交流定格を入れると PR が 1 を超え、"
                   "物理的にあり得ない値になります。その場合はアプリが警告します。\n\n"
                   "0 で PR を計算しません。",
    "out.sections": "レポートに含めるブロックを選びます。\n\n"
                    "チェックを外すと非表示になるだけでなく、そのデータ自体がファイルに"
                    "書き込まれないため、ファイルも小さくなります。2日分の実測値："
                    "全セクション 7.36 MB、表4種を外すと 7.26 MB、表だけ残すと 0.10 MB。"
                    "グラフと時刻ごとの比較の分単位データがサイズのほとんどを占めます。\n\n"
                    "例：比較結果だけを渡したい場合は、グラフと時刻ごとの比較を残し、"
                    "読み込み情報と状態・警報ビットを外します。\n\n"
                    "最低1つは必ず残ります。すべて外すと全セクションに戻ります。",
    "out.htmllang": "レポート自体の表示言語です。.html 内のラベル、ボタン、表の見出し、"
                    "注記が対象です。\n\n"
                    "\u2022 このアプリと同じ：日本語で使っていれば日本語のレポートになります（既定）\n"
                    "\u2022 日本語のみ／英語のみ：アプリの設定に関係なく固定\n"
                    "\u2022 日本語 + English：以前の併記表示\n\n"
                    "開いた人はページ左上の「言語」で切り替えられるため、この設定は"
                    "最初に表示される言語を決めるだけです。\n\n"
                    "項目名は CSV項目名称.xlsx から取得するため元から日本語で、"
                    "この設定の影響を受けません。",
    "out.htmlextra": "分析項目以外に HTML レポートへ同梱する項目の上限です。"
                     "689列のファイルで 100 MB のページが生成されるのを防ぎます。\n\n"
                     "40 は一般的な PCS 出力（1号機あたり約22項目）に十分です。"
                     "0 にすると追加せず、上の設定にかかわらず分析項目のみになります。",
    "run": "ファイルを読み、このタブでチェックした出力をすべて書き出し、結果を「ログ」タブに"
           "並べます。「中止」は次のファイルの前で安全に止まります。",
}

TABLE = {"en": EN, "ja": JA}


def tip(key: str) -> str:
    """Hover text for a field; empty string when there is none."""
    return TABLE.get(language(), EN).get(key) or EN.get(key) or ""
