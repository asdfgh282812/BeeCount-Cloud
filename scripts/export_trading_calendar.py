"""匯出各交易所的休市日表給 App(定期定額遇休市日順延用)。

App 沒有 Python `holidays` 套件,內建一份靜態表(`lib/services/investment/
trading_calendar_data.dart`);判斷邏輯在 `trading_calendar.dart`,必須跟 Cloud
`src/services/securities/trading_calendar.py` 同一套。表只列「平日」的休市日
(週末由程式判斷),超出涵蓋年份的日期只剩週末判斷。

每年發版前跑一次(預設涵蓋 去年 ~ 今年+5):
    .venv/bin/python scripts/export_trading_calendar.py
    .venv/bin/python scripts/export_trading_calendar.py --first 2025 --last 2036 --out /path/to/trading_calendar_data.dart
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.securities import trading_calendar  # noqa: E402

DEFAULT_OUT = (
    ROOT.parent / "BeeCount-main" / "BeeCount-main" / "lib" / "services" / "investment" / "trading_calendar_data.dart"
)


def main() -> None:
    this_year = date.today().year
    parser = argparse.ArgumentParser()
    parser.add_argument("--first", type=int, default=this_year - 1)
    parser.add_argument("--last", type=int, default=this_year + 5)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    lines = [
        "// GENERATED FILE — 不要手改。由 BeeCount-Cloud `scripts/export_trading_calendar.py` 產生。",
        "// 各交易所「平日」的休市日(週末由 trading_calendar.dart 判斷);超出涵蓋年份只剩週末判斷。",
        "// ignore_for_file: lines_longer_than_80_chars",
        "library;",
        "",
        f"/// 涵蓋年份:{args.first} ~ {args.last}。",
        f"const int kTradingCalendarFirstYear = {args.first};",
        f"const int kTradingCalendarLastYear = {args.last};",
        "",
        "/// 交易所 → 休市日(`yyyy-MM-dd`)。",
        "const Map<String, Set<String>> kExchangeClosures = {",
    ]
    for exchange in trading_calendar.EXCHANGES:
        lines.append(f"  '{exchange}': {{")
        for year in range(args.first, args.last + 1):
            days = trading_calendar.exchange_closures(exchange, year)
            if days:
                lines.append("    " + " ".join(f"'{d.isoformat()}'," for d in days))
        lines.append("  },")
    lines.append("};")
    lines.append("")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {args.first}-{args.last} -> {args.out}")


if __name__ == "__main__":
    main()
