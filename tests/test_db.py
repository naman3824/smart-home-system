"""
Tests for db.py — the persistence layer. Focused on the pieces that have
actually caused bugs before (the IST timezone helper) or that other
features depend on being correct (energy_daily, automation rule CRUD).
"""
import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db


# ── Timezone helper — the UTC-vs-IST bug found earlier in this project ─────

def test_now_ist_is_ahead_of_utc_by_five_thirty():
    ist = db.now_ist()
    utc = datetime.utcnow()
    diff = (ist - utc).total_seconds()
    # Allow a couple seconds of slack for the two calls not being instantaneous
    assert 5 * 3600 + 29 * 60 < diff < 5 * 3600 + 31 * 60

def test_now_ist_str_matches_now_ist():
    assert db.now_ist_str() == db.now_ist().strftime("%Y-%m-%d %H:%M:%S")


# ── kv_store ─────────────────────────────────────────────────────────────

def test_kv_set_get_round_trip(test_db):
    test_db.kv_set("test_key", "test_value")
    assert test_db.kv_get("test_key") == "test_value"

def test_kv_get_missing_key_returns_default(test_db):
    assert test_db.kv_get("nonexistent_key", "fallback") == "fallback"
    assert test_db.kv_get("nonexistent_key") is None

def test_kv_set_overwrites_existing_value(test_db):
    test_db.kv_set("counter", "1")
    test_db.kv_set("counter", "2")
    assert test_db.kv_get("counter") == "2"


# ── energy_daily ─────────────────────────────────────────────────────────

def test_upsert_energy_daily_creates_row(test_db):
    test_db.upsert_energy_daily("2026-07-01", 12.5, 45.0)
    rows = test_db.get_energy_daily_range(days=5)
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-07-01"
    assert rows[0]["kwh"] == 12.5

def test_upsert_energy_daily_overwrites_same_date(test_db):
    # This is the live-tick-loop behavior: today's row gets rewritten many
    # times as the day's total accrues, not appended as new rows.
    test_db.upsert_energy_daily("2026-07-01", 5.0, 15.0)
    test_db.upsert_energy_daily("2026-07-01", 8.0, 24.0)
    rows = test_db.get_energy_daily_range(days=5)
    assert len(rows) == 1
    assert rows[0]["kwh"] == 8.0

def test_energy_daily_range_oldest_first(test_db):
    test_db.upsert_energy_daily("2026-07-03", 3, 9)
    test_db.upsert_energy_daily("2026-07-01", 1, 3)
    test_db.upsert_energy_daily("2026-07-02", 2, 6)
    rows = test_db.get_energy_daily_range(days=10)
    assert [r["date"] for r in rows] == ["2026-07-01", "2026-07-02", "2026-07-03"]

def test_energy_daily_range_respects_limit(test_db):
    for i in range(1, 11):
        test_db.upsert_energy_daily(f"2026-07-{i:02d}", i, i * 3)
    rows = test_db.get_energy_daily_range(days=3)
    assert len(rows) == 3
    # the 3 most recent dates, still returned oldest-first
    assert [r["date"] for r in rows] == ["2026-07-08", "2026-07-09", "2026-07-10"]


# ── Automation rules CRUD ────────────────────────────────────────────────

def test_create_and_get_automation_rule(test_db):
    rule = test_db.create_automation_rule(
        name="Test rule", description="desc",
        condition={"type": "sensor_above", "key": "aqi", "threshold": 200},
        action={"room": "living_room", "device": "air_purifier", "set": {"on": True}},
        enabled=True, cooldown_seconds=300,
    )
    fetched = test_db.get_automation_rule(rule["id"])
    assert fetched["name"] == "Test rule"
    assert fetched["condition"]["threshold"] == 200
    assert fetched["enabled"] is True

def test_update_automation_rule_changes_fields(test_db):
    rule = test_db.create_automation_rule(
        name="Original", description="", condition={"type": "sensor_above", "key": "aqi", "threshold": 200},
        action={"room": "living_room", "device": "ac", "set": {"on": True}}, enabled=True, cooldown_seconds=300,
    )
    updated = test_db.update_automation_rule(
        rule["id"], name="Renamed", description="new desc",
        condition={"type": "sensor_above", "key": "aqi", "threshold": 150},
        action={"room": "living_room", "device": "ac", "set": {"on": True, "temp": 20}},
        cooldown_seconds=600,
    )
    assert updated["name"] == "Renamed"
    assert updated["condition"]["threshold"] == 150
    assert updated["cooldown_seconds"] == 600

def test_update_automation_rule_enabled_toggle(test_db):
    rule = test_db.create_automation_rule(
        name="Toggle me", description="", condition={"type": "sensor_above", "key": "aqi", "threshold": 200},
        action={"room": "living_room", "device": "ac", "set": {"on": True}}, enabled=True, cooldown_seconds=300,
    )
    test_db.update_automation_rule_enabled(rule["id"], False)
    assert test_db.get_automation_rule(rule["id"])["enabled"] is False

def test_delete_automation_rule(test_db):
    rule = test_db.create_automation_rule(
        name="Delete me", description="", condition={"type": "sensor_above", "key": "aqi", "threshold": 200},
        action={"room": "living_room", "device": "ac", "set": {"on": True}}, enabled=True, cooldown_seconds=300,
    )
    test_db.delete_automation_rule(rule["id"])
    assert test_db.get_automation_rule(rule["id"]) is None

def test_get_automation_rules_enabled_only_filter(test_db):
    test_db.create_automation_rule(name="On", description="", condition={"type": "sensor_above", "key": "aqi", "threshold": 200},
        action={"room": "living_room", "device": "ac", "set": {"on": True}}, enabled=True, cooldown_seconds=300)
    test_db.create_automation_rule(name="Off", description="", condition={"type": "sensor_above", "key": "aqi", "threshold": 200},
        action={"room": "living_room", "device": "ac", "set": {"on": True}}, enabled=False, cooldown_seconds=300)
    assert len(test_db.get_automation_rules(enabled_only=True)) == 1
    assert len(test_db.get_automation_rules(enabled_only=False)) == 2


# ── Audit log ────────────────────────────────────────────────────────────

def test_add_and_get_audit_entry(test_db):
    test_db.add_audit_entry("aditya", "device_toggle", detail="living_room.ac -> {'on': True}")
    entries = test_db.get_audit_log(limit=10)
    assert len(entries) == 1
    assert entries[0]["username"] == "aditya"
    assert entries[0]["action"] == "device_toggle"

def test_audit_log_returns_newest_first(test_db):
    test_db.add_audit_entry("aditya", "login")
    test_db.add_audit_entry("naman", "login")
    entries = test_db.get_audit_log(limit=10)
    assert entries[0]["username"] == "naman"
    assert entries[1]["username"] == "aditya"


# ── Automation run stats (fire-count tracking) ──────────────────────────

def test_automation_run_stats_counts_today(test_db):
    rule = test_db.create_automation_rule(name="R", description="", condition={"type": "sensor_above", "key": "aqi", "threshold": 200},
        action={"room": "living_room", "device": "ac", "set": {"on": True}}, enabled=True, cooldown_seconds=300)
    test_db.add_automation_run(rule["id"], rule["name"], "fired")
    test_db.add_automation_run(rule["id"], rule["name"], "fired again")
    stats = test_db.get_automation_run_stats()
    assert stats["per_rule"][rule["id"]]["today"] == 2
    assert stats["total_today"] == 2
