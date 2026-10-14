"""M3 只固定資料契約；M4 的 Python 規則須再用 rules_v1 跑輸入→輸出驗證。"""

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "tests/game/golden"
MANIFEST = json.loads((GOLDEN / "contract_sha256.json").read_text())


@pytest.mark.parametrize("name,expected", MANIFEST.items())
def test_farm_contract_hash(name: str, expected: str) -> None:
    path = ROOT / "src/services/game" / name if name.startswith("balance_") else GOLDEN / name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


def test_initial_farm_balance_contract() -> None:
    config = json.loads((ROOT / "src/services/game/balance_v1.json").read_text())
    assert config["version"] == 1
    assert len(config["crops"]) == 19
    assert len({crop["id"] for crop in config["crops"]}) == 19
    assert config["progression"]["cloudMigrationCoinCap"] == 5000
    assert config["progression"]["initialPlots"] == 6
    assert config["progression"]["maximumPlots"] == 24
    assert config["scoring"]["catchUpWeeks"] == 4
    assert sum(config["scoring"]["weights"].values()) == 100
    assert set(config["turnips"]["patterns"]) == {
        "decreasing", "fluctuating", "small_spike", "large_spike"
    }
    assert set(json.loads((GOLDEN / "rules_v1.json").read_text())) == {
        "version", "random", "time", "growth", "market", "scores", "turnips"
    }


def test_each_season_has_five_starter_crops_and_level_unlocks() -> None:
    config = json.loads((ROOT / "src/services/game/balance_v1.json").read_text())
    for season in ("spring", "summer", "autumn", "winter"):
        stock = [crop for crop in config["crops"] if season in crop["seasons"]]
        assert len([crop for crop in stock if crop["minLevel"] == 1]) >= 5
        assert any(crop["minLevel"] > 1 for crop in stock)
