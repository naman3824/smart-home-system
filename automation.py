"""
Automation rules engine.

Rules are stored in SQLite and evaluated every 3 seconds in the sensor tick
loop (plus once immediately after any manual device toggle). Each rule is a
condition -> action(s) pair:

  condition: see _condition_met() for the full list —
    sensor_above / sensor_below  threshold on any sensor key
    nobody_home_minutes          everyone away for N minutes
    someone_arrived_home         any member just came home
    time_of_day                  fires once per day in a time window
    device_state                 reacts to another device being on/off
    aqi_category                 reacts to "Good"/"Moderate"/"Poor" etc.
    and / or                     combines sub-conditions

  action: single dict or list of dicts (multi-device actions per rule)
    {"room": "...", "device": "...", "set": {...}}
"""

import time
from datetime import datetime, timedelta

import db
from applog import logger


_last_fired   = {}       # {rule_id: last_fired_unix_ts}
_all_away_since    = None
_anyone_home_last  = False   # edge-detector for "someone just arrived"


# ── Condition evaluation ───────────────────────────────────────────────────

def _condition_met(condition: dict, sensors: dict, family_members: list, devices: dict) -> bool:
    ctype = condition.get("type")

    # ── Sensor threshold ──────────────────────────────────────────────────
    if ctype == "sensor_above":
        key, threshold = condition.get("key"), condition.get("threshold")
        if key is None or threshold is None:
            return False
        value = sensors.get(key)
        if value is None:  # sensor offline (e.g. climate API down) — never fire
            return False
        return value > threshold

    if ctype == "sensor_below":
        key, threshold = condition.get("key"), condition.get("threshold")
        if key is None or threshold is None:
            return False
        value = sensors.get(key)
        if value is None:
            return False
        return value < threshold

    # ── AQI category ─────────────────────────────────────────────────────
    # condition: {"type":"aqi_category","category":"Poor"}
    # Matches the string category produced by the AQI calculation.
    if ctype == "aqi_category":
        target = condition.get("category", "")
        return sensors.get("aqi_category", "") == target

    # ── Nobody home ───────────────────────────────────────────────────────
    if ctype == "nobody_home_minutes":
        global _all_away_since
        minutes      = condition.get("minutes", 30)
        anyone_home  = any(m.get("status") == "home" for m in family_members)
        now          = time.time()
        if anyone_home:
            _all_away_since = None
            return False
        if _all_away_since is None:
            _all_away_since = now
            return False
        return (now - _all_away_since) >= (minutes * 60)

    # ── Someone just arrived home ─────────────────────────────────────────
    # Fires once on the rising edge (nobody home → someone home).
    if ctype == "someone_arrived_home":
        global _anyone_home_last
        anyone_home = any(m.get("status") == "home" for m in family_members)
        just_arrived = anyone_home and not _anyone_home_last
        _anyone_home_last = anyone_home
        return just_arrived

    # ── Time of day ───────────────────────────────────────────────────────
    if ctype == "time_of_day":
        hour    = condition.get("hour",   0)
        minute  = condition.get("minute", 0)
        window  = condition.get("window_minutes", 2)
        now     = datetime.now()
        target  = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return target <= now < target + timedelta(minutes=window)

    # ── Device state ──────────────────────────────────────────────────────
    # condition: {"type":"device_state","room":"living_room","device":"ac","on":true}
    if ctype == "device_state":
        room   = condition.get("room")
        device = condition.get("device")
        want   = condition.get("on", True)
        dev    = devices.get(room, {}).get(device)
        if dev is None:
            return False
        return bool(dev.get("on")) == bool(want)

    # ── Compound: AND ─────────────────────────────────────────────────────
    # condition: {"type":"and","conditions":[<cond1>,<cond2>,...]}
    if ctype == "and":
        return all(
            _condition_met(c, sensors, family_members, devices)
            for c in condition.get("conditions", [])
        )

    # ── Compound: OR ──────────────────────────────────────────────────────
    if ctype == "or":
        return any(
            _condition_met(c, sensors, family_members, devices)
            for c in condition.get("conditions", [])
        )

    return False


# ── Action application ─────────────────────────────────────────────────────

def _apply_action(action, devices: dict) -> str:
    """
    `action` may be a single dict or a list of dicts.
    Returns a human-readable description for logging.
    Caller already holds state_lock.
    """
    if isinstance(action, list):
        results = [_apply_single_action(a, devices) for a in action]
        return "; ".join(r for r in results if r)
    return _apply_single_action(action, devices)


def _apply_single_action(action: dict, devices: dict) -> str:
    room       = action.get("room")
    device     = action.get("device")
    set_fields = action.get("set", {})

    if room not in devices or device not in devices[room]:
        return f"target {room}.{device} not found — skipped"

    devices[room][device].update(set_fields)
    db.save_device_state(room, device, devices[room][device])
    return f"{room}.{device}→{set_fields}"


# ── Main evaluation loop ───────────────────────────────────────────────────

def evaluate_rules(devices: dict, sensors: dict, family_members: list, state_lock):
    """
    Called once per sensor tick. Checks every enabled rule;
    for any whose condition is met and isn't on cooldown, applies
    the action(s) and logs everything.
    """
    rules = db.get_automation_rules(enabled_only=True)
    now   = time.time()

    for rule in rules:
        rule_id  = rule["id"]
        cooldown = rule.get("cooldown_seconds", 300)

        if now - _last_fired.get(rule_id, 0) < cooldown:
            continue

        try:
            met = _condition_met(rule["condition"], sensors, family_members, devices)
        except Exception as e:
            logger.error("Rule %s condition check failed: %s", rule_id, e, exc_info=True)
            continue

        if not met:
            continue

        try:
            with state_lock:
                description = _apply_action(rule["action"], devices)
        except Exception as e:
            logger.error("Rule %s action failed: %s", rule_id, e, exc_info=True)
            continue

        _last_fired[rule_id] = now
        db.add_automation_run(rule_id, rule.get("name", "Unnamed"), description)
        db.add_audit_entry("automation", "rule_fired",
                           detail=f"{rule.get('name')}: {description}")
        logger.info("Automation fired: %s → %s", rule.get("name"), description)


# ── Per-member routines ────────────────────────────────────────────────────

_routine_last_fired        = {}
_ROUTINE_COOLDOWN_SECONDS  = 23 * 3600
_DAY_NAMES = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]


def _routine_should_run_today(days: str) -> bool:
    if days == "everyday":
        return True
    today   = _DAY_NAMES[datetime.now().weekday()]
    allowed = [d.strip().lower() for d in days.split(",")]
    return today in allowed


def evaluate_routines(devices: dict, state_lock):
    routines = db.get_enabled_routines_for_tick()
    now_ts   = time.time()
    now      = datetime.now()

    for routine in routines:
        rid = routine["id"]
        if now_ts - _routine_last_fired.get(rid, 0) < _ROUTINE_COOLDOWN_SECONDS:
            continue
        if not _routine_should_run_today(routine.get("days", "everyday")):
            continue

        target = now.replace(
            hour=routine["hour"], minute=routine["minute"],
            second=0, microsecond=0
        )
        if not (target <= now < target + timedelta(minutes=2)):
            continue

        room   = routine["room"]
        device = routine["device"]
        action = routine["action"]

        try:
            with state_lock:
                if room in devices and device in devices[room]:
                    devices[room][device].update(action)
                    db.save_device_state(room, device, devices[room][device])
                else:
                    logger.warning("Routine %s: %s.%s not found", routine["name"], room, device)
                    continue
        except Exception as e:
            logger.error("Routine %s failed: %s", routine["name"], e, exc_info=True)
            continue

        _routine_last_fired[rid] = now_ts
        detail = f"{room}.{device}→{action} at {routine['hour']:02d}:{routine['minute']:02d}"
        db.add_automation_run(rid, f"Routine: {routine['name']}", detail)
        db.add_audit_entry(
            routine["member_name"].lower(), "routine_fired",
            detail=f"{routine['name']}: {detail}"
        )
        logger.info("Routine fired: %s → %s", routine["name"], detail)
