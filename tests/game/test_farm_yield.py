"""Weather-weighted 1..5 harvests, with shared independently calculated vectors."""
import json
from datetime import timedelta
from pathlib import Path

import pytest

from src.services.game import growth, rules, time

AT = time.utc('2026-10-13T02:00:00Z')
HOUR_US = 3_600_000_000


@pytest.fixture
def config():
    return rules.load_config()


def planted(config, crop='radish'):
    return rules.plant(rules.empty_plot(0, AT), crop, AT, 'yield-test', config, 99)


def advance(p, config, weather=None, minutes=60):
    return rules.advance(p, AT + timedelta(minutes=minutes), [dict(at=time.iso(AT), **(weather or {}))], config)


def test_exact_weights_and_shared_seed_vectors(config):
    fixture = json.loads(Path(__file__).with_name('golden').joinpath('yield_v1.json').read_text())
    p = planted(config)
    p.update(growth=1, yieldGrowingMicros=100)
    seen = set()
    for name, adverse, weights in [
        ('normal', 0, [5, 40, 30, 20, 5]),
        ('mixed', 50, [17.5, 32.5, 25, 17.5, 7.5]),
        ('adverse', 100, [30, 25, 20, 15, 10]),
    ]:
        p['yieldAdverseMicros'] = adverse
        assert growth.harvest_weights(p, config) == weights
        for row in fixture['vectors']:
            p['playerId'] = row['player']
            result = rules.harvest(p, AT, config)['quantity']
            assert result == row['expected'][name]
            assert rules.harvest(p, AT, config)['quantity'] == result
            seen.add(result)
    assert seen == {1, 2, 3, 4, 5}
    assert all(row['minYield'] == 1 and row['maxYield'] == 5 for row in config['crops'])


@pytest.mark.parametrize('weather', [{}, {'precipitation': 1}, {'cloud': 100}, {'isDay': False}])
def test_neutral_weather(config, weather):
    assert advance(planted(config), config, weather)['yieldAdverseMicros'] == 0
    assert rules.advance(planted(config), AT + timedelta(hours=1), [], config)['yieldAdverseMicros'] == 0


@pytest.mark.parametrize('weather,protection', [
    ({'gust': 70}, 'wind_net'),
    ({'precipitation': 20}, 'sun_doll'),
    ({'temperature': 5}, 'greenhouse'),
    ({'temperature': 40, 'cloud': 0}, 'sprinkler'),
    ({'temperature': 40, 'cloud': 0}, 'rain_jar'),
])
def test_only_matching_protection_works(config, weather, protection):
    p = planted(config)
    assert advance(p, config, weather)['yieldAdverseMicros'] == HOUR_US
    protected = rules.use_item(p, protection, AT, config)
    assert advance(protected, config, weather)['yieldAdverseMicros'] == 0
    greenhouse = rules.use_item(p, 'greenhouse', AT, config)
    assert advance(greenhouse, config, weather)['yieldAdverseMicros'] == 0
    fertilizer = rules.use_item(p, 'fertilizer', AT, config)
    assert advance(fertilizer, config, weather)['yieldAdverseMicros'] == HOUR_US


def test_wrong_protection_pests_and_winter_tolerance(config):
    p = rules.use_item(planted(config), 'wind_net', AT, config)
    assert advance(p, config, {'temperature': 5})['yieldAdverseMicros'] == HOUR_US
    p = planted(config)
    p['pests'] = True
    assert advance(p, config)['yieldAdverseMicros'] == HOUR_US
    assert advance(rules.care(p, 'removePests', config), config)['yieldAdverseMicros'] == 0
    winter = time.utc('2026-12-13T02:00:00Z')
    p = rules.plant(rules.empty_plot(0, winter), 'strawberry', winter, 'p', config, 99)
    result = rules.advance(p, winter + timedelta(hours=1), [dict(at=time.iso(winter), temperature=5)], config)
    assert result['yieldAdverseMicros'] == 0


def test_item_expiry_chunking_and_roundtrip(config):
    p = planted(config)
    p['effects'] = [dict(id='wind_net', expiresAt=time.iso(AT + timedelta(minutes=30)), seasonUsed=False)]
    whole = advance(p, config, {'gust': 70})
    first = advance(p, config, {'gust': 70}, minutes=17)
    chunks = advance(json.loads(json.dumps(first)), config, {'gust': 70})
    assert whole['yieldGrowingMicros'] == chunks['yieldGrowingMicros'] == HOUR_US
    assert whole['yieldAdverseMicros'] == chunks['yieldAdverseMicros'] == HOUR_US // 2


def test_maturity_freezes_yield_and_next_harvest_resets(config):
    p = planted(config)
    p['growth'] = .99
    mature = advance(p, config)
    assert 1 <= mature['readyYield'] <= 5
    assert mature['yieldGrowingMicros'] < HOUR_US
    p = dict(mature, pests=True)
    later = rules.advance(p, AT + timedelta(hours=2), [dict(at=time.iso(AT+timedelta(hours=1)), gust=70)], config)
    assert later['yieldGrowingMicros'] == mature['yieldGrowingMicros']
    assert later['yieldAdverseMicros'] == mature['yieldAdverseMicros']
    assert rules.harvest(later, later['lastAdvancedAt'], config)['quantity'] == mature['readyYield']
    winter = time.utc('2026-12-13T02:00:00Z')
    multi = rules.plant(rules.empty_plot(0, winter), 'strawberry', winter, 'p', config, 99)
    multi.update(growth=1, readyYield=5, yieldGrowingMicros=100, yieldAdverseMicros=100)
    result = rules.harvest(multi, winter, config)
    assert result['quantity'] == 5
    assert result['plot']['harvests'] == 1
    assert all(result['plot'][key] == 0 for key in ['yieldGrowingMicros', 'yieldAdverseMicros', 'readyYield'])


def test_old_save_can_advance_and_harvest(config):
    p = planted(config)
    for key in ['yieldGrowingMicros', 'yieldAdverseMicros', 'readyYield']:
        p.pop(key)
    p['growth'] = 1
    result = advance(p, config, {'gust': 70})
    assert 1 <= result['readyYield'] <= 5
    assert result['yieldGrowingMicros'] == result['yieldAdverseMicros'] == 0
