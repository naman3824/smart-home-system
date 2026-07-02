"""
Automation rules engine.

Rules are stored in SQLite (db.py) and evaluated once per sensor tick (every
3 seconds, from inside server.py's simulate_sensors loop — no separate
thread needed). Each rule is a simple condition -> action pair:

  condition: {"type": "sensor_above" | "sensor_below" | "nobody_home_minutes"
                       | "time_of_day",
              ...type-specific fields...}
  action:    {"room": "...", "device": "...", "set": {...device fields...}}

This intentionally stays simple (4 condition types, single-device actions)
rather than building a general rule DSL — it covers every example asked for
(AQI -> air purifier, smoke -> alert, nobody home -> lights off, time-based
routines) without becoming its own programming language to maintain.

Each rule has a cooldown so it doesn't refire every 3 seconds once its
condition becomes true — it fires once, then waits cooldown_seconds before
it's eligible to fire again (even if the condition stays true the whole
time), so "AQI > 200 -> turn on purifier" doesn't spam toggle the purifier
on every tick while AQI hovers above 200.
"""

import time
from datetime import datetime, timedelta

import db
from applog import logger


# In-memory cooldown tracker: {rule_id: last_fired_timestamp}
# Lives only in memory (not persisted) — on a redeploy, rules are simply
# eligible to fire again immediately, which is harmless (worst case: a
# rule fires once extra right after a restart).
_last_fired = {}

# Tracks how long all members have been continuously "away", for the
# nobody_home_minutes condition type. Reset to None the moment anyone
# is home; set to the current time the moment everyone becomes away.
_all_away_since = None


def _condition_met(condition: dict, sensors: dict, family_members: list) -> bool:
    ctype = condition.get("type")

    if ctype == "sensor_above":
        key = condition.get("key")
        threshold = condition.get("threshold")
        if key is None or threshold is None:
            return False
        return sensors.get(key, float("-inf")) > threshold

    if ctype == "sensor_below":
        key = condition.get("key")
        threshold = condition.get("threshold")
        if key is None or threshold is None:
            return False
        return sensors.get(key, float("inf")) < threshold

    if ctype == "nobody_home_minutes":
        global _all_away_since
        minutes = condition.get("minutes", 30)
        anyone_home = any(m.get("status") == "home" for m in family_members)
        now = time.time()
        if anyone_home:
            _all_away_since = None
            return False
        if _all_away_since is None:
            _all_away_since = now
            return False
        return (now - _all_away_since) >= (minutes * 60)

    if ctype == "time_of_day":
        # condition: {"type": "time_of_day", "hour": 8, "minute": 0, "window_minutes": 2}
        # Fires once per day in the window [hour:minute, hour:minute + window_minutes)
        # so a 3-second tick loop reliably catches it without firing all day.
        hour = condition.get("hour", 0)
        minute = condition.get("minute", 0)
        window = condition.get("window_minutes", 2)
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return target <= now < target + timedelta(minutes=window)

    return False


def _apply_action(action: dict, devices: dict, state_lock) -> str:
    """Applies the action to the in-memory devices dict (caller already holds
    state_lock). Returns a human-readable description for logging."""
    room = action.get("room")
    device = action.get("device")
    set_fields = action.get("set", {})

    if room not in devices or device not in devices[room]:
        return f"action target {room}.{device} does not exist — skipped"

    devices[room][device].update(set_fields)
    db.save_device_state(room, device, devices[room][device])
    return f"{room}.{device} -> {set_fields}"


def evaluate_rules(devices: dict, sensors: dict, family_members: list, state_lock):
    """
    Called once per sensor tick from server.py. Checks every enabled rule;
    for any whose condition is met and isn't on cooldown, applies the
    action and logs it. Safe to call even with zero rules defined.
    """
    rules = db.get_automation_rules(enabled_only=True)
    now = time.time()

    for rule in rules:
        rule_id = rule["id"]
        cooldown = rule.get("cooldown_seconds", 300)

        last_fired = _last_fired.get(rule_id, 0)
        if now - last_fired < cooldown:
            continue

        try:
            condition = rule["condition"]
            met = _condition_met(condition, sensors, family_members)
        except Exception as e:
            logger.error("Automation rule %s (%s) condition check failed: %s", rule_id, rule.get("name"), e, exc_info=True)
            continue

        if not met:
            continue

        try:
            description = _apply_action(rule["action"], devices, state_lock)
        except Exception as e:
            logger.error("Automation rule %s (%s) action failed: %s", rule_id, rule.get("name"), e, exc_info=True)
            continue

        _last_fired[rule_id] = now
        db.add_automation_run(rule_id, rule.get("name", "Unnamed rule"), description)
        db.add_audit_entry("automation", "rule_fired", detail=f"{rule.get('name')}: {description}")
        logger.info("Automation rule fired: %s -> %s", rule.get("name"), description)


# ── Per-member routines ────────────────────────────────────────────────────
# Routines are scoped to individual members and stored separately from
# general automation rules, but evaluated in the same sensor tick loop.
# cooldown per routine: once fired, won't fire again for 23 hours so it
# runs once per day even if the tick loop catches the window multiple times.

_routine_last_fired = {}  # {routine_id: last_fired_timestamp}
_ROUTINE_COOLDOWN_SECONDS = 23 * 3600  # 23 hours

_DAY_NAMES = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]


def _routine_should_run_today(days: str) -> bool:
    if days == "everyday":
        return True
    today = _DAY_NAMES[datetime.now().weekday()]
    allowed = [d.strip().lower() for d in days.split(",")]
    return today in allowed


def evaluate_routines(devices: dict, state_lock):
    """
    Called every sensor tick alongside evaluate_rules(). For each enabled
    routine whose time window is now and whose day matches, apply the action.
    """
    routines = db.get_enabled_routines_for_tick()
    now_ts = time.time()
    now = datetime.now()

    for routine in routines:
        rid = routine["id"]
        last_fired = _routine_last_fired.get(rid, 0)
        if now_ts - last_fired < _ROUTINE_COOLDOWN_SECONDS:
            continue

        if not _routine_should_run_today(routine.get("days", "everyday")):
            continue

        # Fire within a 2-minute window of the scheduled time
        target = now.replace(
            hour=routine["hour"], minute=routine["minute"],
            second=0, microsecond=0
        )
        if not (target <= now < target + timedelta(minutes=2)):
            continue

        room = routine["room"]
        device = routine["device"]
        action = routine["action"]

        try:
            with state_lock:
                if room in devices and device in devices[room]:
                    devices[room][device].update(action)
                    db.save_device_state(room, device, devices[room][device])
                else:
                    logger.warning(
                        "Routine %s: target %s.%s does not exist — skipped",
                        routine["name"], room, device
                    )
                    continue
        except Exception as e:
            logger.error("Routine %s action failed: %s", routine["name"], e, exc_info=True)
            continue

        _routine_last_fired[rid] = now_ts
        detail = f"{room}.{device} → {action} at {routine['hour']:02d}:{routine['minute']:02d}"
        db.add_automation_run(rid, f"Routine: {routine['name']}", detail)
        db.add_audit_entry(
            routine["member_name"].lower(),
            "routine_fired",
            detail=f"{routine['name']}: {detail}"
        )
        logger.info(
            "Routine fired for %s: %s → %s",
            routine["member_name"], routine["name"], detail
        )
