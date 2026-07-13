"""
Device responses for SMOKE / GAS / FIRE / INTRUDER alerts.

Targets devices already in the shared `devices` dict (sprinkler, window,
mains_power, siren, door_lock), so toggling them here plugs into the
existing persistence/floorplan/websocket code with no extra wiring.

Callers must hold state_lock before calling into this module (same
convention as automation.py's _apply_action) since these are invoked
from inside the sensor tick loop, which already holds it.

Every response is logged to automation_runs and audit_log.
"""

from applog import logger
import db


def _rooms_with_device(devices: dict, device_name: str):
    return [room for room, devs in devices.items() if device_name in devs]


def _set_device(devices: dict, room: str, device: str, value: bool, changes: list, label: str):
    if room in devices and device in devices[room]:
        devices[room][device]["on"] = value
        db.save_device_state(room, device, devices[room][device])
        changes.append(f"{room}.{device} -> {label}")


def _log_response(name: str, changes: list):
    if not changes:
        return
    detail = "; ".join(changes)
    db.add_automation_run(None, name, detail)
    db.add_audit_entry("system", "emergency_response", detail=f"{name}: {detail}")
    logger.warning("EMERGENCY RESPONSE — %s: %s", name, detail)


def respond_to_environmental_alert(alert_type: str, devices: dict) -> list:
    """alert_type: SMOKE / GAS / FIRE. Caller holds state_lock. Returns the
    list of changes made (empty if no matching devices exist)."""
    changes = []

    if alert_type in ("SMOKE", "FIRE"):
        for room in _rooms_with_device(devices, "sprinkler"):
            _set_device(devices, room, "sprinkler", True, changes, "ON")

    if alert_type in ("SMOKE", "FIRE", "GAS"):
        # Ventilate, cut power (spark risk), unlock for evacuation, sound siren
        for room in _rooms_with_device(devices, "window"):
            _set_device(devices, room, "window", True, changes, "OPEN")
        _set_device(devices, "security", "mains_power", False, changes, "OFF (cut for safety)")
        _set_device(devices, "security", "door_lock", False, changes, "UNLOCKED (evacuation)")
        _set_device(devices, "security", "siren", True, changes, "ON")

    _log_response(f"Emergency response — {alert_type}", changes)
    return changes


def respond_to_intruder(devices: dict) -> list:
    """Lock the house down. Caller holds state_lock."""
    changes = []
    for room in _rooms_with_device(devices, "window"):
        _set_device(devices, room, "window", False, changes, "CLOSED")
    _set_device(devices, "security", "door_lock", True, changes, "LOCKED")
    _set_device(devices, "security", "siren", True, changes, "ON")

    _log_response("Emergency response — INTRUDER", changes)
    return changes


def reset_to_normal(devices: dict) -> list:
    """Manual override. Doors and mains only come back on when this is
    called explicitly — someone should confirm the hazard is over first."""
    changes = []
    for room in _rooms_with_device(devices, "sprinkler"):
        _set_device(devices, room, "sprinkler", False, changes, "OFF")
    for room in _rooms_with_device(devices, "window"):
        _set_device(devices, room, "window", False, changes, "CLOSED")
    _set_device(devices, "security", "mains_power", True, changes, "ON (restored)")
    _set_device(devices, "security", "door_lock", True, changes, "LOCKED")
    _set_device(devices, "security", "siren", False, changes, "OFF")

    _log_response("Emergency response — RESET", changes)
    return changes
