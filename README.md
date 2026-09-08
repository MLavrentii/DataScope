# DataScope 2.7.0

**SCADA / CSV → readable, colour-coded Excel + interactive HTML, per PCS.**

Built for the Aomori (青森米田) PCS monitoring exports, but everything that is
project-specific lives in a **profile JSON**, so the same application serves
other plants and other data patterns without code changes.

## What your files actually look like (verified 2026-08-25)

| File | Shape |
|---|---|
| `001_PCS監視_YYYY-MM-DD` | 1440 rows (1 min) × **689 columns**: `Time[+09:00]`, then **20 PCS × 16 channels** (`PCS01_Data01`…`PCS01_Data15`, `PCS01_積算電力量(前回値)`, `PCS01_通信異常カウンタ値`), 4 weather/plant channels, and **362 `ANN_*` status bits** |
| `002_受電監視_YYYY-MM-DD` | 55 columns — receiving panel (`Opt_Data*`), sale/purchase energy, `ANN_00*` |
| `003_出力制御_YYYY-MM-DD` | 5 columns — curtailment command, curtailed minutes |
| `CSV項目名称.xlsx` | **three dictionaries side by side on one sheet** (PCS監視 / 受電監視 / 出力制御), each with CSV名称 · 項目名称 · 単位 · 計測値種別 · 備考 — 319 entries |

The `001/002/003` prefix is the **report type, not a unit number** — the PCS
number lives in the *column* name. DataScope therefore splits a wide file into
one virtual table per unit (`identity.entity_from_column`), which is what makes
per-PCS analysis possible at all.

Things the data forced into the design:

* **`②気温` and `③日射量(傾斜)` are identical for all 20 units** (備考 says
  `PCS01-20共通`) — one sensor copied 20 times. They are marked *shared* and kept
  out of unit-to-unit comparison, because comparing them is meaningless.
* **`⑥交流電力量` is `１分毎の差分値`**, everything else is `瞬時値`. Daily totals
  sum the differences and integrate the instantaneous ones — the two agree to
  0.06 % on 2026-08-06, which is a free sanity check on every run.
* **% deviation is blanked when the fleet reference is near zero** (dawn, dusk,
  night), otherwise a 0.001 kW difference reads as ±2000 %.
* **20 units exceed any safe colour palette**, so identity is hue + line style
  (8 hues × 4 dash patterns) and the report opens on the 6 most deviating units.

---

## 1. Purpose

1. **Translate cryptic headers.** `DC_KW` → `直流電力 [kW]`, using your
   `CSV項目名称.xlsx`. The original header is kept in a second row so nothing
   is lost.
2. **Colour cells by meaning.** Zero, missing, negative, threshold, range,
   gradient, data bar, top/bottom N, duplicate, text match — written as **real
   Excel conditional formatting**, so the rules survive your own sorting,
   filtering and editing. Stuck sensors, sigma outliers and sudden jumps are
   computed here (Excel cannot express them) and painted as fixed colours.
3. **Compare PCS units at the same timestamp.** For every metric you choose:
   a `time × PCS` matrix, plus a **deviation sheet** (each unit versus the
   fleet mean / median / max / best, in % or in units). This is where an
   underperforming PCS becomes obvious in one screen.
4. **Report.** Daily summary per unit, zero report, source-file quality report,
   Excel charts, matplotlib PNGs, and a **single-file interactive HTML report**
   with filters, a colour-scaled matrix and hover charts.

Original files are **never modified**. Output is written as new files with a
`_cleaned` suffix (configurable), plus one combined analysis workbook.

---

## 2. Supported inputs

| Type | Notes |
|---|---|
| CSV / TXT / TSV | encoding auto-detected (utf-8, utf-8-sig, **cp932 / shift_jis**, utf-16, euc-jp); delimiter auto-detected (`,` tab `;`) |
| XLSX / XLSM / XLS | first sheet by default, configurable in the profile |
| Dictionary | XLSX or CSV with the raw name / readable name / unit (/ note) columns — positions auto-detected |

Handled automatically: junk title lines above the header, a unit row under the
header, multi-row headers, merged/blank header cells, `-` and `***` as
"no value", full-width digits, Japanese timestamps (`2026年8月6日 12時30分`),
a separate date + time-of-day column, missing rows, duplicate timestamps,
Japanese paths and paths with spaces.

---

## 3. Installation

```bat
install_env.bat            REM uses the Python already on PATH
install_env.bat venv       REM safer: builds a venv at C:\dsenv (short path)
```

The script installs **only what is missing**, one package at a time, and leaves
an existing PySide6 untouched. Do not run `pip install -r requirements.txt`
directly on a machine that already has a working PySide6 — see below.

Python 3.11-3.13. **Avoid the Microsoft Store build of Python**: its
`site-packages` is redirected and read-only, so PySide6 cannot install there.
`install_env.bat` detects it and says so.

### If the installer fails with `OSError: [Errno 2] No such file or directory`

That is the Windows 260-character path limit, not a DataScope problem. PySide6
ships files with very long names (`...qml/Qt/labs/assetdownloader/objects-Debug/
QmlAssetDownloaderPrivate_resources_1/.qt/rcc/qrc_..._init.cpp.obj`) and a long
user folder name pushes them over the edge. Any one of these fixes it:

1. **Enable long paths** (once, admin PowerShell, then sign out and back in):

   ```powershell
   Set-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' `
     -Name LongPathsEnabled -Value 1 -Type DWord
   ```

2. **Use a short-path virtual environment**: `install_env.bat venv` → `C:\dsenv`.

3. **Skip the GUI entirely for now.** DataScope's command line mode needs only
   pandas, numpy, openpyxl and matplotlib — none of which hit the limit:

   ```bat
   python -m pip install pandas openpyxl matplotlib
   python main.py --cli "D:\data\2026-08" --dict "D:\data\CSV項目名称.xlsx" --out "D:\out" --png
   ```

If a PySide6 upgrade already failed half way, `shiboken6` may have been
uninstalled and the replacement not written — other PySide6 apps on the machine
will then fail to start. Repair after applying fix 1 or 2:

```bat
python -m pip install --force-reinstall --no-cache-dir PySide6
```

## 4. How to run

### In PyCharm (or any IDE)

Open the `DataScope` folder as the project, install `pandas openpyxl matplotlib`
when PyCharm offers to, then **right-click `RUN_ME.py` → Run**. Edit the settings
block at the top of that file (data folder, which channels to compare, which
outputs to write) — no command line, no arguments. It prints the per-unit ranking
straight into the PyCharm console.

`main.py` is the graphical version; run that when you want the window with tabs.

### From Windows Explorer

| Double-click | What it does |
|---|---|
| `run_analysis.bat` | **Drag your data folder onto it.** Runs the whole analysis with the bundled profile, finds `CSV項目名称.xlsx` in that folder by itself, writes `DataScope_out\` beside the data and opens it. **Needs no PySide6** — use this when Qt is broken. |
| `run_gui.bat` | Starts the window. If Qt is unusable it says so and points you back to `run_analysis.bat`. |
| `install_env.bat` | Installs only the packages that are missing. |
| `build_exe.bat` | Builds the Windows EXE (needs PySide6 + PyInstaller). |

Same-day duplicates are handled: if a folder holds both `001_PCS監視_2026-08-06.csv`
and `.xlsx`, only the first is read and the log says which was skipped.

Or by hand:

```bat
python main.py                                REM GUI
python main.py --cli "D:\data\2026-08" --out "D:\out" --png
python main.py --cli "D:\data" --profile profiles\aomori_pcs.json ^
               --dict "D:\data\CSV項目名称.xlsx" --metrics Data10,Data06 --png
```

`--dict` may be omitted: DataScope then looks in the input folder for a
spreadsheet whose name contains 項目名称 / 項目名 / dict / mapping / 定義.

---

## 4b. The window on a small screen

Every settings tab sits in a scroll area, so nothing can end up unreachable at
150 % display scaling or on a laptop panel. The window also refuses to open
larger than the display, and a geometry remembered from a bigger monitor is
pulled back on screen instead of opening half off the edge.

The two tabs that grew fastest are split into inner tabs:

* **5. 出力** → ファイル / 内容 / 追加分析 / 書式 — 1334 px of content became
  four pages of 271–496 px.
* **4. 解析** → 比較設定 / グラフ — the chart builder is a separate job from the
  comparison settings; together they were 1004 px.

At a 900 px window every tab now fits with no scrolling at all; the scroll areas
stay as the safety net. *Run* lives in the bottom bar, outside the tabs, so it is
always reachable whatever the tab is doing.

## 5. Step-by-step usage

**Tab 1 — Input**

1. Pick a profile (`Aomori PCS monitoring` is bundled) or press **New**.
2. Set the **dictionary file** (`CSV項目名称.xlsx`).
3. Add the files — drag & drop a folder works. Two shapes are supported:
   * **wide** (Aomori): one file per day holding every unit, the unit taken from
     the column name via `identity.entity_from_column` (`^(PCS\d+)[_ ]+(.+)$`);
   * **one file per unit**, the unit taken from the file name via
     `identity.entity_from_filename`.
   Mixing report types in one run is fine — only files that share a metric are
   compared.

   **The order in the list is the order they are processed in**, and it is
   remembered with the session. Three ways to set it:

   | | |
   |---|---|
   | drag a row | move it up or down inside the list |
   | ▲ ▼ | move the selected rows (works for a multiple selection) |
   | **名前順に並べ替え** / Sort by name | file name order, with digit runs compared as **numbers** — so `001_PCS監視_2026-07-13` comes before `2026-08-06`, and `file2` before `file10`, which plain text sorting gets wrong |
   | **常に名前順** / keep in name order | stays sorted, including files you add or drop in later. Reordering by hand switches it off, so the tick box never claims an order the list does not have |

   Dropping files from outside still adds them; a drag that starts inside the
   list reorders it. The analysis groups by the timestamps **inside** the data,
   so the order changes no result — it decides which cleaned workbook is written
   first and how the log reads.
4. Press **読み込み／列を更新 (Read files)**.

**Tab 2 — Columns**

> Empty? The list is filled by reading the files — go back to tab 1 and press
> **読み込み／列を更新 (Read files / refresh columns)**. Pressing *Run* straight
> away also fills it, but only once the run finishes.

**Six ways to choose columns**, all of which keep the label / time column:

| Button | What it does |
|---|---|
| Select all / none | everything on or off |
| Numeric only | drops text and status columns |
| **Keep only ticked** | hides everything except what is ticked *right now* — the manual route, no pattern needed |
| **Invert** | swaps ticked and unticked |
| **First column only** | clears the board so you can build a selection up instead of paring 689 down |

**Hiding columns by name.** Tick *Apply this name filter*, then type what a
column name must contain to stay visible — `Data10, Data06, 電圧` keeps just
those, comma separated, matching the raw name *or* the readable name. The
second box hides anything containing the listed text (`ANN_, 通信異常`). The
first column (labels / time) is always kept unless you untick that option.

A hidden column is left out of everything: the Excel output, the colour rules,
and the list of channels offered for comparison and for charts. *Show all*
clears the filter.

For a wide file this lists the **channels**, not the 689 raw columns: 16 PCS
channels + the plant channels, each with its readable name, unit, value type
(瞬時値 / １分毎の差分値) and a marker on shared sensors. Edit a name or unit to
override the dictionary (the edit applies to that channel on every unit); untick
a channel to leave it out of the output. Channels the dictionary did not cover
are marked, so you know what to add to `CSV項目名称.xlsx`.

**Tab 3 — Colour rules**
Tick a rule to enable it. **Applies to** decides the scope:

| Scope | Meaning |
|---|---|
| All columns | everything |
| All numeric columns | numbers only (the usual choice) |
| All text columns | status / alarm columns |
| Selected columns only | exactly the columns ticked in tab 2 — press **「列」タブでチェックした列を使う** |
| Columns matching a pattern | regex on the column name, e.g. `温度|TEMP` |

Only the parameters a rule actually needs are shown. Colours are pickable.
Add / duplicate / delete rules freely — they are saved in the profile.

**Gradients** (rule type *Colour gradient*) come in three modes:

| Mode | What Excel gets |
|---|---|
| 2 colours | a smooth low→high gradient |
| 3 colours | low → middle → high gradient |
| 5 bands | five fixed colours, one per value range (Excel gradients stop at three, so this is written as five range rules) |

and three ways to place the stops: the column's own **min/max**, **percentiles**
(ignores extremes), or **your own numbers** typed as `0, 25, 50, 75, 90`.

**Tab 4 — Analysis**

*Top half — the automatic comparison.* Tick the channels to compare across
units, choose the deviation reference (mean / median / max / best unit) and
whether it is shown in %. Optional resampling (1/5/10/15/30 min, 1 h) makes
files with different intervals comparable and shrinks huge datasets.

*Bottom half — your own charts.* Each entry answers "what goes on the X axis,
what goes on the Y axis, and how many charts do I want":

| Field | Meaning |
|---|---|
| **X axis** | normally the time column (the default). Pick a data column instead and the chart becomes an X-versus-Y scatter — e.g. X = 日射量, Y = 交流電力 gives the classic power-versus-irradiance cloud. |
| **Y values** | press *Use the channels ticked above* — whatever is ticked in the list at the top of the tab. |
| **How to split** | *One chart with everything* · *One chart per channel* (all 20 units on it) · *One chart per unit* (all channels on it) · *One chart per name pattern* |
| **Name patterns** | comma separated, e.g. `Data10, Data06` or `交流電力, 直流電圧`. Each pattern gets its own chart holding every column whose raw or readable name contains that text. |
| **Chart type** | line · scatter · bar |
| **% of own maximum** | plots each series as a percentage of its own peak, so channels with different units can share one chart honestly |

Series with different units are **never** put on one axis: the chart is split
per unit automatically unless the % option is on. Charts land in `charts/*.png`
and on a *User charts* sheet in the analysis workbook.

**Tab 5 — Output**
Output folder (empty = beside the source files), file suffix, and which
products to write. **Run** at the bottom right; **Cancel** stops cleanly.

---

## 6. Output

| File | Contents |
|---|---|
| `<source>_cleaned.xlsx` (wide files) | `Info` · **one sheet per unit** (`PCS01`…`PCS20`, 17 readable columns each, raw code kept in row 2) · `PLANT` (irradiance, panel temp, wind, comms counters) · `STATUS_ALARMS` (every `ANN_*` bit that was ever on: minutes, share, events, first/last) |
| `<source>_cleaned.xlsx` (one unit per file) | `Data` (translated, filterable, colour-coded, frozen header) · `Info` · `Flags` (every rule hit with time and value) · `Charts` |
| `PCS_analysis_<stamp>.xlsx` | `Overview` (now also states the colour scheme in use) · `NN <metric>` = time × PCS matrix · `NN <metric> dev` = deviation from the fleet · `NN <metric> chart` · `Daily summary` (samples, coverage, mean/max/min, **Total** with its integration note, zeros, negatives) · `Status alarms` · `Zero report`. **Every colour on these sheets comes from your colour-scale rule on the Rules tab** — 2/3-colour gradients become Excel gradients, a 5-colour rule becomes five solid bands at your thresholds, and the zero rule wins over all of them. |
| `PCS_report_<stamp>.html` | one self-contained file: channel selector, value/deviation view, date range, per-unit toggles, unit and channel search, `None`/`Invert` selection, "min spread" and "hide all-zero" row filters, KPI tiles, hover chart, colour-scaled matrix, **a "Table colours" panel that starts from your app setting and can be changed in the page**, CSV export, light/dark |
| `charts/*.png` | matplotlib value + deviation charts, and — with more than 8 units — a **ranked bar chart per metric** (every unit against the fleet mean, with the % gap printed). That ranking is usually the fastest way to see which PCS is behind |

Colour meaning is listed on the `Info` sheet and in the HTML legend, so a
colleague who did not run the tool can still read the file.

### Choosing which files to create, and what goes in them

The **5. Output** tab is in four groups.

*Which files to create* — the four outputs are independent, so any combination
works. Quick-pick buttons set them in one click:

| Preset | Cleaned | Analysis | HTML | PNG |
|---|---|---|---|---|
| Everything | ✓ | ✓ | ✓ | ✓ |
| HTML only | – | – | ✓ | – |
| Analysis only | – | ✓ | – | – |
| Cleaned only | ✓ | – | – | – |

From the command line: `--only html|analysis|cleaned|all`. If nothing is ticked
the run stops immediately with a message instead of reading the files.

*What goes into each file* — one setting per output:

| Scope | Meaning |
|---|---|
| Selected metrics only | just the channels ticked on the Analysis tab (plus the time column) |
| All visible columns | whatever the Columns tab left visible |
| Everything in the file | ignores the column filter; hidden columns come back |

Defaults, and why:

* **Cleaned workbooks → All visible columns.** The cleaned file is the readable
  copy of the source, so it follows your column filter.
* **Analysis workbook → Selected metrics only.** Every channel costs three
  sheets (values, deviation, chart); 22 channels would mean 66 sheets. *Max
  channels in the analysis workbook* (default 12) is the safety net, and the log
  names anything left out.
* **HTML report → Everything in the file.** The report is interactive, so one
  run gives you every view: switch **Channel list → All channels** in the page
  and pick whatever you want to look at. Channels beyond the analysis set are
  marked `·`, carry 1500 points and no deviation frame; *Max extra channels*
  (default 40) caps the file size.

From the command line: `--scope cleaned,analysis,html`, e.g.
`--scope visible,selected,all`.

Measured on one 08-06 file: cleaned workbook 1.1 MB with *Selected* vs 2.2 MB
with *Everything*; analysis workbook 12 channels = 40 sheets, 5.0 MB;
HTML-only run 4.6 MB with all 16 channels.

### Several channels at once

*グラフ配置 / Layout* in the control bar:

| Mode | What it does |
|---|---|
| 1項目 | one channel, as before |
| 上下に並べる | one panel per channel, **sharing one time axis**; hover any panel and a single crosshair crosses all of them with a tooltip listing every channel at that moment |
| 1つに重ねる | the selected channels on one chart |

A *表示する項目* chip row appears in the two multi-channel modes; the channel
you were already on is pre-selected, so the first click adds a second rather
than replacing the first.

Two details that matter:

* Channels are sampled differently (an extra channel carries fewer points), so
  the multi-channel views map x by **timestamp**, not by array index — otherwise
  two channels with different thinning would be drawn out of step.
* Overlaying channels with different units would need a second Y axis, which is
  never acceptable, so mixed units are drawn as **% of each channel's own
  maximum** and the note says so. If every selected channel shares a unit, real
  values are plotted. With one unit visible its channels are overlaid directly;
  with several, each channel is one line — the mean across the visible units —
  because 5 channels × 6 units is 30 lines and unreadable.

### Extra graphs: build the picture the question needs

Under the main chart there is a *追加グラフ / Extra graphs* card. It opens with
one graph already built — **日射量 and 気温 against the clock, on the same time
axis as the power chart** — and you can add as many more as you like.

That graph replaced the old 出力対日射量 scatter as the default. The scatter is
still there as a tick box (*出力対日射量の散布図* on the Output tab), but a
scatter throws away the time of day, and the time of day is exactly what you
compare a power curve against: cloud edges, a morning shadow, an afternoon
derate all show as *shape*, and shape needs a clock on the X axis.

| Control | What it does |
|---|---|
| ＋ グラフを追加 | a new graph, seeded with the channel the main chart is on |
| 系列を追加 | put any channel that travelled with the report onto that graph |
| the × on a chip | take that series off the graph |
| 既定に戻す | back to the graph the report opened with |
| 並べ方 | one graph per row, or two side by side |
| PNG / JPEG | **that graph only**, at 2× scale, legend drawn into the image |
| このグラフだけ HTML | **that graph only**, as its own offline HTML file with a pruned payload (0.44 MB against the 8.38 MB original in the measured case) |
| the × at the end of the bar | delete the graph |

Every graph shares the main chart's **time axis, period, day tab and unit
selection**, which is what makes the patterns line up. Hover or use the arrow
keys on any of them and one crosshair crosses every extra graph at once, with a
tooltip listing each channel's real value at that instant.

#### Per-series height and drawing order

Each chip in a graph's bar carries three controls of its own:

| | |
|---|---|
| **× number** | how many times to amplify **that series alone**, around its own mean |
| **◀ ▶** | move it in the drawing order — the rightmost chip is drawn on top |
| **×** | drop the series from the graph |

The multiplier exists because two channels on one graph can differ by a factor
of a hundred: 気温 barely moves next to 日射量, and at ×3 its shape becomes
readable. Two things it deliberately does **not** do:

* it does not stretch the axis — that would shrink every other series instead
  of enlarging the one you asked for, so the axis stays on the real data and
  the other lines never move;
* it does not multiply from zero — a channel sitting at 60–100% of its own
  maximum would simply leave the top of the frame. It amplifies around the
  series' **own mean**, so the line grows in place. Anything that still runs off
  the edge is clipped rather than drawn over the labels.

Any multiplier other than 1 is written as `×3` next to the name in the caption,
the note under the graph, the tooltip's group heading and the legend of the
exported image — a line drawn three times too tall and not labelled as such is
a lie. The values in the readout and the CSV are always the real ones. The
setting is keyed by channel, so it survives a saved view and a pruned export.

#### Plant-wide channels: both pyranometers, the panel thermometer, the wind

Some sensors belong to the site, not to a unit. They sit in the PCS file but
outside every `PCS01_…` block, so until 2.7.0 they only reached the PLANT sheet
of the cleaned workbook and were missing from the channel list entirely:

| key | name | unit |
|---|---|---|
| `Opt_Data22` | **日射強度2(水平)** — the horizontal pyranometer | kW/m² |
| `Opt_Data21` | パネル温度 | ℃ |
| `Opt_Data32` / `Opt_Data31` | 風速 / 風向 | m/s · ° |

They are now in the 項目 list — in the app's Analysis tab and in the report's
channel dropdown — and they are treated as plant-wide channels: one line in its
own colour, droppable onto any graph, never twenty copies of one sensor. They
are carried under a `PLANT` pseudo-entity rather than being copied into all
twenty unit tables, and that table is deliberately kept **out** of the
diagnostics: PLANT is not a twenty-first inverter with no DC side.

So the site now has two irradiance channels and the report knows which is
which: `③日射量(傾斜)` is what the panels actually see, `日射強度2(水平)` is
what a forecast or a neighbouring site reports. **The default weather graph
carries both plus 気温**, because the pair is what tells you about soiling,
snow, or a sensor that has been knocked out of alignment — on 08-06 they track
each other at r = 0.998 but differ by ~12 % at midday, which is the tilt
working as intended rather than a fault.

#### Two kinds of channel, and why it matters

日射量 and 気温 are measured once for the plant and copied into all twenty PCS
blocks — byte-identical, which the report checks rather than assumes. Twenty
identically-coloured lines would carry one line's worth of information, so:

* a **plant-wide** channel is drawn **once**, in its own colour (irradiance
  graphite, temperature amber — deliberately outside the twenty-unit palette so
  it can never be mistaken for a PCS line), and can be dropped onto **any**
  graph as an extra line;
* a **per-unit** channel (交流電力, 直流電力 …) is one line per PCS, so it gets a
  graph of its own. Adding a second per-unit channel to a graph creates a new
  graph instead and says so — two channels both needing the twenty-unit palette
  on one picture is unreadable, and quietly drawing it would be worse than
  refusing.

So 出力 + 日射量 + 気温 on one graph is a legitimate combined graph; 出力 +
直流電力 on one graph is two graphs.

Mixed units still never get a second Y axis: each channel is drawn as **% of its
own maximum**, and the maximum is stated in the note under the graph and in the
legend of the exported image, so the picture stays interpretable after it leaves
the page.

### Reading an exact minute with the keyboard

A pixel is several samples wide once a whole day is on screen, so the mouse
cannot land on a chosen minute. The arrow keys can:

| Key | Step |
|---|---|
| ← → | one sample |
| Shift + ← → | ten |
| Ctrl / ⌘ + ← → | sixty |
| Home / End | the ends of the visible range |
| Esc | release the cursor |

The crosshair and tooltip behave exactly as with the mouse, and in the stacked
and side-by-side layouts one keypress moves the cursor in **every** panel, so
you read all channels at that instant. Typing in a text field is never
intercepted.

### Zooming, and picking the days that matter

Both work on every graph in the page — the main chart, the panels, every extra
graph — and both survive into a saved copy, including a "this graph only" file.

**Zoom** is not a second viewport: it writes the report's own 開始 / 終了, so one
gesture narrows the chart, the panels, the extra graphs, the KPI tiles, the
matrix and the CSV export together, and the pickers above show exactly what is
on screen.

| Gesture | Result |
|---|---|
| drag across a graph | zoom into that range (a shaded band follows the pointer) |
| Ctrl (⌘) or Shift + mouse wheel | zoom around the pointer — plain wheel still scrolls the page, because this report is tall |
| Alt + mouse wheel | stretch the **value** axis around the pointer |
| Shift + drag | slide the frame, both axes, size unchanged |
| double-click | back to the default view (time, value and height) |
| 拡大＋ / 縮小− / ◀前へ / 次へ▶ / 全体表示 | the same from buttons, above each chart |
| `+` `-` | zoom time around the cursor · `,` `.` step earlier and later |
| `[` `]` | zoom the value axis · PageUp / PageDown move it · `0` resets everything |

The label beside the buttons always says what you are looking at — 表示範囲
08-06 09:00 → 08-06 15:00（全体の 13%）.

The **value axis** stretches too, with its own group of buttons (縦＋ / 縦− /
▲ / ▼) and **Alt + mouse wheel** around the pointer. It is stored as a fraction
of each panel's *own* automatic range, so one setting works on every panel
whatever its unit — kW, kW/m² and °C all zoom together and none of them needs a
second Y axis. `[` and `]` do it from the keyboard, PageUp / PageDown move it.

**Shift + drag** slides the frame in both directions at once without changing
its size — the way to look at the next stretch of a zoomed-in curve. While the
button is down the tables are left alone (redrawing 300 rows × 20 cells per
frame is what would make it feel like treacle); one full redraw lands on
release.

**グラフの高さ / Height** is a slider from 60% to 250% of the default. It
stretches the drawing area itself, not the data, and exported images follow. A
tall thin panel is often the fastest way to see a small deviation.

**表示をリセット** (or `0`) puts all three back: time, value and height. A
double-click on any graph does the same.

**Days** are a set, not a single tab. Click a day to add or drop it,
Shift-click for just that one, 全日 for everything. The chosen days are then
drawn **side by side with the days in between taken out** — pick 07-13, 08-06
and 08-07 and you get those three next to each other, not three weeks of blank
paper with three stripes in it. A dashed divider and a date label mark every
join, and the lines are broken there so a jump across the gap can never read as
a real change over a few minutes.

Days that really are adjacent merge into one continuous block, so 全日 and a
single day behave exactly as they always did. Everything downstream follows the
selection: the matrix, the KPI tiles, the CSV, the exported images, the readout.

Two consequences worth knowing:

* the day strip appears in three places — above the main chart, above the extra
  graphs and above the matrix — and they are one selection, not three;
* zooming inside one day sets 開始/終了 to that range, which by definition
  excludes the other days; 全体表示 (or `0`) brings them back.

**「表示中の日・期間だけ保存」** writes a new HTML holding **only the rows on
screen** — the chosen days, inside the zoom — but every channel, every unit and
every card. It is the answer to "send me just those two days": on the four-day
report above, 8.34 MB became 3.47 MB, and the 日別サマリー in the copy lists
exactly the two days it contains. Days stay choosable inside the new file, for
the days it holds.

### The floating readout

The box that follows the crosshair lists **every visible unit** — it used to stop
at six, which is useless with twenty PCS on screen — so a long list is split
across up to three columns rather than truncated, grouped by channel with the
unit shown in each group's heading. In the extra graphs one readout covers every
graph at that instant, so a plant-wide channel and a per-unit channel are read
against each other in one glance. Values are always the **real** measurement,
even when the axis is a percentage.

It has two controls of its own, in the bar above each graph:

| Control | Choices |
|---|---|
| 値の吹き出し / Readout | **常に表示** · **Ctrl 押下中のみ** — the crosshair still follows the mouse, the numbers appear only while Ctrl (⌘) is held · **表示しない** — no box at all |
| 透明度 / See-through | 0–70%. The alpha goes on the **background**, not the box, so the numbers stay at full strength; the panel blurs what is behind it so they stay readable |

The arrow keys always work: every control in the bar above a graph hands the
keyboard back when you are done with it, so a dropdown or a slider cannot
swallow the arrows that should be walking the cursor along the chart.

`表示しない` and `Ctrl 押下中のみ` keep the crosshair and the dots on the lines —
what they switch off is the panel that covers the chart. The arrow keys always
bring the numbers up (a keypress is deliberate), unless the readout is off
entirely. Both settings travel with a saved copy.

Three rules it obeys, all of them things that were wrong before:

* it stays **inside the plot area** of the chart it is reading. It can therefore
  never sit over the PNG / JPEG / HTML buttons underneath — and because the box
  ignores the mouse, a box over a button looks exactly like a dead button;
* it keeps the **height the pointer was last at**, so switching from the mouse
  to the arrow keys does not make it jump. Where the list is tall enough to fill
  the chart it simply stays put;
* a hover **lets go** when the pointer leaves the chart. Only the arrow keys pin
  it, and `Esc` releases that. (Previously any hover counted as a pinned cursor,
  which is why the box would not go away.)

### Getting things out of the report

| Button | Result |
|---|---|
| この表示を保存 | an HTML file that **reopens exactly as it looks now** — layout, channels, hidden units, Worst N, colours, language, day and period are all written into the copy, and the controls above the chart show those values too |
| このグラフだけ保存 | the same, but **only the chart, only the selected channels, only the visible units** — and the payload is pruned to match, so the file is small: 210 KB against the 4.7 MB original measured case below |
| グラフを PNG 保存 | the chart at 2× scale, **with the legend drawn into the image** — a chart pasted into a report with no legend is unreadable. In stacked mode the panels are combined into one tall image with a single legend |
| グラフを JPEG 保存 | same, JPEG at quality 0.92 |
| 印刷 / PDF | the browser's print dialog — a print stylesheet hides the controls and lets tables break sensibly, so Save as PDF gives a clean document |
| PNG / JPEG / このグラフだけ HTML *in an extra graph's bar* | that one graph, nothing else |
| 表示中の日・期間だけ保存 | the whole report, but only the days and period on screen (all channels and units kept) |
| この表を CSV 保存 | the diagnosis table on screen |
| Export CSV | the matrix in the current view |

Measured on a one-day report with three units selected: original 4.73 MB,
「このグラフだけ保存」 210 KB, 「この表示を保存」 4.97 MB (the whole payload plus
the saved settings), PNG 169 KB.

All of it runs in the browser: no library, no network, still one offline file.
Verified with the browser in offline mode — four downloads produced, and the
saved HTML reopened with an identical state and no console errors.

**PDF and Excel, honestly:** a real `.pdf` or `.xlsx` written from the page would
need a bundled library, which would break the single-file, no-dependency
property. So PDF goes through the browser's print dialog (which produces better
output anyway), and for spreadsheets the page exports CSV — which Excel opens
with no format warning — while the *real* multi-sheet workbook, with the
conditional formatting and the native charts, is what the app itself writes.

### Diagnostics: why a unit is low, not just that it is

The matrix tells you PCS01 is down. These tell you where to send someone.

Two ratios do the work, and both come from channels the export already carries:

| Ratio | Low means |
|---|---|
| DC power ÷ irradiance | the problem is **outside** the inverter — a string down, shading, soiling, a bad connection |
| AC power ÷ DC power (efficiency) | the problem is **the inverter itself** |

On 2026-08-06/07 that separates two units the old report lumped together:

| Unit | AC vs fleet | DC side | Efficiency | Verdict |
|---|---|---|---|---|
| PCS01 | −13.2 % | **−9.3 %** | +0.03 pt | array / DC side |
| PCS03 | −8.1 % | +0.5 % | **−1.21 pt** | the inverter |

A unit at or above the fleet is never blamed, whatever its internals say —
otherwise a healthy unit with a soft inverter and a strong array gets reported
as broken.

The full set, and where each result can go:

| Analysis | Excel | HTML |
|---|---|---|
| Fault attribution (DC vs inverter) | sheet | table + verdict |
| Availability and lost kWh | sheet | table |
| DC → AC efficiency | sheet | table |
| Performance ratio (PR) | sheet | table |
| Findings summary | Overview | bullet list at the top |
| Irradiance + temperature graph | — | the extra graph the report opens with **(default on)** |
| Power vs irradiance scatter | — | chart *(default off since 2.3.0)* |
| Long-format table for pivots | sheet | — |

*5. 出力 → 追加分析と、その出力先* is a grid of feature × Excel × HTML.
**Nothing is computed unless some output asks for it**, so switching a row off
costs nothing at run time. A greyed box means that result cannot go into that
format — a scatter plot is not a spreadsheet, and a 200 000-row long-format
table is not a web page.

A profile saved by 2.2.0 or earlier is migrated on load: it gains the
irradiance + temperature graph and the scatter is switched off, because the
graph took its place. Tick the scatter again if you want both — nothing else in
the profile is touched.

#### Steady-light comparison

*4. 解析 → 日射が十分で安定した時間帯のみで比較する* (on by default) restricts
the comparison to minutes where irradiance is above a threshold **and** steady
across a short window. A cloud edge crossing twenty inverters over a few seconds
makes every one of them read differently for reasons that have nothing to do
with their health; at dawn a 2 % difference is a rounding error on a tiny number.
On the two-day sample this keeps 850 of 1484 daylight minutes, and the sample
count is printed in every result table.

#### Performance ratio

PR = energy ÷ (irradiation × DC nameplate). Set the nameplate on the Analysis
tab; leave it at 0 and PR is skipped with a message. Use the **DC** nameplate of
the array, not the inverter's AC rating — they differ by 1.1–1.3× on most
plants, and entering the AC rating produces a PR above 1, which is impossible.
The app checks for that and says so.

### Where the report's data lives

**Everything is written into the .html file at generation time.** The report
reads nothing when you open it — no CSV, no network, no server. Verified on a
two-day report: `fetch` / `XMLHttpRequest` / `WebSocket` calls = 0, external
`src`/`href` = 0, and 6.96 MB of the 7.02 MB file is one embedded JSON object.
Opened in an offline browser from a folder containing no CSV at all, the chart,
the matrix and every table still render.

That is why the file is large, and it is also the point: you can mail it,
put it on a share or open it on a machine that has never seen the source data.
The CSV names you see in 読み込み情報 are text in a table, not links.

### Choosing what the report contains

*5. 出力 → HTML レポートに含めるセクション* — six tick boxes:
グラフ・KPI, 時刻ごとの比較, 日別サマリー, ゼロ値レポート, 状態・警報ビット,
読み込み情報.

Unticking one does more than hide it: **that data never enters the file**.
Measured on the same two-day run:

| Sections kept | File |
|---|---|
| all six | 7.36 MB |
| everything except the four tables | 7.26 MB |
| only 日別サマリー + 読み込み情報 | 0.10 MB |

So the minute-by-minute series behind the chart and the matrix are what make the
file big; the summary tables cost about 100 KB together. Controls that only
drive a dropped section disappear with it — no colour panel without the matrix,
no filter bar when neither chart nor matrix is present. At least one section is
always kept; unticking all six falls back to all of them.

Command line: `--sections chart,matrix` (any comma-separated subset).

### Picking a period without typing

**In the app** (*4. 解析 → すべての出力を指定期間に限定する*): two
`QDateTimeEdit` boxes — click the arrow for a month calendar, use the arrow keys
or type on the hour/minute fields. After the files are read the calendar is
clamped to the period they actually cover, so a date with no data cannot be
picked, and both boxes start on the true first and last timestamp. *全期間*
snaps them back.

The window is applied straight after reading, so **every** output covers exactly
that period — cleaned workbooks, the analysis workbook, the HTML report and the
charts — and the daily totals and rankings are computed from it alone. A file
left with no rows inside the window is skipped with a message instead of
producing an empty workbook. Both ends are inclusive: 11:00 → 13:00 on
minute data keeps 121 rows.

**In the HTML report**: the 開始 / 終了 boxes are now
`<input type="datetime-local">` — the browser's own calendar and clock, no
library, still one offline file. They are bounded to the data (and to the
selected day tab), and *期間をクリア* empties them. Switching day tabs drops a
range belonging to another day, which would otherwise filter the new day down to
nothing.

**In Excel** there is no calendar control in a macro-free `.xlsx` — the classic
date picker is a 32-bit ActiveX control that needs VBA and an `.xlsm`, which
would trigger macro warnings on every open. What the matrix sheets get instead:

| Cell | What it is |
|---|---|
| `C2` 日付 / Date | dropdown listing only the dates present in the data |
| `E2` 開始時 / From h | dropdown, 0–23 |
| `G2` 終了時 / To h | dropdown, 0–23 (inclusive, so 11→13 covers 11:00–13:59) |

Choosing from those dropdowns recomputes a **範囲内 / In range** column and
**greys out every row outside the window** immediately — no filtering step
needed, and the AutoFilter on that column narrows the sheet if you want only the
window. The choices live on a hidden `_pick` sheet because Excel caps inline
validation lists at 255 characters. Verified by recalculating the workbook: date
2026-08-07 with hours 11–13 flags exactly the 180 rows from 11:00 to 13:59.

### Report language

The HTML report has its own interface language, set by *HTML report language* on
the **5. Output** tab:

| Choice | Effect |
|---|---|
| Same as this app (default) | a Japanese session writes a Japanese report |
| 日本語 only / English only | fixed, whatever the app is set to |
| 日本語 + English | the bilingual labels older reports used |

Everything inside the page follows it — labels, buttons, dropdown options, KPI
tiles, table headers, the day tabs, the colour panel and every note. The summary
tables (Daily summary, Zero report, Status bits, Source files) get Japanese
headers too: 号機, 日付, 取得率 %, ON 時間（分） and so on. Sentences are never
shown bilingually — in *both* mode the notes stay English, because a note with
bilingual values substituted into it is unreadable.

Whoever opens the file can change it themselves: a **言語 / Language** box sits
first in the control bar, so one file serves a Japanese site meeting and an
English report without regenerating anything. `--lang ja|en|both` sets the
default from the command line.

Channel names (⑩交流電力 …) always come from `CSV項目名称.xlsx`, so they are
Japanese regardless of this setting. The Excel workbooks still use English sheet
headers — say so if you want those translated as well.

### Day tabs

When the report holds more than one day, a tab strip sits at the top of the
**Matrix** card: *All days* plus one tab per date, each showing its row count.
Picking a day narrows the whole view — matrix, chart, KPI tiles and the CSV
export (the day is appended to the exported filename). *All days* returns to the
full range, and *Reset* clears it too. The strip is hidden for a single-day
report.

The From / To boxes still work on top of a chosen day, so you can go from
"08-06" to "08-06 11:00–13:00" in two clicks.

### Opening view of the report

*HTML opens with the worst N units* (default 6) decides how many units the
report shows first — with 20 PCS every line overlaps, so it starts with the ones
furthest from the fleet. That is only the starting view: a number box next to
**Worst N** in the page changes it any time (3, 4, 10 …), and *All* / *None* /
*Invert* or a click on a unit chip override it entirely. `--worst N` sets the
default from the command line.

### One colour scheme, three outputs

The colour-scale rule you set on the **3. Rules** tab is the single definition.
`core.rules.gradient_style()` reads it and the Excel writer, the HTML report and
the chart legend all consume the same dictionary — so the analysis workbook is
no longer blue while the app says red-to-green.

| Rule setting | Excel | HTML table |
|---|---|---|
| 2 colours, min → max | 2-colour gradient | continuous ramp between the two colours |
| 3 colours, min → max | 3-colour gradient | continuous ramp through the middle colour |
| 5 colours, min → max | 3-colour gradient (Excel's limit) + note | continuous 5-stop ramp |
| 5 colours, fixed steps | **five solid bands** at your thresholds | five solid bands, identical |
| Zero rule enabled | painted first, `stopIfTrue` so bands cannot repaint it | painted first |
| Data-bar rule enabled | native Excel data bars | in-cell bars |

Text on a coloured cell is near-black or white, whichever has the higher WCAG
contrast against that fill — so the red and green bands of the default palette
get black text in the HTML table exactly as they do in Excel, and only genuinely
dark fills (a deep blue top band, for instance) switch to white.

In the HTML report the **Table colours** panel lets the reader switch steps
(2/3/5), pick each colour, type their own thresholds, set the colour strength,
change the zero colour and turn bars on — starting from what the app produced.
*Back to app setting* undoes any experiment. Turn the panel off with *HTML: let
the reader change the colours in the page* on the **5. Output** tab.

---

## 7. Where settings are stored

DataScope reopens where you left off. Closing the window remembers the file
list, the dictionary, the output folder, the tab you were on, and **the profile
you were editing — including rule and chart changes you never saved to a
profile file**. Each dialog also remembers its own last folder, so "Add files",
"Browse dictionary" and "Output folder" each open where you were last time
rather than all sharing one path.

Two tick boxes under the file list control it: *Reopen these files next time*
(on by default) and *…and read them straight away* (off by default, because
reading a 689-column day costs ~25 s). **Forget session** clears it all; saved
profiles are untouched.

| What | Where |
|---|---|
| Files, paths, per-dialog folders, working profile | `%APPDATA%\DataScope\session.json` |
| Window size, language | `QSettings` → Windows registry `HKCU\Software\DataScope\DataScope` |
| Profiles you save | `%APPDATA%\DataScope\profiles\*.json` |
| Bundled profiles | `profiles\` next to `main.py` (read-only in an EXE build) |

Nothing is written beside the EXE, so an installation in `Program Files` works.
No credentials or personal data are stored, and no data leaves the computer.

---

## 8. Known limitations

* Excel conditional formatting cannot express "unchanged for N rows",
  "beyond k·σ" or "jumped by more than X". Those three are computed in Python
  and written as **fixed** colours — they do not follow later edits.
* Static fills are capped at 60 000 cells per sheet, chart series at ~1 500
  points, the HTML matrix at ~400 000 cells (thinned with a visible note).
  Excel itself stops at 1 048 576 rows per sheet.
* `.xlsm` macros are not preserved — output is always a new `.xlsx`.
* The deviation "%" column is blank where the reference is 0 (division by
  zero), by design.
* Timestamps are snapped to the finest common interval so that "same time"
  really lines up; units logging on different seconds are matched to the same
  minute.
* Fuzzy dictionary matching can mis-map a column whose name is very close to
  another. Check tab 2 once per new plant; an edit there wins.
* A future Android version would need a new UI — see §12.
* `ANN_*` status bits are not in `CSV項目名称.xlsx`, so `STATUS_ALARMS` names them
  by code only. The unit is inferred from the numbering (`ANN_1701` → 17th unit,
  bit 01); `50xx`/`60xx` stay plant level. Add them to the dictionary and the
  names appear automatically.
* A 689-column file takes roughly 25-30 s to read and about a minute per day of
  data for the full output set. Untick `PNG charts` to halve it.
* Conditional formatting is capped at `max_cf_columns` (120) per sheet so Excel
  stays responsive on very wide sheets.

---

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Headers show as `col0`, `col1` | the header row was not found — set `reader.header_row` (0-based) in the profile JSON |
| Names not translated | the dictionary path is empty or the raw names differ; check `Info` → "Columns not found in the dictionary" |
| Garbled Japanese | unusual encoding; add it to `reader.encodings` in the profile |
| "no time column" | the timestamp column could not be parsed; set `identity.time_column` to its exact header |
| Matrix is empty | files have no timestamp, or the metric is missing in every file |
| Japanese labels missing in PNG charts | install/keep a CJK font (Yu Gothic, Meiryo, Noto Sans JP) |
| Excel says "unreadable content" | usually an old openpyxl; upgrade with `pip install -U openpyxl` |
| Run is slow | untick `PNG charts`, raise the resample interval, or reduce the metric count |

Every failure is logged with a traceback on the **ログ / Log** tab. A file that
fails does not stop the batch.

---

## 10. Testing

`tests/make_sample.py` writes six synthetic PCS files (cp932, junk header
lines, a unit row, one underperforming unit, one two-hour outage, one stuck
sensor, one file with missing rows) plus a matching dictionary:

```bat
python tests\make_sample.py C:\temp\sample_pcs
python main.py --cli C:\temp\sample_pcs --dict C:\temp\sample_pcs\CSV項目名称.xlsx --out C:\temp\out --png
```

Checks worth running once on real data: a cancelled file dialog, a read-only
output folder, a Japanese path with spaces, a very large file, a restart
(settings restored), and the packaged EXE on a clean PC.

---

## 11. EXE packaging

```bat
build_exe.bat
```

One-folder build by default (faster start, fewer antivirus false positives);
set `ONEFILE=1` inside the script for a single file. Output is named
`DataScope_release_v1.0.0`. The script checks prerequisites, stops on the first
error, prints it and pauses, so nothing disappears when you double-click it.

## 12. Android

This is a Windows desktop tool: PySide6 on Android is possible but far from
the one-command story that PyInstaller gives on Windows, and file access,
permissions and the whole UI would need redesigning. The code is already
prepared for that case: **`core/` never imports Qt**, so the reader, dictionary,
rules, analysis, Excel and HTML writers can be reused as-is behind a Flutter or
Kivy front end, or behind a small local API, while `ui/` stays Windows-only.

## 13. Cleanup

Delete the folder. To remove saved settings as well:
`%APPDATA%\DataScope\` and the registry key
`HKCU\Software\DataScope`.
