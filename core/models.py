"""Data models for DataScope.

Pure dataclasses + enums.  No Qt, no pandas-specific types in the public
surface, so this module can be reused by a CLI, a web backend or a future
mobile client.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Optional

APP_NAME = "DataScope"
APP_VERSION = "2.6.0"


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #
class RuleKind(str, Enum):
    """Every supported highlighting rule."""

    ZERO = "zero"                 # value == 0 (optionally |v| <= tolerance)
    BLANK = "blank"               # empty / NaN
    NEGATIVE = "negative"         # value < 0
    THRESHOLD = "threshold"       # comparison against a fixed number
    BETWEEN = "between"           # low <= value <= high
    COLOR_SCALE = "color_scale"   # 2/3-colour gradient over the column
    DATA_BAR = "data_bar"         # in-cell bar
    TOP_BOTTOM = "top_bottom"     # top/bottom N or N %
    DUPLICATE = "duplicate"       # duplicate values in the column
    TEXT_CONTAINS = "text_contains"  # substring match (alarm/status columns)
    OUTLIER_SIGMA = "outlier_sigma"  # |v - mean| > k * std   (static fill)
    STUCK = "stuck"               # unchanged for N consecutive rows (static)
    JUMP = "jump"                 # |v - previous| > limit    (static)


class ScopeKind(str, Enum):
    """Which columns a rule applies to."""

    ALL = "all"                # every numeric column
    SELECTED = "selected"      # explicit list of column keys
    PATTERN = "pattern"        # regex on the column name
    NUMERIC = "numeric"        # numeric columns only
    TEXT = "text"              # text columns only


# Named colours (ARGB, Excel style) taken from the validated report palette.
FILL = {
    "red": "FFF8D0D0",
    "red_strong": "FFD03B3B",
    "orange": "FFFDE2D2",
    "yellow": "FFFCF0C8",
    "green": "FFD7F2D7",
    "blue": "FFD6E6FA",
    "grey": "FFE6E6E3",
    "violet": "FFE2DEF5",
    "aqua": "FFD2F0E4",
    "magenta": "FFFBDDE8",
}
FONT = {
    "red": "FF9C0006",
    "green": "FF006100",
    "blue": "FF1C5CAB",
    "grey": "FF6B6B66",
    "black": "FF0B0B0B",
}


@dataclass
class Rule:
    """One highlighting rule.

    ``kind`` decides which of the optional parameters are used; unused ones
    are simply ignored, which keeps the JSON profile forgiving.
    """

    kind: RuleKind = RuleKind.ZERO
    enabled: bool = True
    label: str = ""

    # scope
    scope: ScopeKind = ScopeKind.NUMERIC
    columns: list[str] = field(default_factory=list)
    pattern: str = ""

    # numeric parameters
    operator: str = "<"            # < <= > >= == != for THRESHOLD
    value: float = 0.0
    value2: float = 0.0            # BETWEEN high / OUTLIER k / STUCK n / JUMP limit
    tolerance: float = 0.0         # ZERO tolerance
    percent: bool = False          # TOP_BOTTOM: N is a percentage
    rank: int = 10                 # TOP_BOTTOM: N
    bottom: bool = False           # TOP_BOTTOM: bottom instead of top
    text: str = ""                 # TEXT_CONTAINS needle

    # appearance
    fill: str = FILL["red"]
    font: str = FONT["red"]
    bold: bool = False
    scale_colors: list[str] = field(default_factory=lambda: ["FFF8696B", "FFFFEB84", "FF63BE7B"])
    scale_reverse: bool = False
    bar_color: str = "FF638EC6"

    # gradient / banding options (COLOR_SCALE)
    scale_mode: str = "3color"        # 2color | 3color | 5color (5 = discrete bands)
    scale_bounds: str = "auto"        # auto (min/max) | percent (10/50/90) | numbers
    scale_values: list[float] = field(default_factory=list)  # explicit stops, low -> high

    # only rows where the value is actually a number are considered
    skip_blank: bool = True

    def __post_init__(self) -> None:
        # Qt marshals str-based enums back as plain strings; keep them enums.
        if not isinstance(self.kind, RuleKind):
            self.kind = RuleKind(str(self.kind))
        if not isinstance(self.scope, ScopeKind):
            self.scope = ScopeKind(str(self.scope))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["scope"] = self.scope.value
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Rule":
        d = dict(d)
        kind = RuleKind(d.pop("kind", "zero"))
        scope = ScopeKind(d.pop("scope", "numeric"))
        known = {f for f in Rule.__dataclass_fields__ if f not in ("kind", "scope")}
        clean = {k: v for k, v in d.items() if k in known}
        return Rule(kind=kind, scope=scope, **clean)

    def display_name(self) -> str:
        if self.label:
            return self.label
        return DEFAULT_RULE_LABELS.get(self.kind, self.kind.value)

    # -- scope test ------------------------------------------------------- #
    def applies_to(self, column: str, is_numeric: bool) -> bool:
        import re

        if not self.enabled:
            return False
        if self.scope is ScopeKind.ALL:
            return True
        if self.scope is ScopeKind.NUMERIC:
            return is_numeric
        if self.scope is ScopeKind.TEXT:
            return not is_numeric
        if self.scope is ScopeKind.SELECTED:
            return column in self.columns
        if self.scope is ScopeKind.PATTERN:
            if not self.pattern:
                return False
            try:
                return re.search(self.pattern, column) is not None
            except re.error:
                return False
        return False


DEFAULT_RULE_LABELS = {
    RuleKind.ZERO: "Zero value / ゼロ値",
    RuleKind.BLANK: "Blank / 空欄",
    RuleKind.NEGATIVE: "Negative / 負の値",
    RuleKind.THRESHOLD: "Threshold / しきい値",
    RuleKind.BETWEEN: "Between / 範囲内",
    RuleKind.COLOR_SCALE: "Colour scale / カラースケール",
    RuleKind.DATA_BAR: "Data bar / データバー",
    RuleKind.TOP_BOTTOM: "Top / bottom N",
    RuleKind.DUPLICATE: "Duplicate / 重複",
    RuleKind.TEXT_CONTAINS: "Text contains / 文字列一致",
    RuleKind.OUTLIER_SIGMA: "Outlier (sigma) / 外れ値",
    RuleKind.STUCK: "Stuck value / 値が固着",
    RuleKind.JUMP: "Sudden jump / 急変",
}


# --------------------------------------------------------------------------- #
# Columns
# --------------------------------------------------------------------------- #
@dataclass
class ColumnSpec:
    """A column after reading + dictionary translation."""

    raw: str                        # original header text, exactly as in the file
    name: str = ""                  # human readable name (from the dictionary)
    unit: str = ""                  # unit string, e.g. "kW"
    note: str = ""                  # extra explanation from the dictionary
    kind: str = ""                  # 計測値種別: 瞬時値 / １分毎の差分値 ...
    shared: bool = False            # same physical sensor for every entity (共通)
    entity: str = ""                # entity this column belongs to, if any
    metric: str = ""                # metric key inside the entity (e.g. Data01)
    is_numeric: bool = True
    include: bool = True            # write to output
    decimals: Optional[int] = None  # display rounding, None = auto
    matched: bool = False           # dictionary hit?

    @property
    def key(self) -> str:
        """Stable identity used by rules and profiles: the raw header."""
        return self.raw

    def header(self, template: str = "{name}\n[{unit}]", fallback_raw: bool = True) -> str:
        name = self.name or (self.raw if fallback_raw else "")
        if self.unit:
            return template.format(name=name, unit=self.unit, raw=self.raw)
        return name

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ColumnSpec":
        known = set(ColumnSpec.__dataclass_fields__)
        return ColumnSpec(**{k: v for k, v in d.items() if k in known})


# --------------------------------------------------------------------------- #
# Profile
# --------------------------------------------------------------------------- #
@dataclass
class ReaderOptions:
    encodings: list[str] = field(
        default_factory=lambda: ["utf-8-sig", "cp932", "shift_jis", "utf-16", "euc_jp", "latin-1"]
    )
    delimiters: list[str] = field(default_factory=lambda: [",", "\t", ";"])
    header_row: Optional[int] = None      # None = autodetect (0-based)
    skip_rows: int = 0
    sheet: Any = 0                        # sheet name or index for Excel input
    thousands: str = ","
    na_values: list[str] = field(
        default_factory=lambda: ["", "-", "--", "N/A", "NA", "null", "NULL", "#N/A", "***"]
    )


@dataclass
class IdentityOptions:
    """How to find the timestamp and the entity (PCS) a file belongs to."""

    time_column: Optional[str] = None
    time_candidates: list[str] = field(
        default_factory=lambda: [
            "日時", "日付", "時刻", "計測日時", "収集日時", "年月日", "時間",
            "timestamp", "time", "datetime", "date",
        ]
    )
    entity_label: str = "PCS"
    entity_from_filename: str = r"^(\d+)"            # regex, group 1 = entity id
    entity_column: Optional[str] = None              # or take it from a column
    entity_from_column: str = ""                     # wide files: r"^(PCS\d+)_(.+)$"
    strip_entity_in_name: bool = True                # "PCS01 ①直流電力" -> "①直流電力"
    source_from_filename: str = ""                   # r"\d+_([^_]+)_" -> "PCS監視"
    alarm_pattern: str = ""                          # r"^ANN_(\d+)$" -> alarm columns
    date_from_filename: str = r"(\d{4}[-_]\d{2}[-_]\d{2})"
    resample: str = ""                               # e.g. "1min", "5min", "" = keep raw


@dataclass
class DictionaryOptions:
    path: str = ""
    sheet: Any = 0
    raw_col: str = "auto"
    name_col: str = "auto"
    unit_col: str = "auto"
    note_col: str = "auto"
    kind_col: str = "auto"
    multi_block: bool = True      # several dictionary tables side by side on one sheet
    shared_markers: list[str] = field(
        default_factory=lambda: ["共通", "common"]
    )
    header_template: str = "{name}\n[{unit}]"
    keep_raw_row: bool = True     # write the original header in a second header row


# --------------------------------------------------------------------------- #
# Analysis features
# --------------------------------------------------------------------------- #
# Every optional piece of analysis is registered here once.  A feature is
# computed only when some output asks for it, and each output is chosen
# separately, so the same run can put the attribution table in Excel but keep
# the scatter plot out of the HTML.
ANALYSIS_FEATURES = (
    "attribution",   # DC-side vs inverter-side split of each unit's shortfall
    "availability",  # good irradiance but no output -> lost kWh
    "efficiency",    # DC -> AC conversion efficiency per unit
    "performance",   # performance ratio (needs nameplate kW)
    "findings",      # the short "what is wrong" summary
    "weather",       # irradiance + temperature as a time graph, same axis
    "scatter",       # power vs irradiance
    "tidy",          # long-format table for pivots
)
FEATURE_LABELS = {
    "attribution":  ("Fault attribution (DC vs inverter)", "不具合の切り分け（直流側／インバータ）"),
    "availability": ("Availability and lost kWh", "稼働状況・損失 kWh"),
    "efficiency":   ("DC → AC efficiency per unit", "号機別 直流→交流 変換効率"),
    "performance":  ("Performance ratio (PR)", "性能比（PR）"),
    "findings":     ("Findings summary", "所見サマリー"),
    "weather":      ("Irradiance + temperature graph", "日射量・気温グラフ（既定の追加グラフ）"),
    "scatter":      ("Power vs irradiance scatter", "出力対日射量の散布図"),
    "tidy":         ("Long-format table for pivots", "ピボット用の縦持ちデータ"),
}
# Which outputs each feature can even be put into.
FEATURE_OUTPUTS = {
    "attribution":  ("excel", "html"),
    "availability": ("excel", "html"),
    "efficiency":   ("excel", "html"),
    "performance":  ("excel", "html"),
    "findings":     ("excel", "html"),
    "weather":      ("html",),
    "scatter":      ("html",),
    "tidy":         ("excel",),
}
# Sensible defaults: everything that costs little, except the long-format sheet
# which duplicates the data and can be large.  The scatter is off by default:
# a time graph of the same two channels shows the pattern, which is what the
# reader is actually after.
FEATURE_DEFAULTS = {
    "attribution":  ["excel", "html"],
    "availability": ["excel", "html"],
    "efficiency":   ["excel", "html"],
    "performance":  ["excel", "html"],
    "findings":     ["excel", "html"],
    "weather":      ["html"],
    "scatter":      [],
    "tidy":         [],
}
# The features that go through core.diagnostics.  "weather" is drawn in the
# page from channels that already travel with the report, so it costs nothing
# on the Python side and must not drag the diagnostics run in with it.
DIAGNOSTIC_FEATURES = (
    "attribution", "availability", "efficiency", "performance",
    "findings", "scatter", "tidy",
)


def default_feature_targets() -> dict[str, list[str]]:
    return {k: list(v) for k, v in FEATURE_DEFAULTS.items()}


# The physical role a channel plays.  The diagnostics need to know which column
# is AC power and which is irradiance; hard-wiring "Data10" would break the
# moment this app is pointed at another plant, so roles are configurable and
# guessed from the dictionary names when left empty.
ROLE_HINTS = {
    "ac_power":   ("交流電力", "ac power", "ac_power", "有効電力"),
    "dc_power":   ("直流電力", "dc power", "dc_power"),
    "irradiance": ("日射", "irradian", "irradiance", "insolation"),
    "ac_energy":  ("交流電力量", "ac energy", "電力量"),
    "temp":       ("気温", "temperature", "外気温", "モジュール温度"),
}


@dataclass
class AnalysisOptions:
    metrics: list[str] = field(default_factory=list)   # raw column keys to cross-compare
    make_matrix: bool = True                           # time x entity per metric
    make_deviation: bool = True                        # value - fleet reference
    deviation_mode: str = "mean"                       # mean | median | max | best
    deviation_percent: bool = True
    # % deviation is meaningless when the reference is ~0 (night, dawn, dusk):
    # cells whose reference is below this share of the metric maximum stay blank.
    deviation_floor_pct: float = 2.0
    deviation_floor_abs: float = 0.0
    make_daily_summary: bool = True
    make_flags: bool = True                            # list every rule hit
    make_alarms: bool = True                           # summarise ANN_* alarm flags
    skip_shared_metrics: bool = True                   # 共通 channels are not comparable
    flag_exclude: list[str] = field(                   # kinds too common to list
        default_factory=lambda: ["zero", "blank"]
    )
    zero_counts: bool = True
    round_to: int = 3
    # Limit every output to one window - a 性能試験 usually covers a few hours,
    # not the whole day.  Empty strings mean "everything in the files".
    time_from: str = ""        # "YYYY-MM-DD HH:MM:SS"
    time_to: str = ""
    time_filter: bool = False  # honour the two fields above

    # ---- diagnostics ---------------------------------------------------- #
    # Which extra analysis to run, and where each result may go.
    feature_targets: dict[str, list[str]] = field(default_factory=default_feature_targets)
    # Channel roles.  Empty = guess from the dictionary names (see ROLE_HINTS).
    role_ac_power: str = ""
    role_dc_power: str = ""
    role_irradiance: str = ""
    role_ac_energy: str = ""
    role_temp: str = ""
    # Only compare units under decent, steady light - dawn, dusk and passing
    # clouds are most of the noise in a raw comparison.
    stable_only: bool = True
    stable_min_irradiance: float = 0.4     # same unit as the irradiance channel
    stable_tolerance_pct: float = 5.0      # max spread across the window
    stable_window: int = 5                 # samples
    # Nameplate DC capacity per unit, needed for the performance ratio.
    capacity_kw: float = 0.0               # one value for every unit
    capacity_by_unit: dict[str, float] = field(default_factory=dict)

    def wants(self, feature: str, output: str) -> bool:
        """Should ``feature`` be produced for ``output`` ("excel" / "html")?"""
        return output in (self.feature_targets or {}).get(feature, [])

    def any_target(self, feature: str) -> bool:
        return bool((self.feature_targets or {}).get(feature))

    def migrate_features(self) -> list[str]:
        """Fill in features that did not exist when this profile was saved.

        A profile written before 2.3.0 has no ``weather`` key.  The time graph
        of irradiance + temperature took the scatter's place as the default -
        it shows the same two channels against the clock, where the pattern is
        - so such a profile gains the graph and drops the scatter.  Everything
        the user has ticked since is left exactly as it is.
        """
        if self.feature_targets is None:
            self.feature_targets = {}
        notes: list[str] = []
        if "weather" not in self.feature_targets:
            self.feature_targets["weather"] = list(FEATURE_DEFAULTS["weather"])
            if self.feature_targets.get("scatter"):
                self.feature_targets["scatter"] = []
                notes.append(
                    "Profile updated: the irradiance + temperature graph replaced "
                    "the power-vs-irradiance scatter (tick the scatter again on the "
                    "Output tab to have both)")
            else:
                notes.append("Profile updated: irradiance + temperature graph is on")
        for key in ANALYSIS_FEATURES:
            self.feature_targets.setdefault(key, list(FEATURE_DEFAULTS.get(key, [])))
        return notes

    def capacity_for(self, entity: str) -> float:
        return float(self.capacity_by_unit.get(entity, self.capacity_kw) or 0.0)


@dataclass
class ColumnFilter:
    """Show / hide columns by what their name contains."""

    enabled: bool = False
    contains: list[str] = field(default_factory=list)   # any match -> visible
    exclude: list[str] = field(default_factory=list)    # any match -> hidden
    keep_first_column: bool = True     # the label/time column is always kept
    case_sensitive: bool = False
    use_raw_name: bool = True          # match the original header too

    def matches(self, raw: str, name: str = "") -> Optional[bool]:
        """True = show, False = hide, None = the filter has no opinion.

        While the filter is on the answer is always definite, so pressing
        Apply twice cannot leave columns hidden by an older, unrelated
        filter.  An empty "show only" box means "everything that is not
        excluded".
        """
        if not self.enabled:
            return None
        hay = [raw or "", name or ""] if self.use_raw_name else [name or raw or ""]
        if not self.case_sensitive:
            hay = [h.lower() for h in hay]

        def hit(needles: list[str]) -> bool:
            for n in needles:
                n = n if self.case_sensitive else n.lower()
                if n and any(n in h for h in hay):
                    return True
            return False

        if self.exclude and hit(self.exclude):
            return False
        if self.contains:
            return hit(self.contains)
        return True          # nothing to match against -> keep it


@dataclass
class ChartSpec:
    """One user-defined chart: pick X, pick Y, decide how to group."""

    title: str = ""
    x_column: str = ""                 # "" = the detected time column
    y_columns: list[str] = field(default_factory=list)
    group_mode: str = "single"         # single | per_column | pattern | per_entity
    patterns: list[str] = field(default_factory=list)   # for group_mode="pattern"
    entities: list[str] = field(default_factory=list)   # "" / empty = every unit
    chart_type: str = "line"           # line | scatter | bar
    normalize: bool = False            # plot each series as % of its own maximum
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ChartSpec":
        known = set(ChartSpec.__dataclass_fields__)
        return ChartSpec(**{k: v for k, v in d.items() if k in known})


# The optional blocks of the HTML report, in the order they appear.
HTML_SECTIONS = ("chart", "matrix", "daily", "zeros", "alarms", "quality")
HTML_SECTION_LABELS = {
    "chart":   ("Chart + KPI tiles", "グラフ・KPI"),
    "matrix":  ("Matrix (time x unit)", "時刻ごとの比較"),
    "daily":   ("Daily summary", "日別サマリー"),
    "zeros":   ("Zero report", "ゼロ値レポート"),
    "alarms":  ("Status / alarm bits", "状態・警報ビット"),
    "quality": ("Source files", "読み込み情報"),
}


class ContentScope(str, Enum):
    """How much of the data one output file gets.

    The three outputs are configured separately: a cleaned workbook usually
    wants every visible column, the analysis workbook only the few metrics
    worth comparing, and the HTML report everything - because there the reader
    picks what to look at inside the page.
    """

    SELECTED = "selected"   # only the metrics ticked on the Analysis tab
    VISIBLE = "visible"     # every column the Columns tab left visible
    ALL = "all"             # everything in the file, ignoring the column filter

    def label(self) -> str:
        return {"selected": "Selected metrics only",
                "visible": "All visible columns",
                "all": "Everything in the file"}[self.value]


@dataclass
class OutputOptions:
    suffix: str = "_cleaned"
    # --- which files to produce (each independent of the others) ---------- #
    write_excel: bool = True        # <source>_cleaned.xlsx per input file
    combined_workbook: bool = True  # one PCS_analysis_*.xlsx across the units
    write_html: bool = True         # PCS_report_*.html
    write_png: bool = False         # charts/*.png
    # --- what goes into each of them ------------------------------------- #
    cleaned_scope: str = ContentScope.VISIBLE.value
    analysis_scope: str = ContentScope.SELECTED.value
    html_scope: str = ContentScope.ALL.value   # the reader filters in the page
    analysis_max_metrics: int = 12   # sheets per metric add up fast
    html_extra_channels: int = 40    # cap for the channels beyond the analysis ones
    html_extra_points: int = 1500    # points per series for those extra channels
    html_worst_n: int = 6            # how many units the report opens with
    html_language: str = "ja"        # ja | en | both | auto (auto = follow the app)
    # Which blocks the report contains.  Anything left out is not merely hidden:
    # its data never enters the file, so the report also gets smaller.
    html_sections: list[str] = field(
        default_factory=lambda: list(HTML_SECTIONS))
    html_color_controls: bool = True  # let the reader change the gradient in the page
    max_html_points: int = 4000      # per series, downsampled beyond this
    # --- formatting details ---------------------------------------------- #
    excel_charts: bool = True
    freeze_panes: bool = True
    autofilter: bool = True
    column_width: int = 14
    out_dir: str = ""               # "" = beside the input files
    split_sheets_by_entity: bool = True   # wide files: one readable sheet per PCS
    cf_only_selected: bool = False        # conditional formatting only on chosen metrics
    max_cf_columns: int = 120             # keep Excel responsive on very wide files
    # --- deprecated, kept so 1.4.0 profiles still load ------------------- #
    html_all_channels: Optional[bool] = None

    def __post_init__(self) -> None:
        # 1.4.0 stored a bool; 1.5.0 stores a scope.  Honour the old flag once.
        if self.html_all_channels is not None:
            self.html_scope = (ContentScope.ALL.value if self.html_all_channels
                               else ContentScope.SELECTED.value)
            self.html_all_channels = None
        if str(self.html_language) not in {"ja", "en", "both", "auto"}:
            self.html_language = "ja"
        keep = [x for x in (self.html_sections or []) if x in HTML_SECTIONS]
        self.html_sections = keep or list(HTML_SECTIONS)
        for name in ("cleaned_scope", "analysis_scope", "html_scope"):
            value = str(getattr(self, name))
            if value not in {s.value for s in ContentScope}:
                setattr(self, name, ContentScope.VISIBLE.value)

    def scope(self, which: str) -> ContentScope:
        """``opts.scope("html")`` -> :class:`ContentScope`."""
        return ContentScope(getattr(self, f"{which}_scope"))

    def any_output(self) -> bool:
        return bool(self.write_excel or self.combined_workbook
                    or self.write_html or self.write_png)


@dataclass
class Profile:
    """Everything that makes DataScope work on one kind of data."""

    name: str = "default"
    description: str = ""
    version: int = 1
    reader: ReaderOptions = field(default_factory=ReaderOptions)
    identity: IdentityOptions = field(default_factory=IdentityOptions)
    dictionary: DictionaryOptions = field(default_factory=DictionaryOptions)
    analysis: AnalysisOptions = field(default_factory=AnalysisOptions)
    output: OutputOptions = field(default_factory=OutputOptions)
    rules: list[Rule] = field(default_factory=list)
    columns: dict[str, dict[str, Any]] = field(default_factory=dict)  # raw -> ColumnSpec overrides
    column_filter: ColumnFilter = field(default_factory=ColumnFilter)
    charts: list[ChartSpec] = field(default_factory=list)

    # ---------------------------------------------------------------- #
    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "reader": asdict(self.reader),
            "identity": asdict(self.identity),
            "dictionary": asdict(self.dictionary),
            "analysis": asdict(self.analysis),
            "output": asdict(self.output),
            "rules": [r.to_dict() for r in self.rules],
            "columns": self.columns,
            "column_filter": asdict(self.column_filter),
            "charts": [c.to_dict() for c in self.charts],
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Profile":
        def sub(cls, key):
            raw = d.get(key) or {}
            known = set(cls.__dataclass_fields__)
            return cls(**{k: v for k, v in raw.items() if k in known})

        prof = Profile(
            name=d.get("name", "default"),
            description=d.get("description", ""),
            version=int(d.get("version", 1)),
            reader=sub(ReaderOptions, "reader"),
            identity=sub(IdentityOptions, "identity"),
            dictionary=sub(DictionaryOptions, "dictionary"),
            analysis=sub(AnalysisOptions, "analysis"),
            output=sub(OutputOptions, "output"),
            rules=[Rule.from_dict(r) for r in d.get("rules", [])],
            columns=d.get("columns", {}),
            column_filter=sub(ColumnFilter, "column_filter"),
            charts=[ChartSpec.from_dict(c) for c in d.get("charts", [])],
        )
        # a profile saved by an older version must not lose features silently
        prof.analysis.migrate_features()
        return prof


def default_rules() -> list[Rule]:
    """A sensible starting set for SCADA / PCS data."""
    return [
        Rule(kind=RuleKind.ZERO, label="", scope=ScopeKind.NUMERIC,
             fill=FILL["grey"], font=FONT["grey"]),
        Rule(kind=RuleKind.BLANK, label="", scope=ScopeKind.ALL,
             fill=FILL["violet"], font=FONT["black"]),
        Rule(kind=RuleKind.NEGATIVE, label="", scope=ScopeKind.NUMERIC,
             fill=FILL["red"], font=FONT["red"]),
        Rule(kind=RuleKind.COLOR_SCALE, label="", scope=ScopeKind.NUMERIC,
             enabled=False),
        Rule(kind=RuleKind.STUCK, label="", scope=ScopeKind.NUMERIC,
             value2=10, fill=FILL["orange"], font=FONT["black"], enabled=False),
        Rule(kind=RuleKind.OUTLIER_SIGMA, label="",
             scope=ScopeKind.NUMERIC, value2=4.0, fill=FILL["yellow"], font=FONT["black"],
             enabled=False),
    ]


def project_root() -> Path:
    """Folder that holds profiles/, works both from source and from a PyInstaller EXE."""
    import sys

    if getattr(sys, "frozen", False):
        # PyInstaller unpacks --add-data into _MEIPASS (one-file *and* one-folder)
        base = getattr(sys, "_MEIPASS", None)
        return Path(base) if base else Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def user_data_dir() -> Path:
    """Writable folder for settings / logs (never beside a Program Files EXE)."""
    import os
    import sys

    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    p = base / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p
