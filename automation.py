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
import json
from datetime import datetime, timedelta

import db
from applog import logger


_last_fired   = {}       # {rule_id: last_fired_unix_ts}
_all_away_since    = None
_anyone_home_last  = False   # edge-detector for "someone just arrived"


# ── Condition evaluation ───────────────────────────────────────────────────

def _condition_met(condition: dict, sensors: dict, family_members: list, devices: dict) -> bool:
    ctype = condition.get("type")

    # ── Negate wrapper ────────────────────────────────────────────────────
    # Any condition can carry "negate": true to fire on the opposite result
    # (e.g. "AQI NOT above 200" instead of writing a mirror-image condition
    # type for every existing one). Checked first so it applies uniformly.
    if condition.get("negate"):
        # Evaluate the same condition dict minus the negate flag, then invert.
        inner = {k: v for k, v in condition.items() if k != "negate"}
        return not _condition_met(inner, sensors, family_members, devices)

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

    # condition: {"type":"sensor_between","key":"temperature","min":20,"max":26}
    # Useful for "keep it in a comfortable range" style rules without two
    # separate above/below rules fighting each other.
    if ctype == "sensor_between":
        key = condition.get("key")
        lo, hi = condition.get("min"), condition.get("max")
        if key is None or lo is None or hi is None:
            return False
        value = sensors.get(key)
        if value is None:
            return False
        return lo <= value <= hi

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
        now     = db.now_ist()   # rules are set by a person thinking in local (IST) time
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

_AUTOMATION_PAUSED_KV_KEY = "automation_paused"
_PAUSED_ROOMS_KV_KEY = "automation_paused_rooms"


def is_paused() -> bool:
    return db.kv_get(_AUTOMATION_PAUSED_KV_KEY) == "1"


def set_paused(paused: bool):
    db.kv_set(_AUTOMATION_PAUSED_KV_KEY, "1" if paused else "0")


def get_paused_rooms() -> set:
    raw = db.kv_get(_PAUSED_ROOMS_KV_KEY)
    if not raw:
        return set()
    try:
        return set(json.loads(raw))
    except Exception:
        return set()


def set_room_paused(room: str, paused: bool):
    rooms = get_paused_rooms()
    if paused:
        rooms.add(room)
    else:
        rooms.discard(room)
    db.kv_set(_PAUSED_ROOMS_KV_KEY, json.dumps(sorted(rooms)))


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

    # A room paused from Control Room means "leave this room's devices
    # alone" — the rule still evaluates and logs normally (so you can see
    # what it *would* have done), it just doesn't touch this specific
    # target. Other rooms in a multi-device action are unaffected.
    if room in get_paused_rooms():
        return f"{room}.{device} skipped — automation paused for this room"

    devices[room][device].update(set_fields)
    db.save_device_state(room, device, devices[room][device])
    return f"{room}.{device}→{set_fields}"


def action_targets(action) -> set:
    """(room, device) pairs an action writes to — used for conflict
    detection between rules, not on the hot path."""
    items = action if isinstance(action, list) else [action]
    return {(a.get("room"), a.get("device")) for a in items if isinstance(a, dict) and a.get("room")}


def find_rule_conflicts(rules: list) -> dict:
    """Non-blocking awareness, not enforcement: two *enabled* rules that can
    both write to the same device are flagged so whoever's editing rules can
    see it, rather than silently wondering why a light won't stay off. This
    intentionally doesn't try to reason about whether the conditions are
    mutually exclusive (e.g. two time-of-day rules at different hours are
    technically "conflicting" by this simple check even though they'd never
    both be true at once) — better a slightly noisy hint than a missed one.
    Returns {rule_id: [other rule names it shares a target with]}.
    """
    enabled = [r for r in rules if r.get("enabled")]
    targets_by_rule = {r["id"]: action_targets(r["action"]) for r in enabled}
    conflicts = {}
    for r in enabled:
        others = []
        for other in enabled:
            if other["id"] == r["id"]:
                continue
            if targets_by_rule[r["id"]] & targets_by_rule[other["id"]]:
                others.append(other["name"])
        if others:
            conflicts[r["id"]] = others
    return conflicts


def _contains_time_of_day(condition: dict) -> bool:
    """Recurses into and/or so a time_of_day buried inside a compound
    condition still gets the daily-fire safety net below."""
    if not isinstance(condition, dict):
        return False
    if condition.get("type") == "time_of_day":
        return True
    if condition.get("type") in ("and", "or"):
        return any(_contains_time_of_day(c) for c in condition.get("conditions", []))
    return False


# A time_of_day condition is only "true" for a short window (2 minutes by
# default) once a day — but nothing stops someone setting a rule's cooldown
# to e.g. 60s, which would let it re-fire 2-3 times inside that same window.
# Rather than trust every cooldown value at face value, clamp anything with
# a time_of_day condition to at least this, mirroring how per-member
# routines already protect themselves (_ROUTINE_COOLDOWN_SECONDS).
_MIN_TIME_OF_DAY_COOLDOWN_SECONDS = 21 * 3600


def _effective_cooldown(rule: dict) -> int:
    cooldown = rule.get("cooldown_seconds", 300)
    if _contains_time_of_day(rule.get("condition", {})):
        return max(cooldown, _MIN_TIME_OF_DAY_COOLDOWN_SECONDS)
    return cooldown


# ── Main evaluation loop ───────────────────────────────────────────────────

def evaluate_rules(devices: dict, sensors: dict, family_members: list, state_lock):
    """
    Called once per sensor tick. Checks every enabled rule;
    for any whose condition is met and isn't on cooldown, applies
    the action(s) and logs everything.
    """
    if is_paused():
        return

    rules = db.get_automation_rules(enabled_only=True)
    now   = time.time()

    for rule in rules:
        rule_id  = rule["id"]
        cooldown = _effective_cooldown(rule)

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
    today   = _DAY_NAMES[db.now_ist().weekday()]  # IST, so the day doesn't flip near midnight UTC
    allowed = [d.strip().lower() for d in days.split(",")]
    return today in allowed


def evaluate_routines(devices: dict, state_lock):
    if is_paused():
        return

    routines = db.get_enabled_routines_for_tick()
    now_ts   = time.time()
    now      = db.now_ist()  # routines are scheduled by a person in local (IST) time

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
