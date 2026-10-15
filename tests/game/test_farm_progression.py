import pytest

from src.services.game import rules


def test_increasing_integer_costs_and_total_xp_boundaries():
    config = rules.load_config()
    thresholds = [0, 50, 125, 237, 405, 657, 1035, 1602, 2452, 3727]
    costs = [50, 75, 112, 168, 252, 378, 567, 850, 1275, 1912]
    for level, (threshold, cost) in enumerate(zip(thresholds, costs, strict=True), start=1):
        assert rules.xp_for_level(level, config) == threshold
        assert rules.level(threshold, config) == level
        assert rules.xp_progress(threshold, config) == (level, 0, cost)
        if level > 1:
            assert rules.level(threshold - 1, config) == level - 1
    assert rules.xp_progress(240, config) == (4, 3, 168)
    assert rules.xp_progress(-10, config) == (1, 0, 50)


def test_old_configs_keep_fixed_level_costs():
    config = {"progression": {"xpPerLevel": 100}}
    assert rules.xp_for_level(5, config) == 400
    assert rules.xp_progress(435, config) == (5, 35, 100)
    config = {"progression": {"xpPerLevel": 50, "xpGrowthPercent": 50.0}}
    assert rules.xp_for_level(4, config) == 237
    assert rules.xp_progress(135, config) == (3, 10, 112)


def test_debug_level_threshold_fits_json_safe_integer():
    config = rules.load_config()
    max_level = min(99, rules.level(rules.MAX_EXACT_XP, config))
    xp = rules.xp_for_level(max_level, config)
    assert xp <= rules.MAX_EXACT_XP
    assert rules.level(xp, config) == max_level
    with pytest.raises(ValueError):
        rules.xp_for_level(99, config)
