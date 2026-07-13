"""
Smart Home Dashboard — FastAPI Backend
Integrates: Climate Control, AQI Monitor, Smoke/Fire/Gas Detector,
            Face Recognition Security, Device Control, Energy Tracking
"""

import os
import json
import time
import random
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Any, Union
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request, Response, Cookie, Depends, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, field_validator
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

import db
import auth
import automation
import alert_responses
from applog import logger, get_recent_logs

# ── SMOKE / GAS / FIRE DETECTION ──
from collections import deque

@dataclass
class SensorReading:
    timestamp: float
    smoke: float
    gas: float
    temperature: float

@dataclass
class AlertState:
    last_trigger_time: Optional[float] = None

class SmokeGasFireDetector:
    def __init__(self, smoke_threshold=60.0, gas_threshold=60.0,
                 temp_threshold=60.0, temp_spike_threshold=10.0,
                 debounce_seconds=300, false_alarm_consecutive=3, history_size=10):
        self.smoke_threshold = smoke_threshold
        self.gas_threshold = gas_threshold
        self.temp_threshold = temp_threshold
        self.temp_spike_threshold = temp_spike_threshold
        self.debounce_seconds = debounce_seconds
        self.false_alarm_consecutive = false_alarm_consecutive
        self.history = deque(maxlen=history_size)
        self.alert_states = {
            "SMOKE": AlertState(), "GAS": AlertState(), "FIRE": AlertState()
        }

    def add_reading(self, reading):
        self.history.append(reading)

    def _should_debounce(self, alert_type, now):
        state = self.alert_states[alert_type]
        if state.last_trigger_time is None:
            return False
        return (now - state.last_trigger_time) < self.debounce_seconds

    def _mark_alert_sent(self, alert_type, now):
        self.alert_states[alert_type].last_trigger_time = now

    def _is_false_alarm_filtered(self, predicate):
        if len(self.history) < self.false_alarm_consecutive:
            return True
        recent = list(self.history)[-self.false_alarm_consecutive:]
        return not all(predicate(r) for r in recent)

    def evaluate(self, reading):
        self.add_reading(reading)
        alerts = []
        now = reading.timestamp

        if reading.smoke >= self.smoke_threshold:
            if not self._is_false_alarm_filtered(lambda r: r.smoke >= self.smoke_threshold):
                if not self._should_debounce("SMOKE", now):
                    alerts.append({"type": "SMOKE", "level": "CRITICAL",
                                   "message": f"Smoke at {reading.smoke:.1f}%"})
                    self._mark_alert_sent("SMOKE", now)

        if reading.gas >= self.gas_threshold:
            if not self._is_false_alarm_filtered(lambda r: r.gas >= self.gas_threshold):
                if not self._should_debounce("GAS", now):
                    alerts.append({"type": "GAS", "level": "CRITICAL",
                                   "message": f"Gas leak at {reading.gas:.1f}%"})
                    self._mark_alert_sent("GAS", now)

        if reading.temperature >= self.temp_threshold:
            if not self._is_false_alarm_filtered(lambda r: r.temperature >= self.temp_threshold):
                if not self._should_debounce("FIRE", now):
                    alerts.append({"type": "FIRE", "level": "CRITICAL",
                                   "message": f"Fire risk — Temp {reading.temperature:.1f}°C"})
                    self._mark_alert_sent("FIRE", now)

        return alerts

# ── AQI CALCULATOR ──
PM25_BREAKPOINTS = [
    (0,30,0,50),(31,60,51,100),(61,90,101,200),
    (91,120,201,300),(121,250,301,400),(251,500,401,500)
]
PM10_BREAKPOINTS = [
    (0,50,0,50),(51,100,51,100),(101,250,101,200),
    (251,350,201,300),(351,430,301,400),(431,600,401,500)
]

def compute_sub_index(concentration, breakpoints):
    for (C_lo, C_hi, I_lo, I_hi) in breakpoints:
        if C_lo <= concentration <= C_hi:
            return round(((I_hi - I_lo) / (C_hi - C_lo)) * (concentration - C_lo) + I_lo)
    return 500

def get_aqi_category(aqi):
    if aqi <= 50: return "Good"
    elif aqi <= 100: return "Satisfactory"
    elif aqi <= 200: return "Moderate"
    elif aqi <= 300: return "Poor"
    elif aqi <= 400: return "Very Poor"
    else: return "Severe"

def calculate_aqi(pm25, pm10):
    a1 = compute_sub_index(pm25, PM25_BREAKPOINTS)
    a2 = compute_sub_index(pm10, PM10_BREAKPOINTS)
    final = max(a1, a2)
    return final, get_aqi_category(final)

# ── CLIMATE / HVAC LOGIC ──
def determine_hvac(indoor_temp, humidity):
    """Decide HVAC mode based on *indoor* temperature and humidity."""
    if indoor_temp >= 32.0:  return "MAX COOLING", 20.0
    elif indoor_temp >= 28.0 and humidity > 60: return "DEHUMIDIFY & COOL", 22.0
    elif indoor_temp >= 26.0: return "COOLING", 24.0
    elif indoor_temp >= 20.0: return "ECO MODE", 24.0
    else: return "HEATING", 24.0


def calculate_indoor_climate(outdoor_temp, outdoor_hum, all_devices):
    """
    Estimate room-by-room indoor temperature and humidity from the outdoor reading
    and the current state of ACs / fans in each room.

    Model (simplified but realistic for a Gurugram flat):
    ─────────────────────────────────────────────────────
    • Building insulation naturally shaves ~2°C off the outdoor temp.
    • AC on → the room temperature approaches the AC's set-point, and it
      acts as a dehumidifier, pulling humidity down towards ~50%.
    • Fan on → provides ~2–3°C of effective cooling from air circulation.
    • Nothing on → room sits at outdoor − insulation offset, and outdoor humidity.

    Returns (avg_indoor_temp, avg_indoor_hum, room_temps_dict, room_hums_dict)
    """
    INSULATION_OFFSET = 2.0
    FAN_MAX_COOLING   = 3.0
    AC_LEAK_FACTOR    = 0.20
    AC_DEHUMIDIFY_TARGET = 50.0
    AC_HUM_LEAK_FACTOR = 0.30

    base_indoor_temp = outdoor_temp - INSULATION_OFFSET
    base_indoor_hum  = outdoor_hum
    room_temps = {}
    room_hums = {}

    climate_rooms = [
        r for r in all_devices
        if any(d in all_devices[r] for d in ("ac", "fan"))
    ]
    if not climate_rooms:
        return round(base_indoor_temp, 1), round(base_indoor_hum, 1), {}, {}

    for room in climate_rooms:
        devs = all_devices[room]
        ac  = devs.get("ac", {})
        fan = devs.get("fan", {})

        room_temp = base_indoor_temp
        room_hum = base_indoor_hum

        if ac.get("on"):
            set_temp = ac.get("temp", 24)
            room_temp = set_temp + AC_LEAK_FACTOR * max(0, outdoor_temp - set_temp)
            # AC dehumidifies the room
            if outdoor_hum > AC_DEHUMIDIFY_TARGET:
                room_hum = AC_DEHUMIDIFY_TARGET + AC_HUM_LEAK_FACTOR * (outdoor_hum - AC_DEHUMIDIFY_TARGET)
            else:
                room_hum = outdoor_hum # AC doesn't add humidity
        elif fan.get("on"):
            speed = fan.get("speed", 0)
            fan_cooling = FAN_MAX_COOLING * (speed / 5) if speed > 0 else 0
            room_temp = base_indoor_temp - fan_cooling
            # fan doesn't change absolute humidity noticeably

        room_temps[room] = round(room_temp, 1)
        room_hums[room] = round(room_hum, 1)

    avg_temp = round(sum(room_temps.values()) / len(room_temps), 1)
    avg_hum = round(sum(room_hums.values()) / len(room_hums), 1)
    return avg_temp, avg_hum, room_temps, room_hums

# ──────────────────────────────────────────────
# THERMAL SIMULATION — first-order RC single-node room model
# ──────────────────────────────────────────────
# Each climate room integrates:  C·dT/dt = UA·(T_out − T) + Q_internal − Q_hvac
# so the AC/fan actually move the room temperature over time, instead of the
# target temperature just being stored with no physical effect.
THERMAL_C   = 600_000.0     # J/°C  thermal capacitance of a ~48 m³ room
THERMAL_UA  = 120.0         # W/°C  envelope heat-loss coefficient
Q_INTERNAL  = 200.0         # W     baseline internal gains (occupants + electronics)
AC_CAPACITY     = 5275.0    # W     heat removed by a 1.5-ton AC (mode == cooling)
HEATER_CAPACITY = 1500.0    # W     heat added in heating mode
INSULATION_OFFSET_INIT = 2.0  # °C  starting indoor offset below outdoor on first run

# Demo time acceleration: real seconds between ticks are multiplied by this
# factor before integrating, so a physically-accurate ~25-30 min cooldown
# compresses to ~3-4 min of real time. Set to 1 for true real-time physics.
SIM_SPEED_MULTIPLIER = 8

# Thermostat hysteresis: stop actively conditioning once within HYST_STOP_BAND
# of target; re-engage only after drift exceeds HYST_REENGAGE_BAND. This stops
# the compressor from oscillating on/off every single tick.
HYST_STOP_BAND     = 0.3    # °C
HYST_REENGAGE_BAND = 1.0    # °C

# Fan-speed multiplier applied to AC_CAPACITY (cooling only).
def _fan_cooling_multiplier(fan):
    if not fan or not fan.get("on"):
        return 0.85                     # "Auto" — AC's own internal blower
    spd = fan.get("speed", 0) or 0
    if spd <= 2:  return 0.6            # Low
    if spd == 3:  return 0.8            # Med
    return 1.0                          # High (speed 4-5)

# Fan evaporative "feels-like" offset — a fan cools skin, NOT the air itself,
# so this only affects feels_like, never the real room temperature.
def _fan_feels_offset(fan):
    if not fan or not fan.get("on"):
        return 0.0
    spd = fan.get("speed", 0) or 0
    if spd <= 2:  return 0.6
    if spd == 3:  return 1.0
    return 1.5

# Per-room thermal state: {room: {"T": float, "engaged": bool}}
room_thermal = {}

def _room_hvac_mode(ac, fan):
    if ac and ac.get("on"):
        return "heating" if ac.get("mode") == "heat" else "cooling"
    if fan and fan.get("on"):
        return "fan"
    return "off"

def thermal_tick(dt_real, t_out, all_devices):
    """Advance every climate room's temperature by one tick.
    Effective Δt = dt_real · SIM_SPEED_MULTIPLIER.
    Returns (avg_temp, room_temps, avg_feels_like, room_feels_like)."""
    dt = dt_real * SIM_SPEED_MULTIPLIER
    climate_rooms = [r for r in all_devices
                     if any(d in all_devices[r] for d in ("ac", "fan"))]
    room_temps, room_feels = {}, {}
    for room in climate_rooms:
        devs = all_devices[room]
        ac, fan = devs.get("ac"), devs.get("fan")
        st = room_thermal.get(room)
        if st is None or st.get("T") is None:
            st = {"T": max(18.0, min(34.0, t_out - INSULATION_OFFSET_INIT)), "engaged": False}
            room_thermal[room] = st
        T = st["T"]
        mode = _room_hvac_mode(ac, fan)
        target = (ac or {}).get("temp", 24)

        Q_hvac = 0.0
        if mode == "cooling":
            if st["engaged"]:
                if T <= target - HYST_STOP_BAND:
                    st["engaged"] = False          # reached target — cycle off
            else:
                if T >= target + HYST_REENGAGE_BAND:
                    st["engaged"] = True            # drifted too warm — cycle on
            if st["engaged"]:
                Q_hvac = AC_CAPACITY * _fan_cooling_multiplier(fan)   # positive = heat removed
        elif mode == "heating":
            if st["engaged"]:
                if T >= target + HYST_STOP_BAND:
                    st["engaged"] = False
            else:
                if T <= target - HYST_REENGAGE_BAND:
                    st["engaged"] = True
            if st["engaged"]:
                Q_hvac = -HEATER_CAPACITY                             # negative = heat added
        else:
            st["engaged"] = False                                    # fan / off: no heat transfer

        dTdt = (THERMAL_UA * (t_out - T) + Q_INTERNAL - Q_hvac) / THERMAL_C
        T = max(5.0, min(50.0, T + dt * dTdt))    # clamp against runaway
        st["T"] = T
        room_temps[room] = round(T, 1)
        room_feels[room] = round(T - _fan_feels_offset(fan), 1)

    if room_temps:
        avg_t = round(sum(room_temps.values()) / len(room_temps), 1)
        avg_f = round(sum(room_feels.values()) / len(room_feels), 1)
    else:
        avg_t = round(t_out - INSULATION_OFFSET_INIT, 1)
        avg_f = avg_t
    return avg_t, room_temps, avg_f, room_feels

# ──────────────────────────────────────────────
# CLIMATE HISTORY — feeds the dashboard range-filter charts
# ──────────────────────────────────────────────
# Indoor temp + humidity are sampled into this ring buffer every
# HISTORY_SAMPLE_SECONDS; /api/climate/history slices it by range and
# downsamples per-range so each zoom level returns a genuinely different
# window and point density.
HISTORY_SAMPLE_SECONDS = 120   # was 60 — sample the indoor climate every 2 minutes
_HISTORY_MAX = 24000           # ~33 days at a 120 s cadence
climate_history = deque(maxlen=_HISTORY_MAX)   # tuples: (epoch_seconds, temp, humidity)

def _seed_climate_history():
    """Back-fill plausible history so the 1H/6H/24H/7D/30D filters visibly
    differ immediately on a fresh start (demo). Live samples from the sensor
    loop take over going forward."""
    import math
    now = time.time()
    t = now - 30 * 86400
    base_t, base_h = 26.0, 55.0
    while t < now:
        diurnal = math.sin(2 * math.pi * ((t % 86400) / 86400.0 - 0.20))
        climate_history.append((
            t,
            round(base_t + 4.0 * diurnal + random.uniform(-0.4, 0.4), 1),
            round(base_h - 12.0 * diurnal + random.uniform(-1.5, 1.5), 1),
        ))
        t += HISTORY_SAMPLE_SECONDS

# NOTE: climate_history intentionally starts EMPTY. It is filled only by real
# samples taken in the sensor loop below — there is no demo pre-seeding. An empty
# buffer correctly means "no data yet", and /api/climate/history returns [] so the
# charts render a "No data available yet" message instead of a fake flat line.
# (_seed_climate_history remains defined but is deliberately never called.)

# ── SHARED STATE ──
state_lock = threading.RLock()  # must be reentrant — automation.evaluate_rules()/
# evaluate_routines() and alert_responses.* re-acquire this same lock from
# inside the tick loop that already holds it; a plain Lock() deadlocks.

# Device states — one room per family member + shared spaces
devices = {
    "living_room": {
        "light": {"on": True,  "brightness": 80, "watts": 12},
        "fan":   {"on": False, "speed": 0,  "watts": 45},
        "tv":    {"on": False, "watts": 120},
        "ac":    {"on": False, "temp": 24, "mode": "cool", "watts": 1500},
        "air_purifier": {"on": False, "speed": 2, "watts": 50},
        "sprinkler": {"on": False, "watts": 0},   # on = actively spraying (fire suppression)
        "window": {"on": False, "watts": 0}       # on = open
    },
    "aditya_room": {
        "light": {"on": False, "brightness": 70, "watts": 10},
        "fan":   {"on": True,  "speed": 2,  "watts": 45},
        "ac":    {"on": True,  "temp": 22, "mode": "cool", "watts": 1500},
        "sprinkler": {"on": False, "watts": 0},
        "window": {"on": False, "watts": 0}
    },
    "diksha_room": {
        "light": {"on": False, "brightness": 60, "watts": 10},
        "fan":   {"on": False, "speed": 0,  "watts": 45},
        "ac":    {"on": False, "temp": 24, "mode": "cool", "watts": 1500},
        "sprinkler": {"on": False, "watts": 0},
        "window": {"on": False, "watts": 0}
    },
    "agrim_room": {
        "light": {"on": False, "brightness": 70, "watts": 10},
        "fan":   {"on": False, "speed": 0,  "watts": 45},
        "ac":    {"on": False, "temp": 24, "mode": "cool", "watts": 1500},
        "sprinkler": {"on": False, "watts": 0},
        "window": {"on": False, "watts": 0}
    },
    "naman_room": {
        "light": {"on": False, "brightness": 70, "watts": 10},
        "fan":   {"on": False, "speed": 0,  "watts": 45},
        "sprinkler": {"on": False, "watts": 0},
        "window": {"on": False, "watts": 0}
    },
    "kamakshi_room": {
        "light": {"on": True,  "brightness": 80, "watts": 10},
        "fan":   {"on": True,  "speed": 1,  "watts": 45},
        "ac":    {"on": False, "temp": 24, "mode": "cool", "watts": 1500},
        "sprinkler": {"on": False, "watts": 0},
        "window": {"on": False, "watts": 0}
    },
    "kitchen": {
        "light":   {"on": True,  "brightness": 100, "watts": 15},
        "exhaust": {"on": False, "watts": 30},
        "sprinkler": {"on": False, "watts": 0},
        "window": {"on": False, "watts": 0}
    },
    "bathroom": {
        "light":   {"on": False, "brightness": 100, "watts": 8},
        "exhaust": {"on": False, "watts": 25},
        "window": {"on": False, "watts": 0}
    },
    "security": {
        # Door lock as a real backend-tracked device (was previously
        # frontend-only state) so automation rules can actually act on it
        # — e.g. auto-unlock on a fire/smoke alert.
        "door_lock": {"on": True, "watts": 0},   # on = locked, off = unlocked
        "siren": {"on": False, "watts": 10},     # on = sounding
        "mains_power": {"on": True, "watts": 0}  # on = house is supplied with power
    }
}

# Initialize the database (creates tables if they don't exist) before
# anything below tries to read or write from it.
db.init_db()

# Overlay any persisted device state on top of the defaults above, so devices
# remember their last on/off/brightness/speed across redeploys instead of
# always resetting to the hardcoded defaults every time the container restarts.
_persisted_devices = db.load_all_device_state()
for _room, _devs in _persisted_devices.items():
    if _room in devices:
        for _dev_name, _dev_state in _devs.items():
            if _dev_name in devices[_room]:
                devices[_room][_dev_name].update(_dev_state)

# Sensor data
sensors = {
    "temperature": None,          # indoor temp — None until the climate API returns real data
    "outdoor_temperature": None,  # raw outdoor temp from climate API
    "indoor_temperature": None,   # computed indoor temp (same as "temperature" when API is live)
    "feels_like": None,           # perceived temp (includes fan evaporative effect)
    "humidity": None,             # indoor humidity — None until the climate API returns real data
    "outdoor_humidity": None,     # raw outdoor humidity from climate API
    "indoor_humidity": None,      # computed indoor humidity
    "smoke": 3.2,
    "gas": 4.1,
    "aqi": 142,
    "aqi_category": "Moderate",
    "pm25": 85.0,
    "pm10": 120.0,
    "co2_ppm": 850.0,
    "hvac_status": None,          # None/OFFLINE until real data arrives (frontend shows "HVAC OFFLINE")
    "hvac_target": None,
    "condition": "partly cloudy",
    "room_temperatures": {},      # per-room estimated temps
    "room_humidities": {}         # per-room estimated humidities
}

# Family members — persisted in SQLite (data/smarthome.db), survives redeploys.
# Defaults below are only inserted once, the very first time the database is created.
_DEFAULT_FAMILY = [
    {"id": 1, "name": "Aditya", "role": "Owner", "status": "away", "avatar": "A", "color": "#4f46e5"},
    {"id": 2, "name": "Diksha", "role": "Member", "status": "away", "avatar": "D", "color": "#7c3aed"},
    {"id": 3, "name": "Agrim",  "role": "Member", "status": "away", "avatar": "Ag", "color": "#0891b2"},
    {"id": 4, "name": "Naman",  "role": "Member", "status": "away", "avatar": "N", "color": "#059669"},
    {"id": 5, "name": "Kamakshi","role":"Member", "status": "away", "avatar": "K", "color": "#dc2626"}
]
db.seed_family_if_empty(_DEFAULT_FAMILY)
family_members = db.get_family_members()

# Security logs — persisted in SQLite, starts empty on a fresh database.
# Real entries are added going forward by actual face recognition / manual
# logging; nothing here is pre-seeded demo data.
security_logs = db.get_security_logs()

# ── User accounts (authentication) ──────────────────────────────────────
# One login per family member, not a single shared admin password. On the
# very first run (empty users table) a random password is generated for
# each member and printed ONCE to the server console/logs — never stored
# in source code or committed to git. Whoever has access to the AWS
# console / docker logs retrieves these once and distributes them, then
# each person should change their password via /api/auth/change-password.
_GENERATED_PASSWORDS_THIS_RUN = []
if db.count_users() == 0:
    import secrets as _secrets
    for m in family_members:
        temp_password = f"{m['name'].lower()}123"
        role = "owner" if m["role"].lower() == "owner" else "member"
        auth.create_user_account(
            username=m["name"].lower(),
            password=temp_password,
            display_name=m["name"],
            role=role,
            member_id=m["id"],
        )
        _GENERATED_PASSWORDS_THIS_RUN.append((m["name"].lower(), temp_password))
    logger.info("First run — created %d default accounts (password = username + '123')", len(_GENERATED_PASSWORDS_THIS_RUN))
    print("=" * 55)
    print("  First run: default password = username + '123'")
    print("  e.g. naman -> naman123, aditya -> aditya123")
    print("=" * 55)

# ── Automation rules ─────────────────────────────────────────────────────
# A few real starter rules, inserted only on the very first run (empty
# table). Anyone can add/edit/disable more from the Automation page —
# these are just sensible defaults, not hardcoded behavior.
_DEFAULT_AUTOMATION_RULES = [
    # ── Air quality ─────────────────────────────────────────────────────────
    {
        "name": "Poor AQI → purifier on max",
        "description": "AQI > 200 (Poor): turn on living room air purifier at full speed.",
        "condition": {"type": "sensor_above", "key": "aqi", "threshold": 200},
        "action": {"room": "living_room", "device": "air_purifier", "set": {"on": True, "speed": 3}},
        "cooldown_seconds": 600,
    },
    {
        "name": "Good AQI → purifier standby",
        "description": "AQI back below 100 (Good): drop purifier to low speed to save power.",
        "condition": {"type": "sensor_below", "key": "aqi", "threshold": 100},
        "action": {"room": "living_room", "device": "air_purifier", "set": {"on": True, "speed": 1}},
        "cooldown_seconds": 1200,
    },
    {
        "name": "High CO₂ → open living room window + exhaust",
        "description": "CO₂ > 1000 ppm: open window and run kitchen exhaust to ventilate.",
        "condition": {"type": "sensor_above", "key": "co2_ppm", "threshold": 1000},
        "action": [
            {"room": "living_room", "device": "window", "set": {"on": True}},
            {"room": "kitchen",     "device": "exhaust","set": {"on": True}},
        ],
        "cooldown_seconds": 600,
    },

    # ── Temperature & climate (skipped automatically while climate API is
    #    down, since sensors["temperature"]/["humidity"] are None then) ────
    {
        "name": "Hot day → AC on (living room + bedrooms)",
        "description": "Temperature > 32 °C: turn on AC in living room, Aditya & Diksha rooms.",
        "condition": {"type": "sensor_above", "key": "temperature", "threshold": 32},
        "action": [
            {"room": "living_room", "device": "ac",  "set": {"on": True, "temp": 24, "mode": "cool"}},
            {"room": "aditya_room", "device": "ac",  "set": {"on": True, "temp": 24, "mode": "cool"}},
            {"room": "diksha_room", "device": "ac",  "set": {"on": True, "temp": 24, "mode": "cool"}},
        ],
        "cooldown_seconds": 1800,
    },
    {
        "name": "Cool night → AC off, fan on",
        "description": "Temperature below 24 °C at night: switch off AC, run fans instead.",
        "condition": {"type": "and", "conditions": [
            {"type": "sensor_below",  "key": "temperature", "threshold": 24},
            {"type": "time_of_day",   "hour": 22, "minute": 0, "window_minutes": 120},
        ]},
        "action": [
            {"room": "living_room", "device": "ac",  "set": {"on": False}},
            {"room": "aditya_room", "device": "ac",  "set": {"on": False}},
            {"room": "diksha_room", "device": "ac",  "set": {"on": False}},
            {"room": "living_room", "device": "fan", "set": {"on": True, "speed": 2}},
        ],
        "cooldown_seconds": 3600,
    },
    {
        "name": "High humidity → exhaust + AC dry mode",
        "description": "Humidity > 75%: run kitchen exhaust and set AC to dry mode.",
        "condition": {"type": "sensor_above", "key": "humidity", "threshold": 75},
        "action": [
            {"room": "kitchen",     "device": "exhaust", "set": {"on": True}},
            {"room": "living_room", "device": "ac",      "set": {"on": True, "mode": "dry"}},
        ],
        "cooldown_seconds": 900,
    },

    # ── Presence-based ───────────────────────────────────────────────────────
    {
        "name": "Nobody home 30 min → all lights off",
        "description": "Everyone away 30+ min: turn off all lights to save energy.",
        "condition": {"type": "nobody_home_minutes", "minutes": 30},
        "action": [
            {"room": "living_room",  "device": "light", "set": {"on": False}},
            {"room": "aditya_room",  "device": "light", "set": {"on": False}},
            {"room": "diksha_room",  "device": "light", "set": {"on": False}},
            {"room": "agrim_room",   "device": "light", "set": {"on": False}},
            {"room": "naman_room",   "device": "light", "set": {"on": False}},
            {"room": "kamakshi_room","device": "light", "set": {"on": False}},
            {"room": "kitchen",      "device": "light", "set": {"on": False}},
        ],
        "cooldown_seconds": 1800,
    },
    {
        "name": "Nobody home → AC + TV off",
        "description": "Everyone away: turn off AC units and TV.",
        "condition": {"type": "nobody_home_minutes", "minutes": 15},
        "action": [
            {"room": "living_room", "device": "ac",  "set": {"on": False}},
            {"room": "aditya_room", "device": "ac",  "set": {"on": False}},
            {"room": "diksha_room", "device": "ac",  "set": {"on": False}},
            {"room": "living_room", "device": "tv",  "set": {"on": False}},
        ],
        "cooldown_seconds": 900,
    },
    {
        "name": "Someone arrived home → welcome lights on",
        "description": "First person home: turn on living room & kitchen lights at 80%.",
        "condition": {"type": "someone_arrived_home"},
        "action": [
            {"room": "living_room", "device": "light", "set": {"on": True, "brightness": 80}},
            {"room": "kitchen",     "device": "light", "set": {"on": True, "brightness": 80}},
        ],
        "cooldown_seconds": 3600,
    },

    # ── Time-based (day/night) ───────────────────────────────────────────────
    {
        "name": "Morning (6 AM) → brighten lights",
        "description": "6 AM: kitchen and living room lights on bright for the morning routine.",
        "condition": {"type": "time_of_day", "hour": 6, "minute": 0, "window_minutes": 3},
        "action": [
            {"room": "kitchen",     "device": "light", "set": {"on": True, "brightness": 100}},
            {"room": "living_room", "device": "light", "set": {"on": True, "brightness": 90}},
        ],
        "cooldown_seconds": 82800,  # 23 h — once per day
    },
    {
        "name": "Night (11 PM) → dim everything",
        "description": "11 PM: dim all lights to 30%, turn off TV and exhaust.",
        "condition": {"type": "time_of_day", "hour": 23, "minute": 0, "window_minutes": 3},
        "action": [
            {"room": "living_room",  "device": "light",   "set": {"on": True, "brightness": 30}},
            {"room": "kitchen",      "device": "light",   "set": {"on": True, "brightness": 30}},
            {"room": "living_room",  "device": "tv",      "set": {"on": False}},
            {"room": "kitchen",      "device": "exhaust", "set": {"on": False}},
        ],
        "cooldown_seconds": 82800,
    },

    # ── Safety (belt-and-braces beyond alert_responses.py) ──────────────────
    {
        "name": "High smoke → unlock door (rule backup)",
        "description": "Smoke > 40%: unlock front door so occupants can evacuate.",
        "condition": {"type": "sensor_above", "key": "smoke", "threshold": 40},
        "action": {"room": "security", "device": "door_lock", "set": {"on": False}},
        "cooldown_seconds": 300,
    },
    {
        "name": "AC on + window open → close window",
        "description": "If AC is running and a window is open in the living room, close it (wasted energy).",
        "condition": {"type": "and", "conditions": [
            {"type": "device_state", "room": "living_room", "device": "ac",     "on": True},
            {"type": "device_state", "room": "living_room", "device": "window", "on": True},
        ]},
        "action": {"room": "living_room", "device": "window", "set": {"on": False}},
        "cooldown_seconds": 600,
    },

    # ── Predictive (uses the hourly-refreshed aqi_forecast_tomorrow virtual
    #    sensor set in simulate_sensors — see _compute_aqi_forecast) ────────
    {
        "name": "Poor AQI forecast → pre-run purifier overnight",
        "description": "If tomorrow's forecast AQI is Poor (>200), start the purifier tonight instead of waiting for tomorrow's air to actually turn bad.",
        "condition": {"type": "sensor_above", "key": "aqi_forecast_tomorrow", "threshold": 200},
        "action": {"room": "living_room", "device": "air_purifier", "set": {"on": True, "speed": 2}},
        "cooldown_seconds": 21600,  # 6h — forecast only refreshes hourly anyway
    },
]
db.seed_automation_rules_if_empty(_DEFAULT_AUTOMATION_RULES)

# Smoke/fire detector instance
detector = SmokeGasFireDetector(smoke_threshold=40.0, gas_threshold=40.0,
                                temp_threshold=50.0, temp_spike_threshold=8.0,
                                debounce_seconds=120)

# WebSocket connections
ws_clients: List[WebSocket] = []
alert_history = []

# ── ENERGY CALCULATION ──
def calculate_energy():
    """Returns instantaneous power draw in watts (used internally for the live wattage figure)."""
    total_watts = 0
    for room, devs in devices.items():
        for dev_name, dev in devs.items():
            if dev.get("on", False):
                w = dev.get("watts", 0)
                if dev_name == "fan":
                    speed = dev.get("speed", 0)
                    w = int(w * (speed / 5)) if speed > 0 else 0
                elif dev_name == "light":
                    brightness = dev.get("brightness", 100)
                    w = int(w * (brightness / 100))
                total_watts += w
    return total_watts


# ── DHBVN Gurugram domestic electricity tariff ──
# Dakshin Haryana Bijli Vitran Nigam (DHBVN) serves Gurugram. Slabs below are
# Category II domestic (load up to 5kW — typical for a house), per the HERC
# tariff order effective 01-04-2025 (FY 2025-26), as published in DHBVN's
# sales circular. No fixed/minimum monthly charge applies below 300 units.
# Source: DHBVN sales circular 04_D_2025, cross-checked against HERC tariff
# order coverage (Tribune India, India TV News, April 2025).
DHBVN_DOMESTIC_SLABS = [
    # (units_up_to, rate_per_unit)  — units_up_to=None means "and above"
    (150, 2.95),
    (300, 5.25),
    (500, 6.45),
    (None, 7.10),
]
# Fixed charge applies only once monthly consumption exceeds 300 units
DHBVN_FIXED_CHARGE_PER_KW_ABOVE_300 = 50.0
SANCTIONED_LOAD_KW = 5.0  # assumed household sanctioned load for fixed-charge calc


def calculate_dhbvn_bill(units: float):
    """
    Slab-wise DHBVN Category-II domestic bill calculation.
    Returns dict with per-slab breakdown, energy charge, fixed charge, and total.
    """
    remaining = units
    prev_cap = 0
    breakdown = []
    energy_charge = 0.0

    for cap, rate in DHBVN_DOMESTIC_SLABS:
        if remaining <= 0:
            break
        slab_size = (cap - prev_cap) if cap is not None else remaining
        units_in_slab = min(remaining, slab_size)
        if units_in_slab <= 0:
            prev_cap = cap if cap is not None else prev_cap
            continue
        slab_cost = round(units_in_slab * rate, 2)
        breakdown.append({
            "range": f"{prev_cap + 1}-{cap}" if cap is not None else f"Above {prev_cap}",
            "units": round(units_in_slab, 2),
            "rate": rate,
            "cost": slab_cost
        })
        energy_charge += slab_cost
        remaining -= units_in_slab
        prev_cap = cap if cap is not None else prev_cap

    fixed_charge = (SANCTIONED_LOAD_KW * DHBVN_FIXED_CHARGE_PER_KW_ABOVE_300) if units > 300 else 0.0

    return {
        "units": round(units, 2),
        "breakdown": breakdown,
        "energy_charge": round(energy_charge, 2),
        "fixed_charge": round(fixed_charge, 2),
        "total": round(energy_charge + fixed_charge, 2)
    }

# ──────────────────────────────────────────────
# SENSOR SIMULATION LOOP
# ──────────────────────────────────────────────
# URL of the climate-control micro-service (api_server.py) which fetches
# real weather data from OpenWeather every 60s. server.py polls this
# endpoint for temperature, humidity, and weather condition instead of
# generating random numbers.
# Host of the climate-control micro-service. Defaults to "localhost" so running
# both scripts directly (no Docker) works unchanged. Under docker-compose this is
# set to the api-server service name, which Docker's internal DNS resolves to the
# sibling container. CLIMATE_API_URL can still override the full URL if needed.
API_SERVER_HOST = os.getenv("API_SERVER_HOST", "localhost")
CLIMATE_API_URL = os.getenv("CLIMATE_API_URL", f"http://{API_SERVER_HOST}:3000/api/all")

def _fetch_climate_data():
    """Fetch real weather data from the climate-control service.
    Returns (temperature, humidity, condition, hvac_status, hvac_target) on
    success, or None if the service is unreachable / returned bad data."""
    try:
        import requests as _req
        r = _req.get(CLIMATE_API_URL, timeout=5)
        if r.status_code != 200:
            logger.warning("Climate API returned HTTP %s from %s", r.status_code, CLIMATE_API_URL)
            return None
        data = r.json()
        temp = data.get("weather", {}).get("temperature")
        hum  = data.get("weather", {}).get("humidity")
        cond = data.get("weather", {}).get("condition")
        hvac_st  = data.get("hvac", {}).get("status")
        hvac_tgt = data.get("hvac", {}).get("target_temp")
        if temp is None or hum is None:
            return None
        return (float(temp), float(hum), cond or "unknown", hvac_st, hvac_tgt)
    except Exception as e:
        logger.warning("Climate API unreachable (%s)", e)
        return None

# Tracks whether the climate API has ever returned data since server start.
# When False, temperature/humidity display as None ("--" on the dashboard)
# rather than fake random numbers — the user explicitly asked for this.
_climate_api_available = False

def simulate_sensors():
    global sensors, _climate_api_available
    aqi_base_pm25 = 95.0
    aqi_base_pm10 = 145.0
    last_aqi_update = 0.0
    last_climate_fetch = 0.0
    last_history_sample = 0.0
    last_thermal_ts = time.time()

    consecutive_failures = 0
    outdoor_temp = None         # current outdoor temperature (None = API down)
    outdoor_hum = None

    while True:
        try:
            now = time.time()
            with state_lock:
                # ── Outdoor weather from the climate API, refreshed every 60 s ──
                if now - last_climate_fetch >= 60:
                    climate = _fetch_climate_data()
                    if climate:
                        real_temp, real_hum, condition, _hst, _htgt = climate
                        if consecutive_failures >= 3:
                            logger.info("Climate API recovered after %d failed checks — live weather restored", consecutive_failures)
                        _climate_api_available = True
                        consecutive_failures = 0
                        # Use every successful reading directly — no rolling average,
                        # so each API call yields a real, immediate data point.
                        outdoor_temp = round(real_temp, 1)
                        outdoor_hum  = round(real_hum, 1)
                        sensors["condition"] = condition
                        logger.info("Climate API → outdoor %.1f°C / %.0f%%, %s",
                                    outdoor_temp, outdoor_hum, condition)
                    else:
                        consecutive_failures += 1
                        if consecutive_failures >= 3:
                            _climate_api_available = False
                            outdoor_temp = None
                            outdoor_hum = None
                            sensors["condition"] = None
                            logger.warning("Climate API down for %d checks — showing 'no data'", consecutive_failures)
                    last_climate_fetch = now

                # ── Indoor climate: RC thermal model, integrated every tick ──
                dt_real = now - last_thermal_ts
                last_thermal_ts = now
                if outdoor_temp is not None:
                    indoor_temp, room_temps, feels_like, room_feels = thermal_tick(dt_real, outdoor_temp, devices)
                    # Humidity keeps the instantaneous mixing model (thermal model is temp-only)
                    _at, indoor_hum, _rt, room_hums = calculate_indoor_climate(outdoor_temp, outdoor_hum, devices)
                    sensors["outdoor_temperature"] = round(outdoor_temp + random.uniform(-0.2, 0.2), 1)
                    sensors["outdoor_humidity"]    = round(outdoor_hum + random.uniform(-0.8, 0.8), 1)
                    sensors["temperature"]         = indoor_temp
                    sensors["indoor_temperature"]  = indoor_temp
                    sensors["feels_like"]          = feels_like
                    sensors["room_temperatures"]   = room_temps
                    sensors["room_feels_like"]     = room_feels
                    sensors["humidity"]            = indoor_hum
                    sensors["indoor_humidity"]     = indoor_hum
                    sensors["room_humidities"]     = room_hums
                    hvac_status, hvac_target = determine_hvac(indoor_temp, indoor_hum)
                    sensors["hvac_status"] = hvac_status
                    sensors["hvac_target"] = hvac_target
                else:
                    for k in ("temperature", "indoor_temperature", "outdoor_temperature",
                              "feels_like", "humidity", "indoor_humidity", "outdoor_humidity"):
                        sensors[k] = None
                    sensors["hvac_status"] = "OFFLINE"
                    sensors["hvac_target"] = None
                    sensors["room_temperatures"] = {}
                    sensors["room_humidities"] = {}

                # ── Sample indoor climate into history for the range charts ──
                if sensors["temperature"] is not None and now - last_history_sample >= HISTORY_SAMPLE_SECONDS:
                    climate_history.append((now, sensors["temperature"], sensors["humidity"]))
                    last_history_sample = now

                # ── Smoke / gas (still simulated) ──
                sensors["smoke"] = round(random.uniform(1.5, 8.0), 1)
                sensors["gas"]   = round(random.uniform(1.0, 6.0), 1)

                # ── AQI every 60 s ──
                if now - last_aqi_update >= 60:
                    aqi_base_pm25 = round(max(40, min(220, aqi_base_pm25 + random.uniform(-10, 10))), 1)
                    aqi_base_pm10 = round(max(60, min(300, aqi_base_pm10 + random.uniform(-12, 12))), 1)
                    aqi_val, aqi_cat = calculate_aqi(aqi_base_pm25, aqi_base_pm10)
                    sensors["pm25"] = aqi_base_pm25
                    sensors["pm10"] = aqi_base_pm10
                    sensors["aqi"] = aqi_val
                    sensors["aqi_category"] = aqi_cat
                    sensors["co2_ppm"] = round(random.uniform(700, 1100), 0)
                    last_aqi_update = now

                # ── Predictive AQI (hourly) ──────────────────────────────
                # Exposes tomorrow's forecast AQI as a virtual sensor so
                # ordinary sensor_above rules can react to it, e.g.
                # "aqi_forecast_tomorrow > 200 -> run purifier tonight".
                # Hourly refresh only — LSTM inference is too slow for every
                # 3s tick — and skipped entirely if the ML deps aren't installed.
                if now - _last_aqi_forecast_fetch[0] >= 3600:
                    _last_aqi_forecast_fetch[0] = now
                    try:
                        predict_fn = _get_aqi_predictor()
                        if predict_fn:
                            forecast = _compute_aqi_forecast(predict_fn, sensors)
                            if forecast:
                                sensors["aqi_forecast_tomorrow"] = forecast[0]
                    except Exception as e:
                        logger.warning("Predictive AQI refresh failed: %s", e)

                # ── Smoke/fire detection ──
                temp_for_detector = sensors["temperature"] if sensors["temperature"] is not None else 25.0
                reading = SensorReading(timestamp=now, smoke=sensors["smoke"],
                                        gas=sensors["gas"], temperature=temp_for_detector)
                for alert in detector.evaluate(reading):
                    alert_entry = {"type": alert["type"], "level": alert["level"],
                                   "message": alert["message"], "time": datetime.now().strftime("%H:%M:%S")}
                    alert_history.append(alert_entry)
                    if len(alert_history) > 50:
                        alert_history.pop(0)
                    # Automated device responses (sprinklers, mains cutoff, evacuation
                    # unlock, siren) — runs inside state_lock, safe now that it's an RLock.
                    try:
                        changes = alert_responses.respond_to_environmental_alert(alert["type"], devices)
                        if changes:
                            alert_entry["auto_actions"] = changes
                    except Exception as ae:
                        logger.error("alert_responses error: %s", ae, exc_info=True)

                # ── Automation rules + per-member routines ──
                automation.evaluate_rules(devices, sensors, family_members, state_lock)
                automation.evaluate_routines(devices, state_lock)
        except Exception as e:
            logger.error("Sensor simulation loop error: %s", e, exc_info=True)

        time.sleep(3)

# ──────────────────────────────────────────────
# FASTAPI APP
# ──────────────────────────────────────────────
app = FastAPI(title="Smart Home API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    # Same-origin dashboard doesn't need CORS at all; this list only affects
    # cross-origin callers (e.g. API testing tools). Wildcard + credentials is
    # both insecure and rejected by browsers, so restrict to one known origin.
    allow_origins=[os.getenv("CORS_ORIGIN", "http://localhost:8000")],
    allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Adds standard defensive headers to every response."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response

# ── Rate limiting (slowapi) ──────────────────────────────────────────────
# Per-client-IP limits on every route. In-memory storage — resets on restart,
# which is fine for a single-container deployment.

def get_real_client_ip(request: Request) -> str:
    """Rate-limit key: the real client IP. Behind Nginx every request's socket
    peer is the proxy, which would put ALL users in one shared rate-limit
    bucket — so use X-Forwarded-For when present, taking the LAST hop (the IP
    Nginx actually observed and appended). The FIRST XFF value is client-supplied
    and can be spoofed to dodge the limiter, so it must never be trusted. Falls
    back to the direct client IP for local/no-proxy runs."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=get_real_client_ip)
app.state.limiter = limiter


def _rate_limit_exceeded(request: Request, exc: RateLimitExceeded):
    # Custom 429 body so the frontend can detect it and show a toast
    # instead of slowapi's default plain-text error.
    retry_after = 60
    try:
        retry_after = int(exc.limit.limit.get_expiry())
    except Exception:
        pass
    response = JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "message": "Too many requests — please wait a moment",
            "retry_after": retry_after,
        },
    )
    response.headers["Retry-After"] = str(retry_after)
    return response


app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded)

# Catch anything that isn't an explicit HTTPException — log the full
# traceback so a live crash actually leaves a record instead of just
# becoming a generic 500 with nothing written down anywhere.
@app.exception_handler(Exception)
async def log_unhandled_exceptions(request: Request, exc: Exception):
    logger.error(
        "Unhandled exception on %s %s: %s",
        request.method, request.url.path, exc, exc_info=True
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Mount images directory (reference face photos for in-browser recognition)
images_dir = os.path.join(os.path.dirname(__file__), "images")
os.makedirs(images_dir, exist_ok=True)
app.mount("/images", StaticFiles(directory=images_dir), name="images")

# Face-data directory holds embeddings.json. It is deliberately NOT served as a
# public static mount — biometric embeddings are returned only via the
# authenticated GET /api/face-embeddings endpoint below.
FACE_DATA_DIR = os.getenv("FACE_DATA_DIR", os.path.join(os.path.dirname(__file__), "face-data"))
os.makedirs(FACE_DATA_DIR, exist_ok=True)

# Mount tools directory (standalone browser utilities, e.g. enroll-faces.html —
# the "Register Face" link in the dashboard opens /tools/enroll-faces.html).
tools_dir = os.path.join(os.path.dirname(__file__), "tools")
os.makedirs(tools_dir, exist_ok=True)
app.mount("/tools", StaticFiles(directory=tools_dir), name="tools")

# ──────────────────────────────────────────────
# PYDANTIC MODELS
# ──────────────────────────────────────────────
class DeviceToggle(BaseModel):
    room: str
    device: str
    value: Any

class MemberAdd(BaseModel):
    name: str
    role: str = "Member"

class LogAdd(BaseModel):
    person: str
    type: str  # member / guest / delivery / intruder
    event: str
    status: str
    estimated: Optional[str] = None

class LoginRequest(BaseModel):
    username: str
    password: str

# ──────────────────────────────────────────────
# ENDPOINTS
# ──────────────────────────────────────────────

# ── Authentication ──────────────────────────────
SESSION_COOKIE_NAME = "smarthome_session"

# Secure flag on the session cookie — set SECURE_COOKIES=true in production
# (HTTPS). Default off so plain-HTTP local dev keeps working; browsers do
# accept Secure cookies on http://localhost, so docker-compose can enable it.
SECURE_COOKIES = os.getenv("SECURE_COOKIES", "false").lower() == "true"


async def get_current_user(smarthome_session: Optional[str] = Cookie(default=None)):
    """FastAPI dependency — raises 401 if there's no valid session cookie.
    Use as: async def some_endpoint(user: dict = Depends(get_current_user))"""
    user = auth.get_session_user(smarthome_session)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


async def get_current_user_optional(smarthome_session: Optional[str] = Cookie(default=None)):
    """Same as above but returns None instead of raising — for endpoints that
    behave differently when logged in vs not, without hard-blocking access."""
    return auth.get_session_user(smarthome_session)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.post("/api/auth/login")
@limiter.limit("5/minute")
async def login(body: LoginRequest, request: Request, response: Response):
    user = auth.authenticate(body.username, body.password)
    if not user:
        db.add_audit_entry(body.username, "login_failed", ip_address=_client_ip(request))
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = auth.start_session(user["id"])
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=SECURE_COOKIES,
        samesite="lax",
        max_age=auth.SESSION_DURATION_HOURS * 3600,
    )
    db.add_audit_entry(user["username"], "login", ip_address=_client_ip(request))
    return {
        "ok": True,
        "user": {
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user["role"],
            "member_id": user["member_id"],
        }
    }


@app.post("/api/auth/logout")
@limiter.limit("10/minute")
async def logout(request: Request, response: Response, smarthome_session: Optional[str] = Cookie(default=None)):
    user = auth.get_session_user(smarthome_session)
    if user:
        db.add_audit_entry(user["username"], "logout", ip_address=_client_ip(request))
    auth.end_session(smarthome_session)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


@app.get("/api/auth/me")
@limiter.limit("30/minute")
async def get_me(request: Request, user: dict = Depends(get_current_user)):
    return {
        "username": user["username"],
        "display_name": user["display_name"],
        "role": user["role"],
        "member_id": user["member_id"],
    }


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

@app.post("/api/auth/change-password")
@limiter.limit("5/minute")
async def change_password(body: ChangePasswordRequest, request: Request, user: dict = Depends(get_current_user)):
    full_user = db.get_user_by_username(user["username"])
    if not auth.verify_password(body.current_password, full_user["password_hash"], full_user["password_salt"]):
        raise HTTPException(status_code=403, detail="Current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")
    new_hash, new_salt = auth.hash_password(body.new_password)
    db.update_user_password(full_user["id"], new_hash, new_salt)
    db.add_audit_entry(user["username"], "password_changed", ip_address=_client_ip(request))
    return {"ok": True}


@app.get("/api/audit-log")
@limiter.limit("30/minute")
async def get_audit_log(request: Request, user: dict = Depends(get_current_user)):
    # Any logged-in member can view the audit log — transparency for the whole household
    return db.get_audit_log()


@app.get("/api/system-logs")
@limiter.limit("30/minute")
async def get_system_logs(request: Request, lines: int = 200, level: Optional[str] = None, user: dict = Depends(get_current_user)):
    """
    Server-side error/debug log — distinct from security_logs (who arrived
    at the house) and audit_log (who changed what in the dashboard). This is
    for diagnosing the live deployment: crashes, unhandled exceptions, sensor
    thread errors, startup events. Gated behind login since it can contain
    internal detail (stack traces, file paths) not meant for public viewing.
    """
    if user["role"] != "owner":
        raise HTTPException(status_code=403, detail="Only the owner account can view system logs")
    lines = max(1, min(lines, 1000))
    return {"lines": get_recent_logs(lines=lines, level=level)}


class FaceEmbeddingsUpload(BaseModel):
    embeddings: list

    @field_validator("embeddings")
    @classmethod
    def _validate_embeddings(cls, v):
        # Bound the payload so an authenticated member can't fill the disk with a
        # giant upload. A real face-api descriptor is exactly 128 floats in
        # [-1, 1]; a household is a handful of members with a few photos each.
        if len(v) > 10:
            raise ValueError("At most 10 entries allowed per upload")
        for entry in v:
            if not isinstance(entry, dict):
                raise ValueError("Each embedding entry must be an object")
            descs = entry.get("descriptors")
            if not isinstance(descs, list):
                raise ValueError("Each entry must include a 'descriptors' list")
            if len(descs) > 10:
                raise ValueError("At most 10 descriptors per person allowed")
            for d in descs:
                if not isinstance(d, list) or len(d) != 128:
                    raise ValueError("Each descriptor must be exactly 128 floats")
                for f in d:
                    if isinstance(f, bool) or not isinstance(f, (int, float)):
                        raise ValueError("Descriptor values must be numbers")
                    if f < -1.0 or f > 1.0:
                        raise ValueError("Descriptor floats must be in range -1.0 to 1.0")
        return v


@app.post("/api/face-embeddings")
@limiter.limit("10/minute")
async def save_face_embeddings(body: FaceEmbeddingsUpload, request: Request, user: dict = Depends(get_current_user)):
    """Register the CALLER'S OWN face embedding.

    Any authenticated member may enroll, but only for themselves: the entry is
    always stored under the session's own display name (any name in the payload
    is ignored), and it is MERGED into face-data/embeddings.json — replacing
    only this member's entry and leaving every other member's data untouched.
    So a member can never add or overwrite another member's face. The file
    stays a JSON array, matching what the dashboard loads from
    /face-data/embeddings.json."""
    my_name = user["display_name"]

    # Collect the caller's descriptors from the payload. The payload's name is
    # deliberately ignored — a member can only ever write their OWN entry.
    descriptors = []
    for entry in body.embeddings:
        if isinstance(entry, dict) and isinstance(entry.get("descriptors"), list):
            descriptors.extend(entry["descriptors"])
    if not descriptors:
        raise HTTPException(status_code=400, detail="No face descriptors provided")

    os.makedirs(FACE_DATA_DIR, exist_ok=True)
    path = os.path.join(FACE_DATA_DIR, "embeddings.json")

    # Load existing entries so other members' data is preserved on write.
    existing = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                existing = loaded
        except Exception:
            existing = []

    # Drop any previous entry for THIS member, then append the fresh one.
    merged = [e for e in existing
              if not (isinstance(e, dict) and e.get("name") == my_name)]
    merged.append({"name": my_name, "descriptors": descriptors})

    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f)
    db.add_audit_entry(user["username"], "face_embeddings_updated",
                       detail=f"{my_name}: {len(descriptors)} descriptor(s)", ip_address=_client_ip(request))
    return {"success": True}


@app.get("/api/face-embeddings")
@limiter.limit("30/minute")
async def get_face_embeddings(request: Request, user: dict = Depends(get_current_user)):
    """Return the enrolled face embeddings (the JSON array the dashboard's face
    matcher loads). Auth-gated — biometric data is no longer exposed via a public
    static mount. 404 when nothing has been enrolled yet."""
    path = os.path.join(FACE_DATA_DIR, "embeddings.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="No embeddings enrolled yet")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Automation rules ────────────────────────────────────────────────────

class AutomationConditionIn(BaseModel):
    type: str  # sensor_above / sensor_below / nobody_home_minutes / time_of_day /
                # aqi_category / device_state / someone_arrived_home / and / or
    key: Optional[str] = None
    threshold: Optional[float] = None
    minutes: Optional[int] = None
    hour: Optional[int] = None
    minute: Optional[int] = None
    window_minutes: Optional[int] = None
    category: Optional[str] = None       # for aqi_category
    room: Optional[str] = None           # for device_state
    device: Optional[str] = None         # for device_state
    on: Optional[bool] = None            # for device_state
    conditions: Optional[List[dict]] = None  # for and/or — nested conditions kept
                                               # as plain dicts rather than a
                                               # recursive Pydantic model to
                                               # keep this simple; automation.py
                                               # validates their shape at eval time

class AutomationActionIn(BaseModel):
    room: str
    device: str
    set: dict

class AutomationRuleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    condition: AutomationConditionIn
    action: Union[AutomationActionIn, List[AutomationActionIn]]  # single device
             # or multiple — matches automation.py's _apply_action, which
             # already handles both shapes
    enabled: bool = True
    cooldown_seconds: int = 300


@app.get("/api/automation/rules")
@limiter.limit("30/minute")
async def list_automation_rules(request: Request, user: dict = Depends(get_current_user)):
    return db.get_automation_rules()


@app.post("/api/automation/rules")
@limiter.limit("10/minute")
async def create_automation_rule(body: AutomationRuleCreate, request: Request, user: dict = Depends(get_current_user)):
    # Validate every action target is a real device before saving — a rule
    # pointing at a room/device that doesn't exist would silently no-op
    # forever, which is worse than rejecting it up front. Handles both a
    # single action and a multi-device action list.
    actions = body.action if isinstance(body.action, list) else [body.action]
    for a in actions:
        if a.room not in devices or a.device not in devices[a.room]:
            raise HTTPException(status_code=400, detail=f"{a.room}.{a.device} is not a real device")
    action_payload = [a.dict() for a in actions] if isinstance(body.action, list) else body.action.dict()
    rule = db.create_automation_rule(
        name=body.name, description=body.description,
        condition=body.condition.dict(exclude_none=True),
        action=action_payload,
        enabled=body.enabled, cooldown_seconds=body.cooldown_seconds,
    )
    db.add_audit_entry(user["username"], "automation_rule_created", detail=body.name, ip_address=_client_ip(request))
    return rule


@app.post("/api/automation/rules/{rule_id}/toggle")
@limiter.limit("10/minute")
async def toggle_automation_rule(rule_id: int, request: Request, user: dict = Depends(get_current_user)):
    rule = db.get_automation_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.update_automation_rule_enabled(rule_id, not rule["enabled"])
    db.add_audit_entry(user["username"], "automation_rule_toggled", detail=f"{rule['name']} -> {'enabled' if not rule['enabled'] else 'disabled'}", ip_address=_client_ip(request))
    return db.get_automation_rule(rule_id)


@app.delete("/api/automation/rules/{rule_id}")
@limiter.limit("10/minute")
async def delete_automation_rule(rule_id: int, request: Request, user: dict = Depends(get_current_user)):
    rule = db.get_automation_rule(rule_id)
    db.delete_automation_rule(rule_id)
    db.add_audit_entry(user["username"], "automation_rule_deleted", detail=rule["name"] if rule else str(rule_id), ip_address=_client_ip(request))
    return {"ok": True}


@app.get("/api/automation/runs")
@limiter.limit("30/minute")
async def list_automation_runs(request: Request, user: dict = Depends(get_current_user)):
    return db.get_automation_runs()


@app.get("/api/automation/status")
@limiter.limit("30/minute")
async def automation_status(request: Request, user: dict = Depends(get_current_user)):
    """Live status for every rule: when it last fired, cooldown remaining."""
    rules = db.get_automation_rules(enabled_only=False)
    now = time.time()
    result = []
    for r in rules:
        last = automation._last_fired.get(r["id"], 0)
        cooldown = r.get("cooldown_seconds", 300)
        remaining = max(0, int(cooldown - (now - last)))
        last_str = datetime.fromtimestamp(last).strftime("%H:%M:%S") if last > 0 else None
        result.append({
            "id": r["id"], "name": r["name"], "enabled": r["enabled"],
            "last_fired": last_str, "cooldown_remaining": remaining,
            "on_cooldown": remaining > 0,
        })
    return result


@app.post("/api/automation/rules/{rule_id}/test")
@limiter.limit("30/minute")
async def test_automation_rule(rule_id: int, request: Request, user: dict = Depends(get_current_user)):
    """Dry-run: evaluate the rule's condition right now without changing anything."""
    rules = db.get_automation_rules()
    rule = next((r for r in rules if r["id"] == rule_id), None)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    with state_lock:
        try:
            met = automation._condition_met(rule["condition"], sensors, family_members, devices)
        except Exception as e:
            return {"ok": False, "condition_met": False, "error": str(e)}
    action = rule["action"]
    if isinstance(action, list):
        would_do = [f"{a['room']}.{a['device']} → {a['set']}" for a in action]
    else:
        would_do = [f"{action['room']}.{action['device']} → {action['set']}"]
    return {
        "ok": True, "condition_met": met, "would_do": would_do,
        "cooldown_remaining": max(0, int(
            rule.get("cooldown_seconds", 300) - (time.time() - automation._last_fired.get(rule_id, 0))
        )),
    }


# ── Automation template bundles ────────────────────────────────────────────
_TEMPLATE_BUNDLES = [
    {
        "id": "vacation_mode", "name": "Vacation Mode", "icon": "✈️", "color": "#6366f1",
        "description": "Nobody home for days — absolute minimum power, maximum security.",
        "rules": [
            {"name": "[Vacation] Nobody home 10 min → all lights off", "description": "Part of Vacation Mode bundle.",
             "condition": {"type": "nobody_home_minutes", "minutes": 10},
             "action": [{"room": r, "device": "light", "set": {"on": False}}
                        for r in ["living_room","aditya_room","diksha_room","agrim_room","naman_room","kamakshi_room","kitchen"]],
             "cooldown_seconds": 600},
            {"name": "[Vacation] Nobody home 10 min → AC + fans off", "description": "Part of Vacation Mode bundle.",
             "condition": {"type": "nobody_home_minutes", "minutes": 10},
             "action": [{"room": "living_room", "device": "ac", "set": {"on": False}},
                        {"room": "aditya_room", "device": "ac", "set": {"on": False}},
                        {"room": "diksha_room", "device": "ac", "set": {"on": False}},
                        {"room": "living_room", "device": "fan", "set": {"on": False}},
                        {"room": "aditya_room", "device": "fan", "set": {"on": False}},
                        {"room": "living_room", "device": "tv", "set": {"on": False}}],
             "cooldown_seconds": 600},
            {"name": "[Vacation] Evening random light", "description": "Part of Vacation Mode bundle — gives impression someone is home.",
             "condition": {"type": "time_of_day", "hour": 20, "minute": 0, "window_minutes": 3},
             "action": {"room": "living_room", "device": "light", "set": {"on": True, "brightness": 60}},
             "cooldown_seconds": 82800},
            {"name": "[Vacation] Night lights off", "description": "Part of Vacation Mode bundle.",
             "condition": {"type": "time_of_day", "hour": 23, "minute": 30, "window_minutes": 3},
             "action": {"room": "living_room", "device": "light", "set": {"on": False}},
             "cooldown_seconds": 82800},
        ],
    },
    {
        "id": "energy_saver", "name": "Energy Saver", "icon": "🌿", "color": "#22c55e",
        "description": "Aggressive power reduction — dims lights, raises AC setpoint, cuts standby loads.",
        "rules": [
            {"name": "[EnergySaver] Cap light brightness at 50%", "description": "Part of Energy Saver bundle.",
             "condition": {"type": "time_of_day", "hour": 0, "minute": 0, "window_minutes": 2},
             "action": [{"room": r, "device": "light", "set": {"brightness": 50}}
                        for r in ["living_room","aditya_room","diksha_room","agrim_room","naman_room","kamakshi_room","kitchen"]],
             "cooldown_seconds": 82800},
            {"name": "[EnergySaver] Raise AC to 26 °C", "description": "Part of Energy Saver bundle — each degree higher saves ~6% energy.",
             "condition": {"type": "sensor_above", "key": "temperature", "threshold": 28},
             "action": [{"room": "living_room", "device": "ac", "set": {"on": True, "temp": 26}},
                        {"room": "aditya_room", "device": "ac", "set": {"on": True, "temp": 26}},
                        {"room": "diksha_room", "device": "ac", "set": {"on": True, "temp": 26}}],
             "cooldown_seconds": 1800},
            {"name": "[EnergySaver] Nobody home 5 min → all off", "description": "Part of Energy Saver bundle — faster than the default 30-min rule.",
             "condition": {"type": "nobody_home_minutes", "minutes": 5},
             "action": [{"room": r, "device": "light", "set": {"on": False}}
                        for r in ["living_room","aditya_room","diksha_room","agrim_room","naman_room","kamakshi_room","kitchen"]]
                       + [{"room": "living_room", "device": "tv", "set": {"on": False}},
                          {"room": "kitchen", "device": "exhaust", "set": {"on": False}}],
             "cooldown_seconds": 300},
        ],
    },
    {
        "id": "night_mode", "name": "Night Mode", "icon": "🌙", "color": "#818cf8",
        "description": "Wind down automatically — dims lights, cuts noise, preps the house for sleep.",
        "rules": [
            {"name": "[Night] 10 PM → living areas dim", "description": "Part of Night Mode bundle.",
             "condition": {"type": "time_of_day", "hour": 22, "minute": 0, "window_minutes": 3},
             "action": [{"room": "living_room", "device": "light", "set": {"on": True, "brightness": 20}},
                        {"room": "kitchen", "device": "light", "set": {"on": True, "brightness": 20}},
                        {"room": "living_room", "device": "tv", "set": {"on": False}},
                        {"room": "kitchen", "device": "exhaust", "set": {"on": False}}],
             "cooldown_seconds": 82800},
            {"name": "[Night] 11 PM → bedroom fans on low", "description": "Part of Night Mode bundle — quiet airflow for sleep.",
             "condition": {"type": "time_of_day", "hour": 23, "minute": 0, "window_minutes": 3},
             "action": [{"room": r, "device": "fan", "set": {"on": True, "speed": 1}}
                        for r in ["aditya_room","diksha_room","agrim_room","naman_room","kamakshi_room"]],
             "cooldown_seconds": 82800},
            {"name": "[Night] Midnight → all lights off", "description": "Part of Night Mode bundle.",
             "condition": {"type": "time_of_day", "hour": 0, "minute": 0, "window_minutes": 3},
             "action": [{"room": r, "device": "light", "set": {"on": False}}
                        for r in ["living_room","aditya_room","diksha_room","agrim_room","naman_room","kamakshi_room","kitchen"]],
             "cooldown_seconds": 82800},
        ],
    },
    {
        "id": "party_mode", "name": "Party Mode", "icon": "🎉", "color": "#f59e0b",
        "description": "Full brightness, fan on high, keep things lively — overrides energy-saving rules.",
        "rules": [
            {"name": "[Party] Full brightness living room", "description": "Part of Party Mode bundle.",
             "condition": {"type": "time_of_day", "hour": 18, "minute": 0, "window_minutes": 3},
             "action": [{"room": "living_room", "device": "light", "set": {"on": True, "brightness": 100}},
                        {"room": "living_room", "device": "fan", "set": {"on": True, "speed": 3}},
                        {"room": "kitchen", "device": "light", "set": {"on": True, "brightness": 100}},
                        {"room": "kitchen", "device": "exhaust", "set": {"on": True}}],
             "cooldown_seconds": 82800},
            {"name": "[Party] AC comfort during party", "description": "Part of Party Mode bundle — more people = more heat.",
             "condition": {"type": "sensor_above", "key": "temperature", "threshold": 26},
             "action": [{"room": "living_room", "device": "ac", "set": {"on": True, "temp": 22, "mode": "cool"}}],
             "cooldown_seconds": 900},
            {"name": "[Party] High CO₂ → exhaust + window", "description": "Part of Party Mode bundle — more people = more CO₂.",
             "condition": {"type": "sensor_above", "key": "co2_ppm", "threshold": 900},
             "action": [{"room": "kitchen", "device": "exhaust", "set": {"on": True}},
                        {"room": "living_room", "device": "window", "set": {"on": True}}],
             "cooldown_seconds": 600},
        ],
    },
    {
        "id": "study_mode", "name": "Study Mode", "icon": "📚", "color": "#22d3ee",
        "description": "Optimal conditions for focus — bright white light, cool temperature, CO₂ management.",
        "rules": [
            {"name": "[Study] Full brightness study areas", "description": "Part of Study Mode bundle.",
             "condition": {"type": "time_of_day", "hour": 8, "minute": 0, "window_minutes": 3},
             "action": [{"room": r, "device": "light", "set": {"on": True, "brightness": 100}}
                        for r in ["aditya_room","agrim_room","naman_room","kamakshi_room"]],
             "cooldown_seconds": 82800},
            {"name": "[Study] Cool AC for focus", "description": "Part of Study Mode bundle — 22°C is optimal for concentration.",
             "condition": {"type": "sensor_above", "key": "temperature", "threshold": 24},
             "action": [{"room": "aditya_room", "device": "ac", "set": {"on": True, "temp": 22, "mode": "cool"}}],
             "cooldown_seconds": 1200},
            {"name": "[Study] CO₂ check → ventilate", "description": "Part of Study Mode bundle — high CO₂ reduces alertness.",
             "condition": {"type": "sensor_above", "key": "co2_ppm", "threshold": 850},
             "action": [{"room": "aditya_room", "device": "window", "set": {"on": True}},
                        {"room": "agrim_room", "device": "window", "set": {"on": True}}],
             "cooldown_seconds": 600},
        ],
    },
    {
        "id": "morning_routine", "name": "Morning Routine", "icon": "🌅", "color": "#fb923c",
        "description": "Automated sunrise — gradual brightening, fresh air, AC off for the day.",
        "rules": [
            {"name": "[Morning] 6 AM → kitchen on full", "description": "Part of Morning Routine bundle.",
             "condition": {"type": "time_of_day", "hour": 6, "minute": 0, "window_minutes": 3},
             "action": [{"room": "kitchen", "device": "light", "set": {"on": True, "brightness": 100}},
                        {"room": "living_room", "device": "light", "set": {"on": True, "brightness": 70}}],
             "cooldown_seconds": 82800},
            {"name": "[Morning] 6:30 AM → bedrooms brighten", "description": "Part of Morning Routine bundle — gentle wake-up.",
             "condition": {"type": "time_of_day", "hour": 6, "minute": 30, "window_minutes": 3},
             "action": [{"room": r, "device": "light", "set": {"on": True, "brightness": 80}}
                        for r in ["aditya_room","diksha_room","agrim_room","naman_room","kamakshi_room"]],
             "cooldown_seconds": 82800},
            {"name": "[Morning] 7 AM → AC off, fans off, open windows", "description": "Part of Morning Routine bundle — fresh morning air.",
             "condition": {"type": "time_of_day", "hour": 7, "minute": 0, "window_minutes": 3},
             "action": [{"room": "living_room", "device": "ac", "set": {"on": False}},
                        {"room": "aditya_room", "device": "ac", "set": {"on": False}},
                        {"room": "diksha_room", "device": "ac", "set": {"on": False}},
                        {"room": "living_room", "device": "fan", "set": {"on": False}},
                        {"room": "living_room", "device": "window", "set": {"on": True}},
                        {"room": "aditya_room", "device": "window", "set": {"on": True}}],
             "cooldown_seconds": 82800},
        ],
    },
]

@app.get("/api/automation/templates")
@limiter.limit("30/minute")
async def list_templates(request: Request, user: dict = Depends(get_current_user)):
    existing_rules = {r["name"] for r in db.get_automation_rules()}
    result = []
    for bundle in _TEMPLATE_BUNDLES:
        installed_count = sum(1 for r in bundle["rules"] if r["name"] in existing_rules)
        result.append({
            "id": bundle["id"], "name": bundle["name"], "icon": bundle["icon"],
            "description": bundle["description"], "color": bundle["color"],
            "rule_count": len(bundle["rules"]),
            "installed": installed_count == len(bundle["rules"]),
            "partial": 0 < installed_count < len(bundle["rules"]),
        })
    return result

@app.post("/api/automation/templates/{bundle_id}/enable")
@limiter.limit("10/minute")
async def enable_template(bundle_id: str, request: Request, user: dict = Depends(get_current_user)):
    bundle = next((b for b in _TEMPLATE_BUNDLES if b["id"] == bundle_id), None)
    if not bundle:
        raise HTTPException(status_code=404, detail="Template not found")
    existing_names = {r["name"] for r in db.get_automation_rules()}
    created = []
    for rule in bundle["rules"]:
        if rule["name"] in existing_names:
            continue
        db.create_automation_rule(
            name=rule["name"], description=rule.get("description"),
            condition=rule["condition"], action=rule["action"],
            enabled=True, cooldown_seconds=rule.get("cooldown_seconds", 300),
        )
        created.append(rule["name"])
    db.add_audit_entry(user["username"], "template_enabled",
                       detail=f"{bundle['name']}: {len(created)} rules created", ip_address=_client_ip(request))
    return {"ok": True, "created": created, "skipped": len(bundle["rules"]) - len(created)}

@app.post("/api/automation/templates/{bundle_id}/disable")
@limiter.limit("10/minute")
async def disable_template(bundle_id: str, request: Request, user: dict = Depends(get_current_user)):
    bundle = next((b for b in _TEMPLATE_BUNDLES if b["id"] == bundle_id), None)
    if not bundle:
        raise HTTPException(status_code=404, detail="Template not found")
    bundle_names = {r["name"] for r in bundle["rules"]}
    removed = []
    for rule in db.get_automation_rules():
        if rule["name"] in bundle_names:
            db.delete_automation_rule(rule["id"])
            removed.append(rule["name"])
    db.add_audit_entry(user["username"], "template_disabled",
                       detail=f"{bundle['name']}: {len(removed)} rules removed", ip_address=_client_ip(request))
    return {"ok": True, "removed": removed}


# ── Routines ───────────────────────────────────────────────────────────────

class RoutineCreate(BaseModel):
    name: str
    hour: int                        # 0-23
    minute: int = 0                  # 0-59
    days: str = "everyday"           # "everyday" or "monday,wednesday,friday" etc.
    room: Optional[str] = None       # if None, defaults to the member's own room
    device: str = "light"
    action: dict                     # e.g. {"on": False} or {"on": True, "brightness": 50}
    member_id: Optional[int] = None  # owner can create for any member; others only for themselves


class ScheduledGuestCreate(BaseModel):
    name: str
    role: str = "guest"      # "guest" | "maid" | "worker" | "delivery" etc.
    days: str = "everyday"   # "everyday" or comma-separated day names
    start_hour: int = 0
    start_min: int = 0
    end_hour: int = 23
    end_min: int = 59
    notes: Optional[str] = None


def _check_guest_access(guest: dict) -> tuple[bool, str]:
    """
    Checks whether a scheduled guest's current visit is within their
    allowed time window and day. Returns (is_authorized, reason_string).
    """
    from datetime import datetime as _dt
    now = _dt.now()
    day_names = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
    today = day_names[now.weekday()]

    allowed_days = guest.get("days", "everyday")
    if allowed_days != "everyday":
        allowed = [d.strip().lower() for d in allowed_days.split(",")]
        if today not in allowed:
            return False, f"not allowed on {today.capitalize()}s"

    now_mins = now.hour * 60 + now.minute
    start_mins = guest["start_hour"] * 60 + guest["start_min"]
    end_mins   = guest["end_hour"]   * 60 + guest["end_min"]

    if not (start_mins <= now_mins <= end_mins):
        return False, (
            f"outside allowed hours "
            f"({guest['start_hour']:02d}:{guest['start_min']:02d}–"
            f"{guest['end_hour']:02d}:{guest['end_min']:02d})"
        )

    return True, "within allowed schedule"


@app.get("/api/routines")
@limiter.limit("30/minute")
async def list_routines(request: Request, member_id: Optional[int] = None, user: dict = Depends(get_current_user)):
    # Non-owners can only see their own routines
    if user["role"] != "owner" and member_id != user["member_id"]:
        member_id = user["member_id"]
    return db.get_routines(member_id=member_id)


@app.post("/api/routines")
@limiter.limit("10/minute")
async def create_routine_endpoint(body: RoutineCreate, request: Request, user: dict = Depends(get_current_user)):
    # Determine which member this routine belongs to
    target_member_id = body.member_id or user["member_id"]
    if user["role"] != "owner" and target_member_id != user["member_id"]:
        raise HTTPException(status_code=403, detail="You can only create routines for yourself")

    # Find the member's name and default room
    member = next((m for m in family_members if m["id"] == target_member_id), None)
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Resolve room: use provided room or fall back to member's own room
    MEMBER_ROOM_MAP = {
        "Aditya": "aditya_room", "Diksha": "diksha_room",
        "Agrim": "agrim_room", "Naman": "naman_room", "Kamakshi": "kamakshi_room"
    }
    room = body.room or MEMBER_ROOM_MAP.get(member["name"], "living_room")

    # Validate device exists in target room
    if room not in devices or body.device not in devices[room]:
        raise HTTPException(status_code=400, detail=f"{room}.{body.device} is not a real device")

    # Validate time
    if not (0 <= body.hour <= 23 and 0 <= body.minute <= 59):
        raise HTTPException(status_code=400, detail="Invalid time — hour must be 0-23, minute 0-59")

    routine = db.create_routine(
        member_id=target_member_id,
        member_name=member["name"],
        name=body.name,
        hour=body.hour,
        minute=body.minute,
        days=body.days,
        room=room,
        device=body.device,
        action=body.action,
    )
    db.add_audit_entry(user["username"], "routine_created",
                       detail=f"{member['name']}: {body.name} at {body.hour:02d}:{body.minute:02d}",
                       ip_address=_client_ip(request))
    return routine


@app.post("/api/routines/{routine_id}/toggle")
@limiter.limit("10/minute")
async def toggle_routine(routine_id: int, request: Request, user: dict = Depends(get_current_user)):
    routine = db.get_routine(routine_id)
    if not routine:
        raise HTTPException(status_code=404, detail="Routine not found")
    if user["role"] != "owner" and routine["member_id"] != user["member_id"]:
        raise HTTPException(status_code=403, detail="You can only edit your own routines")
    db.update_routine_enabled(routine_id, not routine["enabled"])
    db.add_audit_entry(user["username"], "routine_toggled", detail=routine["name"], ip_address=_client_ip(request))
    return db.get_routine(routine_id)


@app.delete("/api/routines/{routine_id}")
@limiter.limit("10/minute")
async def delete_routine(routine_id: int, request: Request, user: dict = Depends(get_current_user)):
    routine = db.get_routine(routine_id)
    if not routine:
        raise HTTPException(status_code=404, detail="Routine not found")
    if user["role"] != "owner" and routine["member_id"] != user["member_id"]:
        raise HTTPException(status_code=403, detail="You can only delete your own routines")
    db.delete_routine(routine_id)
    db.add_audit_entry(user["username"], "routine_deleted", detail=routine["name"], ip_address=_client_ip(request))
    return {"ok": True}


# ── Scheduled guests ──────────────────────────────────────────────────────

@app.get("/api/guests")
@limiter.limit("30/minute")
async def list_scheduled_guests(request: Request, user: dict = Depends(get_current_user)):
    return db.get_scheduled_guests()


@app.post("/api/guests")
@limiter.limit("10/minute")
async def create_scheduled_guest(body: ScheduledGuestCreate, request: Request, user: dict = Depends(get_current_user)):
    if not (0 <= body.start_hour <= 23 and 0 <= body.start_min <= 59 and
            0 <= body.end_hour <= 23 and 0 <= body.end_min <= 59):
        raise HTTPException(status_code=400, detail="Invalid time values")
    if body.start_hour * 60 + body.start_min >= body.end_hour * 60 + body.end_min:
        raise HTTPException(status_code=400, detail="Start time must be before end time")
    guest = db.create_scheduled_guest(
        name=body.name, role=body.role, days=body.days,
        start_hour=body.start_hour, start_min=body.start_min,
        end_hour=body.end_hour, end_min=body.end_min, notes=body.notes
    )
    db.add_audit_entry(user["username"], "guest_schedule_added",
                       detail=f"{body.name} ({body.role})", ip_address=_client_ip(request))
    return guest


@app.post("/api/guests/{guest_id}/toggle")
@limiter.limit("10/minute")
async def toggle_scheduled_guest(guest_id: int, request: Request, user: dict = Depends(get_current_user)):
    guest = db.get_scheduled_guest(guest_id)
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found")
    db.update_scheduled_guest_enabled(guest_id, not guest["enabled"])
    db.add_audit_entry(user["username"], "guest_schedule_toggled",
                       detail=guest["name"], ip_address=_client_ip(request))
    return db.get_scheduled_guest(guest_id)


@app.delete("/api/guests/{guest_id}")
@limiter.limit("10/minute")
async def delete_scheduled_guest(guest_id: int, request: Request, user: dict = Depends(get_current_user)):
    guest = db.get_scheduled_guest(guest_id)
    if not guest:
        raise HTTPException(status_code=404, detail="Guest not found")
    db.delete_scheduled_guest(guest_id)
    db.add_audit_entry(user["username"], "guest_schedule_removed",
                       detail=guest["name"], ip_address=_client_ip(request))
    return {"ok": True}


@app.post("/api/security/guest-detected")
@limiter.limit("10/minute")
async def log_guest_detected(body: dict, request: Request):
    """
    Called when a face is recognized but doesn't match any family member.
    Checks if the person has a scheduled access entry and whether the
    current time/day falls within their allowed window.
    Returns the access decision so the frontend can react appropriately.
    """
    name = body.get("name", "Unknown")
    guest = db.get_scheduled_guest_by_name(name)

    if not guest:
        # No schedule found — treat as a regular unrecognized intruder
        entry = db.add_security_log(
            person=name, type_="intruder",
            event="unrecognized face — not in guest schedule",
            time_str=datetime.now().strftime("%H:%M"),
            date_str=datetime.now().strftime("%d %b"),
            status="unauthorized"
        )
        security_logs.insert(0, entry)
        return {"access": "denied", "reason": "not in guest schedule", "log": entry}

    authorized, reason = _check_guest_access(guest)

    if authorized:
        entry = db.add_security_log(
            person=name, type_="guest",
            event=f"{guest['role']} — scheduled access ({reason})",
            time_str=datetime.now().strftime("%H:%M"),
            date_str=datetime.now().strftime("%d %b"),
            status="authorized"
        )
        security_logs.insert(0, entry)
        return {"access": "granted", "reason": reason, "log": entry}
    else:
        # Person is in the schedule but visiting at the wrong time/day
        entry = db.add_security_log(
            person=name, type_="intruder",
            event=f"{guest['role']} — access OUTSIDE allowed schedule ({reason})",
            time_str=datetime.now().strftime("%H:%M"),
            date_str=datetime.now().strftime("%d %b"),
            status="restricted"
        )
        security_logs.insert(0, entry)
        logger.warning("Scheduled guest %s attempted access outside schedule: %s", name, reason)
        return {"access": "denied", "reason": reason, "log": entry}


# ── Payments ──────────────────────────────────────────────────────────────

class RentConfigUpdate(BaseModel):
    total_rent: float
    due_day: int = 1       # day of month rent is due (1-28)
    auto_pay: bool = False
    notes: Optional[str] = None

class MarkPaymentRequest(BaseModel):
    status: str            # "paid" | "pending" | "waived"
    payment_method: Optional[str] = None   # "cash" | "upi" | "bank_transfer" | "other"
    notes: Optional[str] = None


@app.get("/api/payments/config")
@limiter.limit("30/minute")
async def get_payment_config(request: Request, user: dict = Depends(get_current_user)):
    return db.get_rent_config()


@app.post("/api/payments/config")
@limiter.limit("10/minute")
async def update_payment_config(body: RentConfigUpdate, request: Request, user: dict = Depends(get_current_user)):
    if user["role"] != "owner":
        raise HTTPException(status_code=403, detail="Only the owner can update rent configuration")
    if body.total_rent < 0:
        raise HTTPException(status_code=400, detail="Rent cannot be negative")
    if not (1 <= body.due_day <= 28):
        raise HTTPException(status_code=400, detail="Due day must be between 1 and 28")
    config = db.upsert_rent_config(body.total_rent, body.due_day, body.auto_pay, body.notes)
    db.add_audit_entry(user["username"], "rent_config_updated",
                       detail=f"total=₹{body.total_rent} due_day={body.due_day}",
                       ip_address=_client_ip(request))
    return config


@app.get("/api/payments")
@limiter.limit("30/minute")
async def get_payments(request: Request, month: Optional[str] = None, user: dict = Depends(get_current_user)):
    """
    Returns payments for a given month (YYYY-MM format).
    If no month given, defaults to the current month.
    Auto-creates payment records for all members if none exist yet.
    """
    if not month:
        month = datetime.now().strftime("%Y-%m")

    config = db.get_rent_config()
    total = config["total_rent"]
    members = [m for m in family_members]  # all members pay rent
    n = len(members)
    per_share = round(total / n, 2) if n > 0 and total > 0 else 0

    payments = db.get_or_create_monthly_payments(month, members, per_share)
    paid_count = sum(1 for p in payments if p["status"] == "paid")
    total_collected = sum(p["amount"] for p in payments if p["status"] == "paid")

    return {
        "month": month,
        "total_rent": total,
        "per_share": per_share,
        "due_day": config["due_day"],
        "auto_pay": bool(config["auto_pay"]),
        "payments": payments,
        "paid_count": paid_count,
        "pending_count": len(payments) - paid_count,
        "total_collected": round(total_collected, 2),
        "total_outstanding": round(total - total_collected, 2),
    }


@app.post("/api/payments/{payment_id}/mark")
@limiter.limit("10/minute")
async def mark_payment(payment_id: int, body: MarkPaymentRequest,
                       request: Request, user: dict = Depends(get_current_user)):
    payment = db.get_payment(payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment record not found")
    # Members can only mark their own payment; owner can mark anyone's
    if user["role"] != "owner" and payment["member_id"] != user["member_id"]:
        raise HTTPException(status_code=403, detail="You can only update your own payment")
    if body.status not in ("paid", "pending", "waived"):
        raise HTTPException(status_code=400, detail="Status must be paid, pending, or waived")
    updated = db.mark_payment(
        payment_id, body.status, body.payment_method, body.notes,
        recorded_by=user["username"]
    )
    db.add_audit_entry(user["username"], "payment_marked",
                       detail=f"{payment['member_name']} {payment['month']} → {body.status}",
                       ip_address=_client_ip(request))
    return updated


# Room-to-member mapping — mirrors the frontend MEMBER_ROOM constant.
# Any room not listed is treated as shared and split equally among all members.
_ROOM_OWNER: dict[str, str] = {
    "aditya_room":   "Aditya",
    "diksha_room":   "Diksha",
    "agrim_room":    "Agrim",
    "naman_room":    "Naman",
    "kamakshi_room": "Kamakshi",
    "shreyas_room":  "Shreyas",
}


@app.get("/api/payments/electricity")
@limiter.limit("30/minute")
async def get_electricity_split(request: Request, user: dict = Depends(get_current_user)):
    """
    Calculate each member's share of the projected monthly electricity bill.
    Approach:
      1. Compute per-room energy usage (units/kWh, same 8-hr/day × 30 days projection).
      2. Personal rooms → charged to their owner.
         Shared rooms (living_room, kitchen, bathroom, security) → split equally.
      3. Apply DHBVN slab tariff to the household total to get the monetary bill,
         then assign each member a proportional cost share.
    """
    with state_lock:
        watts = calculate_energy()
        # Per-room watt calculation (same logic as /api/energy)
        room_watts: dict[str, int] = {}
        for room, devs in devices.items():
            rw = 0
            for dev_name, dev in devs.items():
                if dev.get("on", False):
                    w = dev.get("watts", 0)
                    if dev_name == "fan":
                        speed = dev.get("speed", 0)
                        w = int(w * (speed / 5)) if speed > 0 else 0
                    elif dev_name == "light":
                        w = int(w * (dev.get("brightness", 100) / 100))
                    rw += w
            room_watts[room] = rw

    # Convert watts → projected monthly units (kWh)
    room_units = {room: round(w * 8 * 30 / 1000, 2) for room, w in room_watts.items()}
    total_units = round(sum(room_units.values()), 2)

    # Bill for the full household
    bill = calculate_dhbvn_bill(total_units)
    total_cost = bill["total"]

    # Build member usage map
    members = [m["name"] for m in family_members]
    n = len(members) or 1
    member_units: dict[str, float] = {name: 0.0 for name in members}
    member_room_units: dict[str, float] = {name: 0.0 for name in members}
    member_shared_units: dict[str, float] = {name: 0.0 for name in members}

    shared_total = 0.0
    for room, units in room_units.items():
        owner = _ROOM_OWNER.get(room)
        if owner and owner in member_units:
            member_units[owner] += units
            member_room_units[owner] += units
        else:
            # Shared room — split equally
            share = round(units / n, 3)
            shared_total += units
            for name in members:
                member_units[name] += share
                member_shared_units[name] += share

    shared_per_member = round(shared_total / n, 2) if n else 0

    # Proportional cost split
    per_member = []
    for name in members:
        u = round(member_units[name], 2)
        pct = round((u / total_units * 100) if total_units else 0, 1)
        cost = round((u / total_units * total_cost) if total_units else 0, 2)
        fm = next((m for m in family_members if m["name"] == name), {})
        per_member.append({
            "name": name,
            "member_id": fm.get("id"),
            "avatar": fm.get("avatar", name[0]),
            "color": fm.get("color", "#6c8bef"),
            "units": u,
            "room_units": round(member_room_units[name], 2),
            "shared_units": round(member_shared_units[name], 2),
            "percentage": pct,
            "cost": cost,
        })

    # Sort by units descending (heaviest user first)
    per_member.sort(key=lambda x: x["units"], reverse=True)

    return {
        "total_units": total_units,
        "total_cost": total_cost,
        "bill_breakdown": bill["breakdown"],
        "shared_units_per_member": shared_per_member,
        "per_member": per_member,
        "projection_basis": "current draw × 8 hrs/day × 30 days",
    }


@app.get("/")
@limiter.limit("30/minute")
async def root(request: Request):
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))

@app.get("/api/status")
@limiter.limit("60/minute")
async def get_status(request: Request):
    with state_lock:
        return {
            "sensors": sensors.copy(),
            "devices": devices.copy(),
            "energy_watts": calculate_energy(),
            "energy_kwh_today": round(calculate_energy() * 8 / 1000, 2),
            "timestamp": datetime.now().isoformat()
        }

@app.get("/api/sensors")
@limiter.limit("60/minute")
async def get_sensors(request: Request):
    with state_lock:
        return sensors.copy()

@app.get("/api/climate/history")
@limiter.limit("60/minute")
async def get_climate_history(request: Request, metric: str = "temperature", range_: str = Query("1h", alias="range")):
    """History for the dashboard range-filter charts. Each range returns a
    different time window AND point density, so 6H visibly spans more real
    time / more points than 1H."""
    windows   = {"1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600, "7d": 7 * 86400, "30d": 30 * 86400}
    densities = {"1h": 60,   "6h": 72,       "24h": 96,        "7d": 168,       "30d": 180}
    window = windows.get(range_, 3600)
    target_pts = densities.get(range_, 60)
    idx = 2 if str(metric).lower().startswith("hum") else 1
    now = time.time()
    cutoff = now - window
    with state_lock:
        rows = [r for r in climate_history if r[0] >= cutoff]
    if len(rows) > target_pts:
        step = len(rows) / target_pts
        rows = [rows[int(i * step)] for i in range(target_pts)] + [rows[-1]]
    return {
        "metric": metric,
        "range": range_,
        "points": len(rows),
        "window_seconds": window,
        "data": [round(r[idx], 1) for r in rows],
        "timestamps": [datetime.fromtimestamp(r[0]).isoformat() for r in rows],
    }

@app.get("/api/devices")
@limiter.limit("30/minute")
async def get_devices(request: Request):
    with state_lock:
        return devices.copy()

@app.post("/api/device/toggle")
@limiter.limit("20/minute")
async def toggle_device(body: DeviceToggle, request: Request, user: Optional[dict] = Depends(get_current_user_optional)):
    with state_lock:
        if body.room not in devices:
            raise HTTPException(status_code=404, detail="Room not found")
        if body.device not in devices[body.room]:
            raise HTTPException(status_code=404, detail="Device not found")
        dev = devices[body.room][body.device]
        if isinstance(body.value, dict):
            dev.update(body.value)
        else:
            dev["on"] = body.value
        db.save_device_state(body.room, body.device, dev)
        actor = user["username"] if user else "anonymous"
        db.add_audit_entry(actor, "device_toggle", detail=f"{body.room}.{body.device} -> {body.value}", ip_address=_client_ip(request))
        # Re-check rules immediately instead of waiting for the next tick —
        # device_state rules (e.g. AC + window) react right away. Safe to
        # call often: each rule's cooldown makes repeat calls a no-op.
        try:
            automation.evaluate_rules(devices, sensors, family_members, state_lock)
        except Exception as e:
            logger.error("Immediate automation re-check failed: %s", e, exc_info=True)
        return {"ok": True, "device": dev, "energy_watts": calculate_energy()}

@app.get("/api/family")
@limiter.limit("30/minute")
async def get_family(request: Request):
    return family_members

@app.post("/api/family/add")
@limiter.limit("10/minute")
async def add_family(body: MemberAdd, request: Request, user: Optional[dict] = Depends(get_current_user_optional)):
    colors = ["#4f46e5","#7c3aed","#0891b2","#059669","#dc2626","#d97706","#be185d"]
    avatar = body.name[:2].upper()
    color = colors[len(family_members) % len(colors)]
    member = db.add_family_member(name=body.name, role=body.role, status="away", avatar=avatar, color=color)
    family_members.append(member)
    db.add_audit_entry(user["username"] if user else "anonymous", "family_member_added", detail=body.name, ip_address=_client_ip(request))
    return member

@app.delete("/api/family/{member_id}")
@limiter.limit("10/minute")
async def delete_member(member_id: int, request: Request, user: Optional[dict] = Depends(get_current_user_optional)):
    global family_members
    removed = next((m for m in family_members if m["id"] == member_id), None)
    db.delete_family_member(member_id)
    family_members = [m for m in family_members if m["id"] != member_id]
    db.add_audit_entry(user["username"] if user else "anonymous", "family_member_removed", detail=removed["name"] if removed else str(member_id), ip_address=_client_ip(request))
    return {"ok": True}

@app.get("/api/security/logs")
@limiter.limit("60/minute")
async def get_security_logs(request: Request):
    return db.get_security_logs()

@app.post("/api/security/logs")
@limiter.limit("10/minute")
async def add_security_log(body: LogAdd, request: Request):
    entry = db.add_security_log(
        person=body.person,
        type_=body.type,
        event=body.event,
        time_str=datetime.now().strftime("%H:%M"),
        date_str=datetime.now().strftime("%d %b"),
        status=body.status,
        estimated=body.estimated,
    )
    security_logs.insert(0, entry)
    return entry

@app.post("/api/security/intruder")
@limiter.limit("10/minute")
async def log_intruder(request: Request):
    entry = db.add_security_log(
        person="Unknown Person",
        type_="intruder",
        event="unrecognized face detected at front door",
        time_str=datetime.now().strftime("%H:%M"),
        date_str=datetime.now().strftime("%d %b"),
        status="unauthorized",
    )
    security_logs.insert(0, entry)
    # Automated lockdown — runs under state_lock (RLock, safe to re-enter)
    with state_lock:
        try:
            changes = alert_responses.respond_to_intruder(devices)
            entry["auto_actions"] = changes
        except Exception as e:
            logger.error("intruder response error: %s", e, exc_info=True)
    return entry

@app.post("/api/security/member-detected")
@limiter.limit("10/minute")
async def log_member_detected(body: dict, request: Request):
    name = body.get("name", "Unknown")
    entry = db.add_security_log(
        person=name,
        type_="member",
        event="face recognized — arrived home",
        time_str=datetime.now().strftime("%H:%M"),
        date_str=datetime.now().strftime("%d %b"),
        status="authorized",
    )
    security_logs.insert(0, entry)
    # Update member status (in-memory + persisted)
    db.update_member_status(name, "home")
    for m in family_members:
        if m["name"].lower() == name.lower():
            m["status"] = "home"
    return entry

@app.post("/api/simulate/alert")
@limiter.limit("10/minute")
async def simulate_alert(body: dict, request: Request):
    """Lets the frontend Simulations tab trigger a real alert pipeline
    (including auto-responses) without waiting for the sensor tick loop."""
    alert_type = body.get("type", "").upper()
    if alert_type not in ("SMOKE", "GAS", "FIRE", "INTRUDER"):
        raise HTTPException(status_code=400, detail="Unknown alert type")
    with state_lock:
        if alert_type == "INTRUDER":
            changes = alert_responses.respond_to_intruder(devices)
        else:
            changes = alert_responses.respond_to_environmental_alert(alert_type, devices)
    alert_entry = {
        "type": alert_type.lower(), "level": "CRITICAL",
        "message": f"[SIM] {alert_type} alert triggered",
        "time": datetime.now().strftime("%H:%M:%S"),
        "auto_actions": changes,
    }
    alert_history.append(alert_entry)
    if len(alert_history) > 50:
        alert_history.pop(0)
    return {"ok": True, "changes": changes}

@app.post("/api/emergency/reset")
@limiter.limit("10/minute")
async def emergency_reset(request: Request):
    """Manual all-clear: turns off sprinklers + siren, closes windows,
    restores mains power and re-locks doors."""
    with state_lock:
        changes = alert_responses.reset_to_normal(devices)
    return {"ok": True, "changes": changes}

@app.get("/api/emergency/status")
async def emergency_status():
    """Live state of all emergency-related devices for the UI banner."""
    with state_lock:
        sec = devices.get("security", {})
        sprinklers_on = any(devs.get("sprinkler", {}).get("on", False) for devs in devices.values())
        windows_open = any(devs.get("window", {}).get("on", False) for devs in devices.values())
        return {
            "siren": sec.get("siren", {}).get("on", False),
            "door_locked": sec.get("door_lock", {}).get("on", True),
            "mains_on": sec.get("mains_power", {}).get("on", True),
            "sprinklers_on": sprinklers_on,
            "windows_open": windows_open,
        }

# ── AQI-MONITOR integration: LSTM forecast ──────────────────────────────────
# Optional — install AQI-MONITOR/requirements.txt (torch + scikit-learn) to
# enable. Degrades gracefully with a clear message otherwise; never crashes
# the main app.
_aqi_predict_fn = None
_aqi_predict_load_failed = False
_last_aqi_forecast_fetch = [0.0]  # mutable single-element list so the tick
                                   # loop closure can update it without `global`
_aqi_forecast_cache = {"forecast": None, "at": 0.0}  # avoid recomputing on
                                                        # every /api/aqi/forecast hit

def _get_aqi_predictor():
    global _aqi_predict_fn, _aqi_predict_load_failed
    if _aqi_predict_fn is not None or _aqi_predict_load_failed:
        return _aqi_predict_fn
    try:
        import sys as _sys
        ml_dir = os.path.join(os.path.dirname(__file__), "AQI-MONITOR", "ml")
        if ml_dir not in _sys.path:
            _sys.path.insert(0, ml_dir)
        from predict import predict_next_7_days
        _aqi_predict_fn = predict_next_7_days
        logger.info("AQI-MONITOR LSTM forecaster loaded")
    except Exception as e:
        _aqi_predict_load_failed = True
        logger.warning("AQI forecast unavailable (%s) — install AQI-MONITOR/requirements.txt to enable", e)
    return _aqi_predict_fn


def _compute_aqi_forecast(predict_fn, current_sensors: dict):
    """Used by both /api/aqi/forecast and the hourly predictive-automation
    refresh. Returns 7 rounded AQI values or None on failure. Cached 55min
    so repeated calls don't re-run LSTM inference each time."""
    now = time.time()
    if _aqi_forecast_cache["forecast"] is not None and (now - _aqi_forecast_cache["at"]) < 3300:
        return _aqi_forecast_cache["forecast"]
    pm25 = current_sensors.get("pm25") or 90.0
    pm10 = current_sensors.get("pm10") or 140.0
    aqi  = current_sensors.get("aqi") or 150
    def synth_day(pm25_v, pm10_v, aqi_v):
        return {"pm25": pm25_v, "pm10": pm10_v,
                "no2": round(pm25_v * 0.35, 1), "co": round(pm25_v * 0.018, 2),
                "o3": round(pm10_v * 0.15, 1), "aqi": aqi_v}
    import random as _random
    history = []
    p25, p10, a = pm25, pm10, aqi
    for _ in range(7):
        history.insert(0, synth_day(round(p25,1), round(p10,1), round(a)))
        p25 = max(10, p25 + _random.uniform(-8, 8))
        p10 = max(15, p10 + _random.uniform(-10, 10))
        a   = max(10, a + _random.uniform(-15, 15))
    try:
        forecast = [round(v) for v in predict_fn(history)]
        _aqi_forecast_cache["forecast"] = forecast
        _aqi_forecast_cache["at"] = now
        return forecast
    except Exception as e:
        logger.error("AQI forecast prediction failed: %s", e, exc_info=True)
        return None


@app.get("/api/aqi/forecast")
@limiter.limit("20/minute")
async def aqi_forecast(request: Request, user: dict = Depends(get_current_user)):
    """7-day AQI forecast using the AQI-MONITOR project's trained LSTM."""
    predict_fn = _get_aqi_predictor()
    if predict_fn is None:
        return {"available": False,
                "reason": "ML dependencies not installed — run: pip install -r AQI-MONITOR/requirements.txt"}
    with state_lock:
        snapshot = dict(sensors)
    forecast = _compute_aqi_forecast(predict_fn, snapshot)
    if forecast is None:
        return {"available": False, "reason": "Prediction failed — check server logs"}
    return {"available": True, "forecast": forecast,
            "note": "Approximate — based on synthesized pollutant history, not persisted real sensor logs."}


# ── Smart Suggestions engine ─────────────────────────────────────────────────
# Scans the audit log for repeated manual device_toggle patterns (same
# room+device+state around the same hour on 3+ different days) and proposes
# turning them into automation rules.
import ast as _ast_module
import re as _re_module

def _parse_toggle_detail(detail: str):
    """Parses 'living_room.ac -> {'on': True, 'temp': 22}' into
    (room, device, value_dict). Returns None if it doesn't match the
    expected device_toggle detail format."""
    m = _re_module.match(r"^([a-z_]+)\.([a-z_]+) -> (.+)$", detail or "")
    if not m:
        return None
    room, device, value_str = m.groups()
    try:
        value = _ast_module.literal_eval(value_str)
    except Exception:
        return None
    if not isinstance(value, dict) or "on" not in value:
        return None
    return room, device, value


@app.get("/api/automation/suggestions")
@limiter.limit("15/minute")
async def automation_suggestions(request: Request, user: dict = Depends(get_current_user)):
    """Looks for repeated manual toggle habits and suggests rules for them."""
    entries = db.get_audit_log(limit=500)
    existing_rules = db.get_automation_rules()
    # Skip suggesting anything that overlaps an existing time_of_day rule for
    # the same room+device — no point suggesting what's already automated.
    already_automated = set()
    for r in existing_rules:
        cond = r.get("condition", {})
        act = r.get("action", {})
        if cond.get("type") == "time_of_day" and isinstance(act, dict):
            already_automated.add((act.get("room"), act.get("device")))

    # Bucket: (room, device, on_state, hour) -> set of distinct calendar dates
    from collections import defaultdict
    buckets = defaultdict(set)
    bucket_values = {}  # keep one example "value" dict per bucket for the action payload

    for entry in entries:
        if entry.get("action") != "device_toggle":
            continue
        parsed = _parse_toggle_detail(entry.get("detail", ""))
        if not parsed:
            continue
        room, device, value = parsed
        created_at = entry.get("created_at", "")
        if not created_at or "T" not in created_at and " " not in created_at:
            continue
        try:
            # created_at is 'YYYY-MM-DD HH:MM:SS' (SQLite datetime('now'))
            date_part, time_part = created_at.split(" ", 1) if " " in created_at else created_at.split("T", 1)
            hour = int(time_part.split(":")[0])
        except Exception:
            continue
        key = (room, device, bool(value.get("on")), hour)
        buckets[key].add(date_part)
        bucket_values[key] = value

    suggestions = []
    for (room, device, on_state, hour), dates in buckets.items():
        if len(dates) < 3:  # need to see the habit on 3+ distinct days to trust it
            continue
        if (room, device) in already_automated:
            continue
        value = bucket_values[(room, device, on_state, hour)]
        room_label = room.replace("_", " ").title()
        suggestions.append({
            "room": room, "device": device, "hour": hour, "on": on_state,
            "days_observed": len(dates),
            "title": f"{room_label} {device} — {'on' if on_state else 'off'} around {hour:02d}:00",
            "description": f"You've manually turned {device} {'on' if on_state else 'off'} in {room_label} around {hour:02d}:00 on {len(dates)} different days. Automate it?",
            "proposed_rule": {
                "name": f"[Suggested] {room_label} {device} {'on' if on_state else 'off'} at {hour:02d}:00",
                "description": f"Auto-created from a detected habit ({len(dates)} occurrences).",
                "condition": {"type": "time_of_day", "hour": hour, "minute": 0, "window_minutes": 5},
                "action": {"room": room, "device": device, "set": value},
                "cooldown_seconds": 82800,
            },
        })

    suggestions.sort(key=lambda s: -s["days_observed"])
    return suggestions[:5]


@app.get("/api/alerts")
@limiter.limit("30/minute")
async def get_alerts(request: Request):
    return alert_history[-20:]

@app.get("/api/energy")
@limiter.limit("30/minute")
async def get_energy(request: Request):
    with state_lock:
        watts = calculate_energy()
        room_breakdown_watts = {}
        for room, devs in devices.items():
            room_watts = 0
            for dev_name, dev in devs.items():
                if dev.get("on", False):
                    w = dev.get("watts", 0)
                    if dev_name == "fan":
                        speed = dev.get("speed", 0)
                        w = int(w * (speed / 5)) if speed > 0 else 0
                    elif dev_name == "light":
                        w = int(w * (dev.get("brightness", 100) / 100))
                    room_watts += w
            room_breakdown_watts[room] = room_watts

        # Units (kWh) consumed today, assuming current draw held for 8 hrs —
        # same assumption as before, just expressed in units instead of watts.
        units_today = round(watts * 8 / 1000, 2)
        # Project a full month at today's daily usage rate
        units_month_projected = round(units_today * 30, 1)

        today_bill = calculate_dhbvn_bill(units_today)
        month_bill = calculate_dhbvn_bill(units_month_projected)

        room_breakdown_units = {
            room: round(w * 8 / 1000, 3) for room, w in room_breakdown_watts.items()
        }

        return {
            "total_watts": watts,                       # kept for live "now" readouts
            "units_now_rate_per_hour": round(watts / 1000, 3),  # units/hour at current draw
            "units_today": units_today,
            "units_month_projected": units_month_projected,
            "cost_today": today_bill["total"],
            "cost_today_breakdown": today_bill,
            "cost_month_projected": month_bill["total"],
            "cost_month_breakdown": month_bill,
            "tariff": {
                "provider": "DHBVN (Dakshin Haryana Bijli Vitran Nigam)",
                "category": "Domestic — Category II (load up to 5kW)",
                "effective_from": "2025-04-01",
                "slabs": [
                    {"range": "0-150 units", "rate": 2.95},
                    {"range": "151-300 units", "rate": 5.25},
                    {"range": "301-500 units", "rate": 6.45},
                    {"range": "Above 500 units", "rate": 7.10},
                ]
            },
            "room_breakdown": room_breakdown_units,       # units (kWh) per room, today
            "room_breakdown_watts": room_breakdown_watts  # raw watts, for live device list
        }

# ──────────────────────────────────────────────
# WEBSOCKET for real-time updates
# ──────────────────────────────────────────────
# slowapi decorators only work on HTTP routes, so the WebSocket endpoint is
# limited by capping concurrent connections per client IP instead. The counter
# lives on the single asyncio event loop (check + increment happen with no
# await in between), so no lock is needed.
MAX_WS_CONNECTIONS_PER_IP = 5
ws_connections_per_ip: dict = {}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Same proxy-awareness as get_real_client_ip: behind Nginx the socket peer
    # is the proxy, which would make every household dashboard share the same
    # 5-connection budget.
    forwarded = websocket.headers.get("x-forwarded-for")
    ip = (forwarded.split(",")[0].strip() if forwarded
          else (websocket.client.host if websocket.client else "unknown"))
    if ws_connections_per_ip.get(ip, 0) >= MAX_WS_CONNECTIONS_PER_IP:
        logger.warning("WebSocket rejected — %s already has %d open connections",
                       ip, MAX_WS_CONNECTIONS_PER_IP)
        if "websocket.http.response" in websocket.scope.get("extensions", {}):
            # Deny the upgrade with a real HTTP 429 (supported by uvicorn)
            await websocket.send({
                "type": "websocket.http.response.start",
                "status": 429,
                "headers": [(b"content-type", b"application/json"),
                            (b"retry-after", b"60")],
            })
            await websocket.send({
                "type": "websocket.http.response.body",
                "body": b'{"error": "rate_limit_exceeded", "message": '
                        b'"Too many WebSocket connections - please wait a moment", '
                        b'"retry_after": 60}',
            })
        else:
            await websocket.close(code=1013)  # 1013 = try again later
        return
    ws_connections_per_ip[ip] = ws_connections_per_ip.get(ip, 0) + 1
    await websocket.accept()
    ws_clients.append(websocket)
    try:
        while True:
            with state_lock:
                data = {
                    "type": "update",
                    "sensors": sensors.copy(),
                    "energy_watts": calculate_energy(),
                    "alerts": alert_history[-5:] if alert_history else []
                }
            await websocket.send_json(data)
            await __import__('asyncio').sleep(3)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error("WebSocket connection error: %s", e, exc_info=True)
    finally:
        if websocket in ws_clients:
            ws_clients.remove(websocket)
        remaining = ws_connections_per_ip.get(ip, 1) - 1
        if remaining <= 0:
            ws_connections_per_ip.pop(ip, None)
        else:
            ws_connections_per_ip[ip] = remaining

# ──────────────────────────────────────────────
# STARTUP
# ──────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    t = threading.Thread(target=simulate_sensors, daemon=True)
    t.start()
    db.delete_expired_sessions()
    logger.info("Server started — sensor simulation thread running")
    print("\n" + "="*55)
    print("  Smart Home Dashboard — Server Started")
    print("  Open: http://localhost:8000")
    if _GENERATED_PASSWORDS_THIS_RUN:
        generated_credentials = _GENERATED_PASSWORDS_THIS_RUN
        print("=" * 55)
        print("  FIRST RUN CREDENTIALS — SAVE THESE NOW")
        print("  Shown only once, never stored in plain text")
        print("=" * 55)
        for username, password in generated_credentials:
            print(f"  {username:<12} {password}")
        print("=" * 55)

        # Also log each one individually so it appears in structured logs
        for username, password in generated_credentials:
            logger.info(f"CREDENTIAL | {username} | {password}")
    print("="*55 + "\n")

if __name__ == "__main__":
    # PORT env var lets a dev harness assign a free port when 8000 is taken
    # (e.g. by the docker compose stack). Docker itself uses the Dockerfile
    # CMD (uvicorn CLI, explicit port), so this only affects direct runs.
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
