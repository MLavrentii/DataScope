"""Robust reader for SCADA exports (CSV / TSV / XLSX).

Handles, without asking the user:

* encoding (utf-8-sig, cp932/shift_jis, utf-16, euc-jp ...)
* delimiter (``,`` / tab / ``;``)
* junk metadata lines before the real header
* a second header row that only carries units
* Japanese / full-width numbers, thousands separators, ``-`` as "no value"
* the timestamp column, whatever it is called
* the entity (PCS) id, taken from the file name or from a column
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from .dictionary import looks_like_unit, normalize
from .models import IdentityOptions, ReaderOptions

EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xltx", ".xls"}
CSV_SUFFIXES = {".csv", ".txt", ".tsv", ".dat"}


@dataclass
class LoadedTable:
    """One source file, cleaned up but not yet translated."""

    path: Path
    df: pd.DataFrame
    time_column: Optional[str]
    entity: str
    day: Optional[str]
    encoding: str = ""
    delimiter: str = ""
    header_row: int = 0
    unit_row: dict[str, str] = field(default_factory=dict)
    numeric_columns: list[str] = field(default_factory=list)
    text_columns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.entity} {self.day}" if self.day else self.entity


# --------------------------------------------------------------------------- #
# low level
# --------------------------------------------------------------------------- #
def sniff_encoding(path: Path, candidates: list[str]) -> str:
    head = path.open("rb").read(200_000)
    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        return "utf-16"
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    for enc in candidates:
        try:
            head.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(head).best()
        if best and best.encoding:
            return best.encoding
    except Exception:            # pragma: no cover - optional dependency
        pass
    return "latin-1"


def sniff_delimiter(sample: str, candidates: list[str]) -> str:
    lines = [ln for ln in sample.splitlines() if ln.strip()][:40]
    best, best_score = candidates[0] if candidates else ",", -1.0
    for d in candidates:
        counts = [ln.count(d) for ln in lines]
        if not counts or max(counts) == 0:
            continue
        # prefer the delimiter with the most fields and the most stable count
        common = max(set(counts), key=counts.count)
        stability = counts.count(common) / len(counts)
        score = common * stability
        if score > best_score:
            best, best_score = d, score
    return best


def _cell_is_number(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, (int, float, np.integer, np.floating)) and not pd.isna(v):
        return True
    s = unicodedata.normalize("NFKC", str(v)).strip().replace(",", "")
    if not s:
        return False
    return bool(re.fullmatch(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?", s))


def _looks_like_time(v: Any) -> bool:
    s = unicodedata.normalize("NFKC", str(v)).strip()
    if not s:
        return False
    return bool(
        re.match(r"^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}", s)
        or re.match(r"^\d{1,2}[:：]\d{2}", s)
        or re.match(r"^\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{1,2}:\d{2}", s)
    )


def _filled(row) -> list:
    return [v for v in row if v is not None and not (isinstance(v, float) and pd.isna(v))]


def _is_data_row(row) -> bool:
    vals = _filled(row)
    if len(vals) < 2:
        return False
    hits = sum(1 for v in vals if _cell_is_number(v) or _looks_like_time(v))
    return hits / len(vals) >= 0.6


def detect_layout(raw: pd.DataFrame, max_scan: int = 40) -> tuple[int, int]:
    """Return ``(header_row, data_start)``.

    Real SCADA exports look like::

        PCS監視データ出力          <- junk
        出力日時,2026/08/06        <- junk
        計測日時,DC_V,DC_KW,...    <- header      (returned as header_row)
        ,V,kW,...                  <- unit row    (part of the header block)
        2026/08/06 00:00,780,0,... <- data        (returned as data_start)

    The header is the *first* text row of the block that directly precedes the
    numeric block - not the last one, which is usually the unit row.
    """
    n = len(raw)
    first_data = None
    for i in range(min(max_scan + 8, n)):
        if _is_data_row(raw.iloc[i]) and (
            i + 1 >= n or _is_data_row(raw.iloc[i + 1]) or i + 1 == n
        ):
            first_data = i
            break
    if first_data is None:
        # no numeric block found: fall back to the widest early row
        widths = [(len(_filled(raw.iloc[i])), -i, i) for i in range(min(max_scan, n))]
        return (max(widths)[2] if widths else 0), (max(widths)[2] + 1 if widths else 1)

    if first_data == 0:
        return 0, 0

    data_width = len(_filled(raw.iloc[first_data]))
    header_row = first_data - 1
    j = first_data - 1
    while j >= 0:
        w = len(_filled(raw.iloc[j]))
        if w >= max(2, data_width * 0.5) and not _is_data_row(raw.iloc[j]):
            header_row = j
            j -= 1
        else:
            break
    return header_row, first_data


def _dedupe(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    out = []
    for n in names:
        n = n if n else "col"
        if n in seen:
            seen[n] += 1
            out.append(f"{n}#{seen[n]}")
        else:
            seen[n] = 0
            out.append(n)
    return out


def to_number(series: pd.Series, thousands: str = ",") -> pd.Series:
    """Coerce a text column to float, tolerating full-width digits and commas."""
    if pd.api.types.is_numeric_dtype(series):
        return series.astype("float64")
    s = series.astype("string")
    s = s.map(lambda v: unicodedata.normalize("NFKC", v) if isinstance(v, str) else v)
    if thousands:
        s = s.str.replace(thousands, "", regex=False)
    s = s.str.replace("　", "", regex=False).str.strip()
    s = s.replace({"": None, "-": None, "--": None, "***": None, "N/A": None,
                   "NA": None, "#N/A": None, "null": None, "NULL": None})
    return pd.to_numeric(s, errors="coerce")


def parse_timestamps(series: pd.Series) -> pd.Series:
    """Parse a mixed Japanese/ISO timestamp column, without noisy warnings."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    s = series.astype("string").map(
        lambda v: unicodedata.normalize("NFKC", v).strip() if isinstance(v, str) else v
    )
    s = (s.str.replace("年", "-", regex=False)
           .str.replace("月", "-", regex=False)
           .str.replace("日", " ", regex=False)
           .str.replace("時", ":", regex=False)
           .str.replace("分", ":", regex=False)
           .str.replace("秒", "", regex=False)
           .str.replace("：", ":", regex=False)
           .str.strip())
    out = pd.to_datetime(s, errors="coerce", format="mixed")
    if out.notna().mean() < 0.5:
        out = pd.to_datetime(s, errors="coerce", dayfirst=False)
    return out


# --------------------------------------------------------------------------- #
# main entry point
# --------------------------------------------------------------------------- #
SMALL_FILE_BYTES = 25 * 1024 * 1024


def read_raw(path: Path, opts: ReaderOptions) -> tuple[pd.DataFrame, str, str]:
    """Read a file into an all-object DataFrame with **no** header applied.

    Ragged SCADA exports (title lines with one field, then 30-field data rows)
    break pandas' own header inference, so small files are parsed with the csv
    module and padded to the widest row; big files take the fast C engine with
    an explicit column count.
    """
    import csv

    suffix = path.suffix.lower()
    if suffix in EXCEL_SUFFIXES:
        df = pd.read_excel(path, sheet_name=opts.sheet, header=None, dtype=object)
        return df, "", ""

    enc = sniff_encoding(path, opts.encodings)
    with path.open("r", encoding=enc, errors="replace", newline="") as fh:
        sample = fh.read(200_000)
    delim = sniff_delimiter(sample, opts.delimiters)

    size = path.stat().st_size
    if size <= SMALL_FILE_BYTES:
        with path.open("r", encoding=enc, errors="replace", newline="") as fh:
            rows = [r for r in csv.reader(fh, delimiter=delim)]
        rows = [r for r in rows if any(c.strip() for c in r)] or [[]]
        width = max(len(r) for r in rows)
        padded = [[(c.strip() if c.strip() != "" else None) for c in r]
                  + [None] * (width - len(r)) for r in rows]
        return pd.DataFrame(padded, dtype=object), enc, delim

    # large file: find the widest row from a generous sample, then stream it
    with path.open("r", encoding=enc, errors="replace", newline="") as fh:
        width = 0
        for i, row in enumerate(csv.reader(fh, delimiter=delim)):
            width = max(width, len(row))
            if i >= 20000:
                break
    df = pd.read_csv(
        path,
        header=None,
        names=list(range(max(width, 1))),
        dtype=object,
        encoding=enc,
        encoding_errors="replace",
        sep=delim,
        engine="c",
        skipinitialspace=True,
        on_bad_lines="warn",
    )
    return df, enc, delim


def load_table(path: Path, reader: ReaderOptions, identity: IdentityOptions) -> LoadedTable:
    path = Path(path)
    raw, enc, delim = read_raw(path, reader)
    notes: list[str] = []
    if raw.empty:
        raise ValueError(f"{path.name}: file is empty")

    if reader.skip_rows:
        raw = raw.iloc[reader.skip_rows:].reset_index(drop=True)

    if reader.header_row is not None:
        hdr = max(0, min(reader.header_row, len(raw) - 1))
        data_start = hdr + 1
        while data_start < len(raw) and not _is_data_row(raw.iloc[data_start]):
            data_start += 1
    else:
        hdr, data_start = detect_layout(raw)
    header_cells = ["" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()
                    for v in raw.iloc[hdr]]

    # rows between the header and the data: unit row(s) or a second name row
    unit_by_index: dict[int, str] = {}
    extra_names: dict[int, list[str]] = {}
    for r in range(hdr + 1, min(data_start, len(raw))):
        row = raw.iloc[r]
        vals = _filled(row)
        if not vals:
            continue
        unit_like = sum(1 for v in vals if looks_like_unit(v) and not _cell_is_number(v))
        as_unit = unit_like / len(vals) > 0.5
        for i, v in enumerate(row):
            if v is None or (isinstance(v, float) and pd.isna(v)):
                continue
            text = str(v).strip()
            if not text:
                continue
            if as_unit:
                unit_by_index.setdefault(i, text)
            else:
                extra_names.setdefault(i, []).append(text)
    if unit_by_index:
        notes.append("unit row detected below the header")
    if extra_names:
        notes.append("multi-row header combined")

    # forward-fill merged/blank header cells (common in SCADA exports)
    filled_header: list[str] = []
    last = ""
    for i, h in enumerate(header_cells):
        parts = [h] if h else ([f"{last}"] if last else [])
        parts += extra_names.get(i, [])
        text = " ".join(p for p in parts if p).strip()
        if h:
            last = h
        if not text:
            text = f"col{i}"
        filled_header.append(text)
    columns = _dedupe(filled_header)
    unit_row: dict[str, str] = {columns[i]: u for i, u in unit_by_index.items()
                               if i < len(columns)}

    df = raw.iloc[data_start:].reset_index(drop=True)
    df.columns = columns
    # drop fully empty rows / columns
    df = df.dropna(axis=0, how="all")
    empty_cols = [c for c in df.columns if df[c].isna().all()]
    if empty_cols:
        df = df.drop(columns=empty_cols)
        notes.append(f"dropped {len(empty_cols)} empty column(s)")

    # -- timestamp -------------------------------------------------------- #
    time_col = identity.time_column if identity.time_column in df.columns else None
    if time_col is None:
        best, best_ratio = None, 0.0
        for c in df.columns:
            head = df[c].dropna().head(30)
            if head.empty:
                continue
            ratio = sum(_looks_like_time(v) for v in head) / len(head)
            hinted = any(normalize(h) in normalize(c) for h in identity.time_candidates)
            ratio += 0.35 if hinted else 0.0
            if ratio > best_ratio:
                best, best_ratio = c, ratio
        if best is not None and best_ratio >= 0.5:
            time_col = best
    if time_col is not None:
        parsed = parse_timestamps(df[time_col])
        good = float(parsed.notna().mean())
        if good < 0.5:
            notes.append(f"timestamp column '{time_col}' parsed poorly ({good:.0%}) - kept as text")
            time_col = None
        else:
            df[time_col] = parsed
            df = df[df[time_col].notna()].reset_index(drop=True)
            # a date-only column plus a separate time column
            if (df[time_col].dt.hour == 0).all() and (df[time_col].dt.minute == 0).all():
                for c in df.columns:
                    if c == time_col:
                        continue
                    head = df[c].dropna().astype(str).head(20)
                    if len(head) and all(re.match(r"^\d{1,2}[:：]\d{2}", unicodedata.normalize("NFKC", v))
                                         for v in head):
                        try:
                            tod = pd.to_timedelta(
                                df[c].astype(str).str.replace("：", ":", regex=False)
                                .str.replace(r"^(\d{1,2}:\d{2})$", r"\1:00", regex=True)
                            )
                            df[time_col] = df[time_col] + tod
                            notes.append(f"merged time-of-day column '{c}' into the timestamp")
                        except Exception:
                            pass
                        break
            df = df.sort_values(time_col).reset_index(drop=True)

    # -- numeric coercion -------------------------------------------------- #
    numeric_cols, text_cols = [], []
    for c in df.columns:
        if c == time_col:
            continue
        col = df[c]
        if pd.api.types.is_numeric_dtype(col):
            numeric_cols.append(c)
            df[c] = col.astype("float64")
            continue
        head = col.dropna()
        if head.empty:
            text_cols.append(c)
            df[c] = col.astype("string")
            continue
        converted = to_number(col, reader.thousands)
        ratio = float(converted.notna().sum()) / max(int(head.shape[0]), 1)
        if ratio >= 0.8:
            df[c] = converted
            numeric_cols.append(c)
        else:
            df[c] = col.astype("string")
            text_cols.append(c)

    # -- entity + day ------------------------------------------------------ #
    entity = ""
    if identity.entity_column and identity.entity_column in df.columns:
        vals = df[identity.entity_column].dropna().astype(str).unique()
        if len(vals):
            entity = str(vals[0])
    if not entity and identity.entity_from_filename:
        m = re.search(identity.entity_from_filename, path.stem)
        if m:
            entity = m.group(1) if m.groups() else m.group(0)
    if not entity and identity.source_from_filename:
        m = re.search(identity.source_from_filename, path.stem)
        if m:
            entity = (m.group(1) if m.groups() else m.group(0))
    if not entity:
        entity = path.stem
    if identity.entity_label and entity.isdigit():
        entity = f"{identity.entity_label}{entity}"

    day = None
    if identity.date_from_filename:
        m = re.search(identity.date_from_filename, path.stem)
        if m:
            day = m.group(1).replace("_", "-")
    if day is None and time_col is not None and len(df):
        day = str(pd.Timestamp(df[time_col].iloc[0]).date())

    # -- optional resampling ---------------------------------------------- #
    if identity.resample and time_col is not None and numeric_cols:
        agg = {c: "mean" for c in numeric_cols}
        for c in text_cols:
            agg[c] = "first"
        df = (df.set_index(time_col)
                .resample(identity.resample)
                .agg(agg)
                .dropna(how="all")
                .reset_index())
        notes.append(f"resampled to {identity.resample}")

    return LoadedTable(
        path=path,
        df=df,
        time_column=time_col,
        entity=entity,
        day=day,
        encoding=enc,
        delimiter=delim,
        header_row=hdr,
        unit_row=unit_row,
        numeric_columns=numeric_cols,
        text_columns=text_cols,
        notes=notes,
    )


def discover_files(folder: Path, patterns: tuple[str, ...] = ("*.csv", "*.CSV", "*.xlsx", "*.txt")
                   ) -> list[Path]:
    out: list[Path] = []
    for p in patterns:
        out.extend(sorted(Path(folder).glob(p)))
    return [p for p in out if not p.name.startswith("~$")]
