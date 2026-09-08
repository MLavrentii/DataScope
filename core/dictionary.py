"""Column dictionary: turn cryptic SCADA headers into readable names + units.

The dictionary lives in a spreadsheet (for the Aomori plant:
``CSV項目名称.xlsx``).  Its layout is *not* hard-coded: the columns that hold
the raw key, the readable name, the unit and the note are auto-detected, and
can be overridden from the profile so the same code serves other projects.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

from .models import ColumnSpec, DictionaryOptions

# --------------------------------------------------------------------------- #
# header text normalisation
# --------------------------------------------------------------------------- #
_BRACKETS = re.compile(r"[\[\](){}（）［］｛｝]")
_SPACES = re.compile(r"[\s　_\-・/\\.]+")
_UNIT_IN_NAME = re.compile(
    r"[\[\(（［]\s*([^\]\)）］]{1,12})\s*[\]\)）］]\s*$"
)

# Strings that look like a unit, used when guessing the unit column.
_UNIT_HINTS = {
    "kw", "kwh", "kvar", "kva", "mw", "mwh", "w", "wh", "v", "a", "ma", "hz",
    "%", "℃", "°c", "c", "k", "pa", "kpa", "mpa", "m/s", "m2", "w/m2", "w/m²",
    "kw/m2", "min", "s", "h", "sec", "hour", "count", "回", "件", "時間", "分",
    "秒", "度", "kvarh", "var", "pf", "ohm", "Ω", "kΩ",
}


def normalize(text: Any) -> str:
    """Aggressive normalisation for matching: NFKC, lower, drop spaces/brackets."""
    if text is None:
        return ""
    s = unicodedata.normalize("NFKC", str(text)).strip()
    s = _BRACKETS.sub(" ", s)
    s = _SPACES.sub("", s)
    return s.lower()


_NOT_A_UNIT = re.compile(r"^[+-]?\d{1,2}:\d{2}$")      # +09:00 is a timezone


def split_unit(name: Any) -> tuple[str, str]:
    """``"直流電圧(V)"`` -> ``("直流電圧", "V")``; ``"Time[+09:00]"`` keeps its name."""
    s = "" if name is None else str(name).strip()
    m = _UNIT_IN_NAME.search(s)
    if m:
        unit = m.group(1).strip()
        if (unit and len(unit) <= 12 and normalize(unit) not in ("", "-")
                and not _NOT_A_UNIT.match(unit)):
            return s[: m.start()].strip(), unit
    return s, ""


def looks_like_unit(value: Any) -> bool:
    s = "" if value is None else str(value).strip()
    if not s or s in {"-", "--"}:
        return False
    if len(s) > 12:
        return False
    return unicodedata.normalize("NFKC", s).lower() in _UNIT_HINTS or bool(
        re.fullmatch(r"[A-Za-zΩ°℃%/·\^0-9\-]{1,10}", s)
    )


# --------------------------------------------------------------------------- #
# reading the dictionary file
# --------------------------------------------------------------------------- #
# "項目" alone is ambiguous in Japanese sheets (項目名称 = item *name*), so the key
# family only keeps markers that really mean "machine identifier".
_KEY_HINTS = ("csv", "code", "コード", "key", "id", "tag", "タグ", "signal", "point",
              "列名", "column", "raw", "item")
_NAME_HINTS = ("項目名称", "名称", "名前", "説明", "日本語", "name", "title", "label",
               "意味", "内容", "表示")
_UNIT_HINTS_HDR = ("単位", "unit", "単位系")
_NOTE_HINTS = ("備考", "注記", "note", "remark", "コメント", "comment", "詳細")
_KIND_HINTS = ("計測値種別", "種別", "区分", "type", "kind", "measurement", "集計")


def _score(header: Any, hints: Iterable[str]) -> int:
    h = normalize(header)
    return sum(1 for hint in hints if normalize(hint) and normalize(hint) in h)


_CODE_RE = re.compile(r"^[A-Za-z0-9_().\-]+$")


def _code_ratio(values: Iterable[Any]) -> float:
    """How many values look like machine identifiers (PCS01_Data01, Opt_Data21)."""
    vals = [str(v).strip() for v in values
            if v is not None and not (isinstance(v, float) and pd.isna(v)) and str(v).strip()]
    if not vals:
        return 0.0
    return sum(1 for v in vals if _CODE_RE.match(v)) / len(vals)


_STRONG_KEY = ("csv", "code", "コード", "tag", "タグ", "key", "id", "raw", "列名")


def _classify(header: Any) -> Optional[str]:
    """Which family a header belongs to: key / name / unit / kind / note."""
    h = normalize(header)
    if any(normalize(m) in h for m in _STRONG_KEY):
        return "key"           # "CSV名称" is a key column, not a name column
    fams = {
        "key": _score(header, _KEY_HINTS),
        "name": _score(header, _NAME_HINTS),
        "unit": _score(header, _UNIT_HINTS_HDR),
        "kind": _score(header, _KIND_HINTS),
        "note": _score(header, _NOTE_HINTS),
    }
    best = max(fams.values())
    if best <= 0:
        return None
    winners = [k for k, v in fams.items() if v == best]
    if len(winners) == 1:
        return winners[0]
    for pref in ("name", "unit", "kind", "note", "key"):   # 名称 beats a bare csv/item hit
        if pref in winners:
            return pref
    return winners[0]


def _read_any_table(path: Path, sheet: Any = 0) -> pd.DataFrame:
    """Read xlsx/xls/csv into a raw DataFrame with *no* header interpretation."""
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm", ".xltx", ".xls"):
        return pd.read_excel(path, sheet_name=sheet, header=None, dtype=object)
    for enc in ("utf-8-sig", "cp932", "shift_jis", "utf-16", "latin-1"):
        try:
            return pd.read_csv(path, header=None, dtype=object, encoding=enc,
                               engine="python", sep=None)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ValueError(f"Could not decode {path.name}")


def _find_header_row(raw: pd.DataFrame, max_scan: int = 12) -> int:
    """Row index whose cells look most like a header (hint words + few blanks)."""
    best, best_score = 0, -1
    for i in range(min(max_scan, len(raw))):
        row = raw.iloc[i]
        filled = int(row.notna().sum())
        if filled < 2:
            continue
        score = filled
        for cell in row:
            score += 3 * _score(cell, _KEY_HINTS + _NAME_HINTS + _UNIT_HINTS_HDR)
        if score > best_score:
            best, best_score = i, score
    return best


@dataclass
class Entry:
    """One dictionary row."""

    name: str
    unit: str = ""
    note: str = ""
    kind: str = ""          # 計測値種別 (瞬時値 / １分毎の差分値 …)
    shared: bool = False    # 備考 says the sensor is common to all entities

    def as_tuple(self) -> tuple[str, str, str]:
        return self.name, self.unit, self.note


class ColumnDictionary:
    """Maps raw header text -> :class:`Entry`.

    Lookup order: exact -> normalised -> normalised-without-unit ->
    substring -> fuzzy (>= ``fuzzy_cutoff``).  Everything unmatched keeps its
    original header, and :meth:`unmatched` reports it so the user can extend
    the dictionary file.
    """

    def __init__(self, fuzzy_cutoff: float = 0.86) -> None:
        self.fuzzy_cutoff = fuzzy_cutoff
        self.entries: dict[str, Entry] = {}       # exact raw key
        self._norm: dict[str, Entry] = {}          # normalised key
        self._misses: list[str] = []
        self.source: Optional[Path] = None
        self.detected: dict[str, Any] = {}
        self.blocks: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    def __len__(self) -> int:
        return len(self.entries)

    def add(self, raw: Any, name: str, unit: str = "", note: str = "",
            kind: str = "", shared: bool = False) -> None:
        key = str(raw).strip()
        if not key:
            return
        name = (name or "").strip()
        if not name:
            name = key
        if not unit:
            name, unit = split_unit(name)
        rec = Entry(name=name, unit=(unit or "").strip(), note=(note or "").strip(),
                    kind=(kind or "").strip(), shared=bool(shared))
        self.entries.setdefault(key, rec)
        self._norm.setdefault(normalize(key), rec)
        # also index by the readable name, so already-translated files re-match
        self._norm.setdefault(normalize(name), rec)

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, opts: DictionaryOptions) -> "ColumnDictionary":
        d = cls()
        if not opts.path:
            return d
        path = Path(opts.path)
        if not path.exists():
            raise FileNotFoundError(f"Dictionary file not found: {path}")
        raw = _read_any_table(path, opts.sheet)
        if raw.empty:
            return d

        hdr_row = _find_header_row(raw)
        header = [("" if pd.isna(v) else str(v).strip()) for v in raw.iloc[hdr_row]]
        titles = ([("" if pd.isna(v) else str(v).strip()) for v in raw.iloc[hdr_row - 1]]
                  if hdr_row > 0 else [""] * len(header))
        body = raw.iloc[hdr_row + 1:].reset_index(drop=True)
        body.columns = range(body.shape[1])
        n_cols = body.shape[1]

        # ---- where do the dictionary tables start? ----------------------- #
        # A sheet can hold several tables side by side (PCS監視 / 受電監視 /
        # 出力制御 in one row).  Every column whose header looks like a key
        # column opens a new block; the block ends at the next one.
        key_cols: list[int] = []
        if opts.raw_col and opts.raw_col != "auto":
            for i, h in enumerate(header):
                if normalize(h) == normalize(opts.raw_col):
                    key_cols.append(i)
            if not key_cols and opts.raw_col.isdigit():
                key_cols = [int(opts.raw_col)]
        if not key_cols:
            for i, h in enumerate(header):
                if i >= n_cols or not h:
                    continue
                fam = _classify(h)
                ratio = _code_ratio(body[i])
                if fam == "key" and ratio >= 0.5:
                    key_cols.append(i)
                elif fam in (None, "name") and ratio >= 0.6 and _score(h, _KEY_HINTS) > 0:
                    key_cols.append(i)
        if not opts.multi_block:
            key_cols = key_cols[:1]
        if not key_cols:
            key_cols = [0]

        bounds = list(zip(key_cols, key_cols[1:] + [n_cols]))
        shared_markers = [normalize(m) for m in (opts.shared_markers or []) if m]

        def pick(family: str, hints, lo: int, hi: int, requested: str,
                 used: set[int]) -> Optional[int]:
            if requested and requested != "auto":
                for i in range(lo, hi):
                    if i < len(header) and normalize(header[i]) == normalize(requested):
                        return i
            exact = [i for i in range(lo, min(hi, len(header)))
                     if i not in used and _classify(header[i]) == family]
            if exact:
                return exact[0]
            scored = [(_score(header[i], hints), -i, i)
                      for i in range(lo, min(hi, len(header)))
                      if i not in used and _score(header[i], hints) > 0]
            return max(scored)[2] if scored else None

        total = 0
        for key_idx, end in bounds:
            used = {key_idx}
            name_idx = pick("name", _NAME_HINTS, key_idx + 1, end, opts.name_col, used)
            if name_idx is not None:
                used.add(name_idx)
            unit_idx = pick("unit", _UNIT_HINTS_HDR, key_idx + 1, end, opts.unit_col, used)
            if unit_idx is not None:
                used.add(unit_idx)
            kind_idx = pick("kind", _KIND_HINTS, key_idx + 1, end, opts.kind_col, used)
            if kind_idx is not None:
                used.add(kind_idx)
            note_idx = pick("note", _NOTE_HINTS, key_idx + 1, end, opts.note_col, used)

            if name_idx is None:
                name_idx = key_idx + 1 if key_idx + 1 < end else key_idx
            if unit_idx is None:
                for i in range(key_idx + 1, min(end, n_cols)):
                    if i in (key_idx, name_idx):
                        continue
                    col = body[i].dropna()
                    if len(col) and (sum(looks_like_unit(v) for v in col) / len(col)) > 0.6:
                        unit_idx = i
                        break

            def cell(row, idx) -> str:
                if idx is None or idx >= n_cols:
                    return ""
                v = row.get(idx)
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    return ""
                return str(v).strip()

            count = 0
            for _, row in body.iterrows():
                key = cell(row, key_idx)
                if not key:
                    continue
                unit = cell(row, unit_idx)
                if unit in {"-", "--", "なし"}:
                    unit = ""
                note = cell(row, note_idx)
                nn = normalize(note)
                shared = any(m and m in nn for m in shared_markers)
                d.add(key, cell(row, name_idx), unit, note, cell(row, kind_idx), shared)
                count += 1
            total += count
            title = ""
            for i in range(key_idx, min(end, len(titles))):
                if titles[i]:
                    title = titles[i]
                    break
            d.blocks.append({
                "title": title,
                "key_col": header[key_idx] if key_idx < len(header) else key_idx,
                "name_col": header[name_idx] if name_idx is not None
                and name_idx < len(header) else None,
                "unit_col": header[unit_idx] if unit_idx is not None
                and unit_idx < len(header) else None,
                "kind_col": header[kind_idx] if kind_idx is not None
                and kind_idx < len(header) else None,
                "note_col": header[note_idx] if note_idx is not None
                and note_idx < len(header) else None,
                "entries": count,
            })

        d.source = path
        d.detected = {"header_row": hdr_row + 1, "blocks": d.blocks, "entries": total}
        return d

    # ------------------------------------------------------------------ #
    def find(self, raw: Any) -> tuple[Optional[Entry], bool]:
        """Best :class:`Entry` for a raw header, and whether it was a real hit."""
        key = str(raw).strip()
        if key in self.entries:
            return self.entries[key], True

        n = normalize(key)
        if n in self._norm:
            return self._norm[n], True

        base, unit_from_hdr = split_unit(key)
        nb = normalize(base)
        if nb and nb in self._norm:
            e = self._norm[nb]
            if not e.unit and unit_from_hdr:
                e = Entry(e.name, unit_from_hdr, e.note, e.kind, e.shared)
            return e, True

        # substring both ways (SCADA files often prefix the unit number)
        if nb:
            for cand, rec in self._norm.items():
                if len(cand) >= 4 and (cand in nb or nb in cand):
                    return rec, True

        # fuzzy last resort
        best, best_ratio = None, 0.0
        for cand, rec in self._norm.items():
            if not cand:
                continue
            r = SequenceMatcher(None, nb or n, cand).ratio()
            if r > best_ratio:
                best, best_ratio = rec, r
        if best is not None and best_ratio >= self.fuzzy_cutoff:
            return best, True

        self._misses.append(key)
        return None, False

    def lookup(self, raw: Any) -> tuple[str, str, str, bool]:
        """Backwards-compatible ``(name, unit, note, matched)``."""
        entry, ok = self.find(raw)
        if entry is not None:
            return entry.name, entry.unit, entry.note, ok
        name, unit = split_unit(str(raw).strip())
        return name or str(raw), unit, "", False

    def unmatched(self) -> list[str]:
        seen, out = set(), []
        for k in self._misses:
            if k not in seen:
                seen.add(k)
                out.append(k)
        return out

    # ------------------------------------------------------------------ #
    def describe(self, raw_headers: Iterable[Any], numeric_flags: dict[str, bool] | None = None
                 ) -> list[ColumnSpec]:
        """Build :class:`ColumnSpec` objects for a list of raw headers."""
        specs: list[ColumnSpec] = []
        numeric_flags = numeric_flags or {}
        for h in raw_headers:
            key = str(h)
            entry, matched = self.find(key)
            if entry is None:
                name, unit = split_unit(key)
                entry = Entry(name=name or key, unit=unit)
            specs.append(
                ColumnSpec(
                    raw=key,
                    name=entry.name,
                    unit=entry.unit,
                    note=entry.note,
                    kind=entry.kind,
                    shared=entry.shared,
                    is_numeric=bool(numeric_flags.get(key, True)),
                    matched=matched,
                )
            )
        return specs
