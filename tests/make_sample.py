"""Generate synthetic PCS monitoring files + a dictionary, for testing.

Mimics the awkward parts of real SCADA exports: cp932 encoding, junk lines
above the header, a unit row, Japanese headers, zeros, gaps, stuck sensors.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/sample_pcs")
OUT.mkdir(parents=True, exist_ok=True)

RAW_COLS = ["計測日時", "DC_V", "DC_A", "DC_KW", "AC_KW", "AC_KWH", "PF", "TEMP_INV",
            "IRR", "FREQ", "STATUS"]
DICT_ROWS = [
    ("DC_V", "直流電圧", "V", "PCS入力側"),
    ("DC_A", "直流電流", "A", ""),
    ("DC_KW", "直流電力", "kW", ""),
    ("AC_KW", "交流有効電力", "kW", "系統側"),
    ("AC_KWH", "交流積算電力量", "kWh", "積算値"),
    ("PF", "力率", "-", ""),
    ("TEMP_INV", "インバータ温度", "℃", ""),
    ("IRR", "日射強度", "W/m2", "参照日射計"),
    ("FREQ", "系統周波数", "Hz", ""),
    ("STATUS", "運転状態", "", "運転/停止/故障"),
    ("計測日時", "計測日時", "", "1分間隔"),
]


def make_day(day: str, unit: int, rng: np.random.Generator) -> pd.DataFrame:
    idx = pd.date_range(f"{day} 00:00", f"{day} 23:59", freq="1min")
    t = np.arange(len(idx))
    sun = np.clip(np.sin((t - 300) / 840 * np.pi), 0, None) ** 1.3
    irr = sun * (950 + rng.normal(0, 18, len(idx)))
    irr = np.clip(irr, 0, None)
    scale = {1: 1.0, 2: 0.985, 3: 0.72}.get(unit, 1.0)      # unit 3 underperforms
    dc_kw = irr / 1000.0 * 2200 * scale
    if unit == 3:                                            # a two-hour outage
        dc_kw[600:720] = 0.0
    ac_kw = dc_kw * 0.985
    df = pd.DataFrame({
        "計測日時": idx.strftime("%Y/%m/%d %H:%M"),
        "DC_V": np.where(dc_kw > 1, 780 + rng.normal(0, 4, len(idx)), 0).round(1),
        "DC_A": (dc_kw * 1000 / 780).round(1),
        "DC_KW": dc_kw.round(2),
        "AC_KW": ac_kw.round(2),
        "AC_KWH": (ac_kw.cumsum() / 60).round(2),
        "PF": np.where(ac_kw > 1, 0.99 + rng.normal(0, 0.003, len(idx)), 0).round(3),
        "TEMP_INV": (24 + sun * 26 + rng.normal(0, 0.6, len(idx))).round(1),
        "IRR": irr.round(1),
        "FREQ": (50 + rng.normal(0, 0.02, len(idx))).round(3),
        "STATUS": np.where(dc_kw > 1, "運転", "停止"),
    })
    if unit == 2:                                            # stuck temperature sensor
        df.loc[400:520, "TEMP_INV"] = 31.4
    if unit == 1:                                            # missing rows (gap)
        df = pd.concat([df.iloc[:800], df.iloc[860:]], ignore_index=True)
    df["PF"] = df["PF"].astype(object)
    df.loc[df.index[:3], "PF"] = "-"                         # non-numeric junk
    return df


def write_csv(df: pd.DataFrame, path: Path) -> None:
    units = {"計測日時": "", "DC_V": "V", "DC_A": "A", "DC_KW": "kW", "AC_KW": "kW",
             "AC_KWH": "kWh", "PF": "-", "TEMP_INV": "℃", "IRR": "W/m2",
             "FREQ": "Hz", "STATUS": ""}
    with path.open("w", encoding="cp932", newline="") as fh:
        fh.write("PCS監視データ出力\n")
        fh.write(f"出力日時,{df['計測日時'].iloc[0]}\n")
        fh.write("\n")
        fh.write(",".join(RAW_COLS) + "\n")
        fh.write(",".join(units[c] for c in RAW_COLS) + "\n")
        for _, row in df.iterrows():
            fh.write(",".join("" if pd.isna(v) else str(v) for v in row[RAW_COLS]) + "\n")


def main() -> None:
    rng = np.random.default_rng(7)
    for unit in (1, 2, 3):
        for day in ("2026-08-06", "2026-08-07"):
            df = make_day(day, unit, rng)
            write_csv(df, OUT / f"{unit:03d}_PCS監視_{day}.csv")
    dic = pd.DataFrame(DICT_ROWS, columns=["CSV項目名", "項目名称", "単位", "備考"])
    with pd.ExcelWriter(OUT / "CSV項目名称.xlsx") as xw:
        pd.DataFrame([["出力対象項目一覧"]]).to_excel(xw, index=False, header=False,
                                                     sheet_name="項目")
        dic.to_excel(xw, index=False, sheet_name="項目", startrow=2)
    print(f"wrote sample data to {OUT}")


if __name__ == "__main__":
    main()
