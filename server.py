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
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

import db

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
        "ac":    {"on": False, "temp": 24, "mode": "cool", "watts": 1500}
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
    }
}

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
db.init_db()
db.seed_family_if_empty(_DEFAULT_FAMILY)
family_members = db.get_family_members()

# Security logs — persisted in SQLite, starts empty on a fresh database.
# Real entries are added going forward by actual face recognition / manual
# logging; nothing here is pre-seeded demo data.
security_logs = db.get_security_logs()

# Smoke/fire detector instance
detector = SmokeGasFireDetector(smoke_threshold=40.0, gas_threshold=40.0,
                                temp_threshold=50.0, temp_spike_threshold=8.0,
                                debounce_seconds=120)

# WebSocket connections
ws_clients: List[WebSocket] = []
alert_history = []

# ── ENERGY CALCULATION ──
def calculate_energy():
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

# ──────────────────────────────────────────────
# ENDPOINTS
# ──────────────────────────────────────────────
@app.get("/")
async def root():
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
async def toggle_device(body: DeviceToggle):
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
        return {"ok": True, "device": dev, "energy_watts": calculate_energy()}

@app.get("/api/family")
async def get_family():
    return family_members

@app.post("/api/family/add")
async def add_family(body: MemberAdd):
    colors = ["#4f46e5","#7c3aed","#0891b2","#059669","#dc2626","#d97706","#be185d"]
    avatar = body.name[:2].upper()
    color = colors[len(family_members) % len(colors)]
    member = db.add_family_member(name=body.name, role=body.role, status="away", avatar=avatar, color=color)
    family_members.append(member)
    return member

@app.delete("/api/family/{member_id}")
async def delete_member(member_id: int):
    global family_members
    db.delete_family_member(member_id)
    family_members = [m for m in family_members if m["id"] != member_id]
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
        room_breakdown = {}
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
            room_breakdown[room] = room_watts
        return {
            "total_watts": watts,
            "kwh_today": round(watts * 8 / 1000, 2),
            "cost_today": round(watts * 8 / 1000 * 8.5, 2),
            "room_breakdown": room_breakdown
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
        ws_clients.remove(websocket)
    except Exception:
        if websocket in ws_clients:
            ws_clients.remove(websocket)

# ──────────────────────────────────────────────
# STARTUP
# ──────────────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    t = threading.Thread(target=simulate_sensors, daemon=True)
    t.start()
    print("\n" + "="*55)
    print("  Smart Home Dashboard — Server Started")
    print("  Open: http://localhost:8000")
    print("="*55 + "\n")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
