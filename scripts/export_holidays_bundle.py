"""匯出 App 內建的節日備援資料(docs/HOLIDAYS_SD.md §App 內建備援)。

App(`assets/holidays/holidays_bundle.json`)在沒有 BeeCount Cloud、或還沒跟
server 同步過時,用這份內建資料顯示節日。跟 server 用同一個產生器
(`services/holidays/generator.py`),格式跟 `GET /read/holidays` 回應相同,
`version` 固定 0(server 的資料集版本從 1 開始,App 拿到 server 資料後一律
覆蓋內建版本)。

發版前執行一次(預設:去年/今年/明年):
    .venv/bin/python scripts/export_holidays_bundle.py
    .venv/bin/python scripts/export_holidays_bundle.py --years 2026 2027 --out /path/to/holidays_bundle.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.holidays.catalog import SUPPORTED_COUNTRIES  # noqa: E402
from src.services.holidays.generator import generate_year  # noqa: E402

DEFAULT_OUT = ROOT.parent / "BeeCount-main" / "BeeCount-main" / "assets" / "holidays" / "holidays_bundle.json"


def main() -> None:
    this_year = date.today().year
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, nargs="+", default=[this_year - 1, this_year, this_year + 1])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    entries = []
    for year in sorted(set(args.years)):
        entries.extend(e.to_wire() for e in generate_year(year))
    payload = {
        "version": 0,
        "generated_at": date.today().isoformat(),
        "years": sorted(set(args.years)),
        "countries": list(SUPPORTED_COUNTRIES),
        "entries": entries,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {len(entries)} entries ({payload['years']}) -> {args.out}")


if __name__ == "__main__":
    main()
