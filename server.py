"""
Smart Home Dashboard — FastAPI Backend
Integrates: Climate Control, AQI Monitor, Smoke/Fire/Gas Detector,
            Face Recognition Security, Device Control, Energy Tracking
"""

import os
import time
import random
import threading
from datetime import datetime, timedelta
from typing import Optional, List, Any
from dataclasses import dataclass

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request, Response, Cookie, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import db
import auth
import automation
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
def determine_hvac(temperature, humidity):
    if temperature >= 35.0: return "MAX COOLING", 20.0
    elif temperature >= 28.0 and humidity > 60: return "DEHUMIDIFY & COOL", 22.0
    elif temperature >= 20.0: return "ECO MODE", 24.0
    else: return "HEATING", 24.0

# ── SHARED STATE ──
state_lock = threading.Lock()

# Device states — one room per family member + shared spaces
devices = {
    "living_room": {
        "light": {"on": True,  "brightness": 80, "watts": 12},
        "fan":   {"on": False, "speed": 0,  "watts": 45},
        "tv":    {"on": False, "watts": 120},
        "ac":    {"on": False, "temp": 24, "mode": "cool", "watts": 1500},
        "air_purifier": {"on": False, "speed": 2, "watts": 50}
    },
    "aditya_room": {
        "light": {"on": False, "brightness": 70, "watts": 10},
        "fan":   {"on": True,  "speed": 2,  "watts": 45},
        "ac":    {"on": True,  "temp": 22, "mode": "cool", "watts": 1500}
    },
    "diksha_room": {
        "light": {"on": False, "brightness": 60, "watts": 10},
        "fan":   {"on": False, "speed": 0,  "watts": 45},
        "ac":    {"on": False, "temp": 24, "mode": "cool", "watts": 1500}
    },
    "agrim_room": {
        "light": {"on": False, "brightness": 70, "watts": 10},
        "fan":   {"on": False, "speed": 0,  "watts": 45},
        "ac":    {"on": False, "temp": 24, "mode": "cool", "watts": 1500}
    },
    "naman_room": {
        "light": {"on": False, "brightness": 70, "watts": 10},
        "fan":   {"on": False, "speed": 0,  "watts": 45}
    },
    "kamakshi_room": {
        "light": {"on": True,  "brightness": 80, "watts": 10},
        "fan":   {"on": True,  "speed": 1,  "watts": 45},
        "ac":    {"on": False, "temp": 24, "mode": "cool", "watts": 1500}
    },
    "kitchen": {
        "light":   {"on": True,  "brightness": 100, "watts": 15},
        "exhaust": {"on": False, "watts": 30}
    },
    "bathroom": {
        "light":   {"on": False, "brightness": 100, "watts": 8},
        "exhaust": {"on": False, "watts": 25}
    },
    "security": {
        # Door lock as a real backend-tracked device (was previously
        # frontend-only state) so automation rules can actually act on it
        # — e.g. auto-unlock on a fire/smoke alert.
        "door_lock": {"on": True, "watts": 0}  # on = locked, off = unlocked
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
    "temperature": 28.5,
    "humidity": 62.0,
    "smoke": 3.2,
    "gas": 4.1,
    "aqi": 142,
    "aqi_category": "Moderate",
    "pm25": 85.0,
    "pm10": 120.0,
    "co2_ppm": 850.0,
    "hvac_status": "ECO MODE",
    "hvac_target": 24.0,
    "condition": "partly cloudy"
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
        temp_password = _secrets.token_urlsafe(9)  # ~12 random chars
        role = "owner" if m["role"].lower() == "owner" else "member"
        auth.create_user_account(
            username=m["name"].lower(),
            password=temp_password,
            display_name=m["name"],
            role=role,
            member_id=m["id"],
        )
        _GENERATED_PASSWORDS_THIS_RUN.append((m["name"].lower(), temp_password))

# ── Automation rules ─────────────────────────────────────────────────────
# A few real starter rules, inserted only on the very first run (empty
# table). Anyone can add/edit/disable more from the Automation page —
# these are just sensible defaults, not hardcoded behavior.
_DEFAULT_AUTOMATION_RULES = [
    {
        "name": "High AQI -> air purifier on",
        "description": "Turns on the living room air purifier at speed 3 whenever AQI rises above 200 (HERC 'Poor' threshold).",
        "condition": {"type": "sensor_above", "key": "aqi", "threshold": 200},
        "action": {"room": "living_room", "device": "air_purifier", "set": {"on": True, "speed": 3}},
        "cooldown_seconds": 600,
    },
    {
        "name": "Nobody home 30 min -> all lights off",
        "description": "If every family member has been away for 30+ minutes, turns off lights in every room to save energy.",
        "condition": {"type": "nobody_home_minutes", "minutes": 30},
        "action": {"room": "living_room", "device": "light", "set": {"on": False}},
        "cooldown_seconds": 1800,
    },
    {
        "name": "High smoke -> unlock door",
        "description": "If smoke level exceeds the 40% safety threshold, automatically unlocks the front door so it's not blocking an evacuation.",
        "condition": {"type": "sensor_above", "key": "smoke", "threshold": 40},
        "action": {"room": "security", "device": "door_lock", "set": {"on": False}},
        "cooldown_seconds": 300,
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
def simulate_sensors():
    global sensors
    base_temp = 28.5
    base_humidity = 62.0
    # AQI drifts slowly — Gurugram baseline ~160 AQI
    aqi_base_pm25 = 95.0
    aqi_base_pm10 = 145.0
    last_aqi_update = 0.0   # track when we last updated AQI

    while True:
        try:
            now = time.time()
            with state_lock:
                # Temperature and humidity drift every 3s (fast sensors)
                sensors["temperature"] = round(base_temp + random.uniform(-1.5, 2.0), 1)
                sensors["humidity"]    = round(base_humidity + random.uniform(-5, 5), 1)
                sensors["smoke"]       = round(random.uniform(1.5, 8.0), 1)
                sensors["gas"]         = round(random.uniform(1.0, 6.0), 1)

                # AQI updates every 60 seconds — air quality doesn't spike every 3 seconds
                if now - last_aqi_update >= 60:
                    # Slow drift: ±10 from base, clamp to realistic Gurugram range
                    aqi_base_pm25 = round(max(40, min(220, aqi_base_pm25 + random.uniform(-10, 10))), 1)
                    aqi_base_pm10 = round(max(60, min(300, aqi_base_pm10 + random.uniform(-12, 12))), 1)
                    aqi_val, aqi_cat = calculate_aqi(aqi_base_pm25, aqi_base_pm10)
                    sensors["pm25"]         = aqi_base_pm25
                    sensors["pm10"]         = aqi_base_pm10
                    sensors["aqi"]          = aqi_val
                    sensors["aqi_category"] = aqi_cat
                    sensors["co2_ppm"]      = round(random.uniform(700, 1100), 0)
                    last_aqi_update = now

                # HVAC logic
                hvac_status, target = determine_hvac(sensors["temperature"], sensors["humidity"])
                sensors["hvac_status"] = hvac_status
                sensors["hvac_target"] = target

                # Smoke/fire detection
                reading = SensorReading(
                    timestamp=now,
                    smoke=sensors["smoke"],
                    gas=sensors["gas"],
                    temperature=sensors["temperature"]
                )
                alerts = detector.evaluate(reading)
                for alert in alerts:
                    alert_entry = {
                        "type":    alert["type"],
                        "level":   alert["level"],
                        "message": alert["message"],
                        "time":    datetime.now().strftime("%H:%M:%S")
                    }
                    alert_history.append(alert_entry)
                    if len(alert_history) > 50:
                        alert_history.pop(0)

                # Automation rules — checked every tick, each rule has its
                # own cooldown so this doesn't spam-toggle devices.
                automation.evaluate_rules(devices, sensors, family_members, state_lock)
                # Per-member routines — time-based, fires once per day per routine.
                automation.evaluate_routines(devices, state_lock)
        except Exception as e:
            # Without this, any unexpected error here would silently kill the
            # daemon thread forever — sensor data would just freeze at its
            # last values with absolutely no record of why. Log it and keep
            # the loop alive instead.
            logger.error("Sensor simulation loop error: %s", e, exc_info=True)

        time.sleep(3)

# ──────────────────────────────────────────────
# FASTAPI APP
# ──────────────────────────────────────────────
app = FastAPI(title="Smart Home API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)

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
async def logout(request: Request, response: Response, smarthome_session: Optional[str] = Cookie(default=None)):
    user = auth.get_session_user(smarthome_session)
    if user:
        db.add_audit_entry(user["username"], "logout", ip_address=_client_ip(request))
    auth.end_session(smarthome_session)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"ok": True}


@app.get("/api/auth/me")
async def get_me(user: dict = Depends(get_current_user)):
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
async def get_audit_log(user: dict = Depends(get_current_user)):
    # Any logged-in member can view the audit log — transparency for the whole household
    return db.get_audit_log()


@app.get("/api/system-logs")
async def get_system_logs(lines: int = 200, level: Optional[str] = None, user: dict = Depends(get_current_user)):
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


# ── Automation rules ────────────────────────────────────────────────────

class AutomationConditionIn(BaseModel):
    type: str  # sensor_above / sensor_below / nobody_home_minutes / time_of_day
    key: Optional[str] = None
    threshold: Optional[float] = None
    minutes: Optional[int] = None
    hour: Optional[int] = None
    minute: Optional[int] = None
    window_minutes: Optional[int] = None

class AutomationActionIn(BaseModel):
    room: str
    device: str
    set: dict

class AutomationRuleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    condition: AutomationConditionIn
    action: AutomationActionIn
    enabled: bool = True
    cooldown_seconds: int = 300


@app.get("/api/automation/rules")
async def list_automation_rules(user: dict = Depends(get_current_user)):
    return db.get_automation_rules()


@app.post("/api/automation/rules")
async def create_automation_rule(body: AutomationRuleCreate, request: Request, user: dict = Depends(get_current_user)):
    # Validate the action targets a real device before saving — a rule
    # pointing at a room/device that doesn't exist would silently no-op
    # forever, which is worse than rejecting it up front.
    if body.action.room not in devices or body.action.device not in devices[body.action.room]:
        raise HTTPException(status_code=400, detail=f"{body.action.room}.{body.action.device} is not a real device")
    rule = db.create_automation_rule(
        name=body.name, description=body.description,
        condition=body.condition.dict(exclude_none=True),
        action=body.action.dict(),
        enabled=body.enabled, cooldown_seconds=body.cooldown_seconds,
    )
    db.add_audit_entry(user["username"], "automation_rule_created", detail=body.name, ip_address=_client_ip(request))
    return rule


@app.post("/api/automation/rules/{rule_id}/toggle")
async def toggle_automation_rule(rule_id: int, request: Request, user: dict = Depends(get_current_user)):
    rule = db.get_automation_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    db.update_automation_rule_enabled(rule_id, not rule["enabled"])
    db.add_audit_entry(user["username"], "automation_rule_toggled", detail=f"{rule['name']} -> {'enabled' if not rule['enabled'] else 'disabled'}", ip_address=_client_ip(request))
    return db.get_automation_rule(rule_id)


@app.delete("/api/automation/rules/{rule_id}")
async def delete_automation_rule(rule_id: int, request: Request, user: dict = Depends(get_current_user)):
    rule = db.get_automation_rule(rule_id)
    db.delete_automation_rule(rule_id)
    db.add_audit_entry(user["username"], "automation_rule_deleted", detail=rule["name"] if rule else str(rule_id), ip_address=_client_ip(request))
    return {"ok": True}


@app.get("/api/automation/runs")
async def list_automation_runs(user: dict = Depends(get_current_user)):
    return db.get_automation_runs()


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


@app.get("/api/routines")
async def list_routines(member_id: Optional[int] = None, user: dict = Depends(get_current_user)):
    # Non-owners can only see their own routines
    if user["role"] != "owner" and member_id != user["member_id"]:
        member_id = user["member_id"]
    return db.get_routines(member_id=member_id)


@app.post("/api/routines")
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
async def delete_routine(routine_id: int, request: Request, user: dict = Depends(get_current_user)):
    routine = db.get_routine(routine_id)
    if not routine:
        raise HTTPException(status_code=404, detail="Routine not found")
    if user["role"] != "owner" and routine["member_id"] != user["member_id"]:
        raise HTTPException(status_code=403, detail="You can only delete your own routines")
    db.delete_routine(routine_id)
    db.add_audit_entry(user["username"], "routine_deleted", detail=routine["name"], ip_address=_client_ip(request))
    return {"ok": True}


    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))

@app.get("/api/status")
async def get_status():
    with state_lock:
        return {
            "sensors": sensors.copy(),
            "devices": devices.copy(),
            "energy_watts": calculate_energy(),
            "energy_kwh_today": round(calculate_energy() * 8 / 1000, 2),
            "timestamp": datetime.now().isoformat()
        }

@app.get("/api/sensors")
async def get_sensors():
    with state_lock:
        return sensors.copy()

@app.get("/api/devices")
async def get_devices():
    with state_lock:
        return devices.copy()

@app.post("/api/device/toggle")
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
        return {"ok": True, "device": dev, "energy_watts": calculate_energy()}

@app.get("/api/family")
async def get_family():
    return family_members

@app.post("/api/family/add")
async def add_family(body: MemberAdd, request: Request, user: Optional[dict] = Depends(get_current_user_optional)):
    colors = ["#4f46e5","#7c3aed","#0891b2","#059669","#dc2626","#d97706","#be185d"]
    avatar = body.name[:2].upper()
    color = colors[len(family_members) % len(colors)]
    member = db.add_family_member(name=body.name, role=body.role, status="away", avatar=avatar, color=color)
    family_members.append(member)
    db.add_audit_entry(user["username"] if user else "anonymous", "family_member_added", detail=body.name, ip_address=_client_ip(request))
    return member

@app.delete("/api/family/{member_id}")
async def delete_member(member_id: int, request: Request, user: Optional[dict] = Depends(get_current_user_optional)):
    global family_members
    removed = next((m for m in family_members if m["id"] == member_id), None)
    db.delete_family_member(member_id)
    family_members = [m for m in family_members if m["id"] != member_id]
    db.add_audit_entry(user["username"] if user else "anonymous", "family_member_removed", detail=removed["name"] if removed else str(member_id), ip_address=_client_ip(request))
    return {"ok": True}

@app.get("/api/security/logs")
async def get_security_logs():
    return db.get_security_logs()

@app.post("/api/security/logs")
async def add_security_log(body: LogAdd):
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
async def log_intruder():
    entry = db.add_security_log(
        person="Unknown Person",
        type_="intruder",
        event="unrecognized face detected at front door",
        time_str=datetime.now().strftime("%H:%M"),
        date_str=datetime.now().strftime("%d %b"),
        status="unauthorized",
    )
    security_logs.insert(0, entry)
    return entry

@app.post("/api/security/member-detected")
async def log_member_detected(body: dict):
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

@app.get("/api/alerts")
async def get_alerts():
    return alert_history[-20:]

@app.get("/api/energy")
async def get_energy():
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
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
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
        if websocket in ws_clients:
            ws_clients.remove(websocket)
    except Exception as e:
        logger.error("WebSocket connection error: %s", e, exc_info=True)
        if websocket in ws_clients:
            ws_clients.remove(websocket)

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
        print("\n  First run — generated login credentials (SAVE THESE NOW,")
        print("  shown only once, never stored anywhere in plain text):")
        for uname, pwd in _GENERATED_PASSWORDS_THIS_RUN:
            print(f"    {uname:12s}  {pwd}")
        print("\n  Each person should log in and change their password via")
        print("  the Account menu — see /api/auth/change-password.")
        logger.info("First run — generated %d default user accounts", len(_GENERATED_PASSWORDS_THIS_RUN))
    print("="*55 + "\n")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
