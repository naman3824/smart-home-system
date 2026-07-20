"""
Tests for the automation rules engine (automation.py).

This is the highest-value thing to test in this codebase: it's the part
that acts on its own, unattended, and a silent bug here (like the
timezone/UTC bug and the daily-rule double-fire bug found earlier in this
project) doesn't throw an error — it just quietly does the wrong thing at
3am. These tests exist to catch that class of bug before it ships, not
just to pad a coverage number.
"""
import time
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import automation


# ── Fixtures reset automation.py's module-level state between tests ────────
# _all_away_since and _anyone_home_last are mutated by _condition_met() as
# real side effects (that's how "nobody home for N minutes" and "someone
# just arrived" detect edges) — without resetting them, test order would
# affect results, which is exactly the kind of flaky test that erodes trust
# in a suite instead of building it.
def setup_function():
    automation._all_away_since = None
    automation._anyone_home_last = False
    automation._last_fired = {}


# ── sensor_above / sensor_below / sensor_between ────────────────────────────

def test_sensor_above_true(sample_sensors):
    cond = {"type": "sensor_above", "key": "aqi", "threshold": 100}
    assert automation._condition_met(cond, sample_sensors, [], {}) is True

def test_sensor_above_false(sample_sensors):
    cond = {"type": "sensor_above", "key": "aqi", "threshold": 200}
    assert automation._condition_met(cond, sample_sensors, [], {}) is False

def test_sensor_above_missing_sensor_never_fires(sample_sensors):
    # A sensor that's offline (e.g. climate API down) should never
    # accidentally satisfy a threshold condition.
    cond = {"type": "sensor_above", "key": "wind_speed", "threshold": 10}
    assert automation._condition_met(cond, sample_sensors, [], {}) is False

def test_sensor_below(sample_sensors):
    cond = {"type": "sensor_below", "key": "temperature", "threshold": 30}
    assert automation._condition_met(cond, sample_sensors, [], {}) is True

def test_sensor_between_inside_range(sample_sensors):
    cond = {"type": "sensor_between", "key": "temperature", "min": 20, "max": 30}
    assert automation._condition_met(cond, sample_sensors, [], {}) is True

def test_sensor_between_outside_range(sample_sensors):
    cond = {"type": "sensor_between", "key": "temperature", "min": 27, "max": 30}
    assert automation._condition_met(cond, sample_sensors, [], {}) is False

def test_sensor_between_min_must_not_be_dropped():
    # Regression test: min/max were missing from the API request model at
    # one point, which would silently make every sensor_between rule act
    # like it had no lower bound. Confirms the engine itself uses both.
    cond = {"type": "sensor_between", "key": "temperature", "min": 0, "max": 10}
    assert automation._condition_met(cond, {"temperature": 15}, [], {}) is False


# ── negate ───────────────────────────────────────────────────────────────

def test_negate_inverts_result(sample_sensors):
    cond = {"type": "sensor_above", "key": "aqi", "threshold": 200, "negate": True}
    assert automation._condition_met(cond, sample_sensors, [], {}) is True  # aqi=142, NOT >200

def test_negate_on_true_condition_becomes_false(sample_sensors):
    cond = {"type": "sensor_above", "key": "aqi", "threshold": 100, "negate": True}
    assert automation._condition_met(cond, sample_sensors, [], {}) is False  # aqi=142 IS >100


# ── aqi_category / device_state ─────────────────────────────────────────────

def test_aqi_category_match(sample_sensors):
    cond = {"type": "aqi_category", "category": "Moderate"}
    assert automation._condition_met(cond, sample_sensors, [], {}) is True

def test_aqi_category_no_match(sample_sensors):
    cond = {"type": "aqi_category", "category": "Severe"}
    assert automation._condition_met(cond, sample_sensors, [], {}) is False

def test_device_state_on(sample_devices):
    cond = {"type": "device_state", "room": "living_room", "device": "light", "on": True}
    assert automation._condition_met(cond, {}, [], sample_devices) is True

def test_device_state_wrong_room_or_device_is_false(sample_devices):
    cond = {"type": "device_state", "room": "bathroom", "device": "light", "on": True}
    assert automation._condition_met(cond, {}, [], sample_devices) is False


# ── nobody_home_minutes (stateful — depends on real elapsed time) ──────────

def test_nobody_home_returns_false_immediately_when_everyone_just_left():
    # First tick after everyone leaves: the clock starts, but 0 minutes
    # have passed, so it must not fire yet.
    cond = {"type": "nobody_home_minutes", "minutes": 30}
    away_family = [{"status": "away"}]
    assert automation._condition_met(cond, {}, away_family, {}) is False
    assert automation._all_away_since is not None  # but it started tracking

def test_nobody_home_fires_after_enough_time_elapsed():
    cond = {"type": "nobody_home_minutes", "minutes": 30}
    away_family = [{"status": "away"}]
    automation._all_away_since = time.time() - (31 * 60)  # simulate 31 min ago
    assert automation._condition_met(cond, {}, away_family, {}) is True

def test_nobody_home_resets_when_someone_is_home():
    cond = {"type": "nobody_home_minutes", "minutes": 30}
    automation._all_away_since = time.time() - (60 * 60)  # was away an hour
    home_family = [{"status": "home"}]
    assert automation._condition_met(cond, {}, home_family, {}) is False
    assert automation._all_away_since is None  # and the clock resets


# ── someone_arrived_home (edge-triggered) ───────────────────────────────────

def test_someone_arrived_fires_only_on_rising_edge():
    cond = {"type": "someone_arrived_home"}
    away = [{"status": "away"}]
    home = [{"status": "home"}]
    assert automation._condition_met(cond, {}, away, {}) is False   # still away
    assert automation._condition_met(cond, {}, home, {}) is True    # just arrived
    assert automation._condition_met(cond, {}, home, {}) is False   # still home, not a new arrival


# ── Compound and/or ──────────────────────────────────────────────────────

def test_and_requires_all_true(sample_sensors):
    cond = {"type": "and", "conditions": [
        {"type": "sensor_above", "key": "aqi", "threshold": 100},
        {"type": "sensor_below", "key": "temperature", "threshold": 30},
    ]}
    assert automation._condition_met(cond, sample_sensors, [], {}) is True

def test_and_fails_if_one_condition_false(sample_sensors):
    cond = {"type": "and", "conditions": [
        {"type": "sensor_above", "key": "aqi", "threshold": 100},
        {"type": "sensor_below", "key": "temperature", "threshold": 10},  # false: temp=26
    ]}
    assert automation._condition_met(cond, sample_sensors, [], {}) is False

def test_or_needs_only_one_true(sample_sensors):
    cond = {"type": "or", "conditions": [
        {"type": "sensor_above", "key": "aqi", "threshold": 9999},  # false
        {"type": "sensor_below", "key": "temperature", "threshold": 30},  # true
    ]}
    assert automation._condition_met(cond, sample_sensors, [], {}) is True


# ── _contains_time_of_day (recursion into and/or) ───────────────────────────

def test_contains_time_of_day_direct():
    assert automation._contains_time_of_day({"type": "time_of_day", "hour": 8}) is True

def test_contains_time_of_day_false_for_unrelated_condition():
    assert automation._contains_time_of_day({"type": "sensor_above", "key": "aqi"}) is False

def test_contains_time_of_day_nested_in_and():
    cond = {"type": "and", "conditions": [
        {"type": "sensor_above", "key": "aqi", "threshold": 100},
        {"type": "time_of_day", "hour": 20, "minute": 0},
    ]}
    assert automation._contains_time_of_day(cond) is True


# ── _effective_cooldown — the daily double-fire safety net ─────────────────

def test_effective_cooldown_clamps_short_cooldown_on_time_of_day_rule():
    rule = {"condition": {"type": "time_of_day", "hour": 19, "minute": 0}, "cooldown_seconds": 60}
    assert automation._effective_cooldown(rule) == automation._MIN_TIME_OF_DAY_COOLDOWN_SECONDS

def test_effective_cooldown_leaves_long_cooldown_alone():
    rule = {"condition": {"type": "time_of_day", "hour": 19, "minute": 0}, "cooldown_seconds": 90000}
    assert automation._effective_cooldown(rule) == 90000

def test_effective_cooldown_unaffected_for_non_daily_rules():
    rule = {"condition": {"type": "sensor_above", "key": "aqi", "threshold": 200}, "cooldown_seconds": 60}
    assert automation._effective_cooldown(rule) == 60


# ── Conflict detection ───────────────────────────────────────────────────

def test_find_rule_conflicts_detects_shared_target():
    rules = [
        {"id": 1, "name": "Rule A", "enabled": True, "action": {"room": "living_room", "device": "ac", "set": {"on": True}}},
        {"id": 2, "name": "Rule B", "enabled": True, "action": {"room": "living_room", "device": "ac", "set": {"on": False}}},
    ]
    conflicts = automation.find_rule_conflicts(rules)
    assert conflicts[1] == ["Rule B"]
    assert conflicts[2] == ["Rule A"]

def test_find_rule_conflicts_ignores_disabled_rules():
    rules = [
        {"id": 1, "name": "Rule A", "enabled": True, "action": {"room": "living_room", "device": "ac", "set": {"on": True}}},
        {"id": 2, "name": "Rule B", "enabled": False, "action": {"room": "living_room", "device": "ac", "set": {"on": False}}},
    ]
    assert automation.find_rule_conflicts(rules) == {}

def test_find_rule_conflicts_none_for_different_targets():
    rules = [
        {"id": 1, "name": "Rule A", "enabled": True, "action": {"room": "living_room", "device": "ac", "set": {"on": True}}},
        {"id": 2, "name": "Rule B", "enabled": True, "action": {"room": "kitchen", "device": "light", "set": {"on": False}}},
    ]
    assert automation.find_rule_conflicts(rules) == {}

def test_action_targets_handles_multi_device_list():
    action = [{"room": "living_room", "device": "ac"}, {"room": "kitchen", "device": "light"}]
    assert automation.action_targets(action) == {("living_room", "ac"), ("kitchen", "light")}


# ── Global and per-room pause ────────────────────────────────────────────

def test_apply_action_blocked_when_room_paused(sample_devices, test_db):
    automation.set_room_paused("kitchen", True)
    sample_devices["kitchen"]["light"]["on"] = False
    result = automation._apply_single_action({"room": "kitchen", "device": "light", "set": {"on": True}}, sample_devices)
    assert "skipped" in result
    assert sample_devices["kitchen"]["light"]["on"] is False  # actually blocked, not just logged

def test_apply_action_works_normally_when_room_not_paused(sample_devices, test_db):
    result = automation._apply_single_action({"room": "living_room", "device": "ac", "set": {"on": True}}, sample_devices)
    assert "skipped" not in result
    assert sample_devices["living_room"]["ac"]["on"] is True

def test_room_pause_resume_round_trip(test_db):
    automation.set_room_paused("bathroom", True)
    assert "bathroom" in automation.get_paused_rooms()
    automation.set_room_paused("bathroom", False)
    assert "bathroom" not in automation.get_paused_rooms()

def test_global_pause_round_trip(test_db):
    automation.set_paused(True)
    assert automation.is_paused() is True
    automation.set_paused(False)
    assert automation.is_paused() is False
