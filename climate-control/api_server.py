"""
Smart Climate Control — FastAPI Server
Runs the climate sensor loop in a background thread (every 30s)
AND exposes a /trigger-climate endpoint for on-demand execution.

Endpoints:
    GET /api/all              → everything in one response
    GET /docs                 → Swagger UI
"""

import os
import time
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import requests as http_requests
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from dotenv import load_dotenv

# ──────────────────────────────────────────────
# CONFIGURATION — Credentials loaded from .env
# ──────────────────────────────────────────────
load_dotenv()

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
BLYNK_AUTH_TOKEN = os.getenv("BLYNK_AUTH_TOKEN")

CITY = "Gurugram"
LATITUDE = 28.477511
LONGITUDE = 77.080851
OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
BLYNK_URL = "https://blynk.cloud/external/api/update"

PIN_TEMPERATURE = "V0"
PIN_HUMIDITY = "V1"
PIN_CONDITION = "V2"
PIN_HVAC_STATUS = "V3"
PIN_TARGET_TEMP = "V4"

POLL_INTERVAL = 60
API_PORT = 8000


# ──────────────────────────────────────────────
# PYDANTIC RESPONSE MODELS
# ──────────────────────────────────────────────
class LocationInfo(BaseModel):
    city: str
    latitude: float
    longitude: float


class WeatherInfo(BaseModel):
    temperature: Optional[float] = None
    humidity: Optional[int] = None
    condition: Optional[str] = None


class HvacInfo(BaseModel):
    status: Optional[str] = None
    target_temp: Optional[float] = None


class AllDataResponse(BaseModel):
    weather: WeatherInfo
    hvac: HvacInfo
    location: LocationInfo
    last_updated: Optional[str] = None


class ErrorResponse(BaseModel):
    detail: str


# ──────────────────────────────────────────────
# SHARED DATA STORE
# ──────────────────────────────────────────────
latest_data = {
    "weather": {
        "temperature": None,
        "humidity": None,
        "condition": None,
    },
    "hvac": {
        "status": None,
        "target_temp": None,
    },
    "location": {
        "city": CITY,
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
    },
    "last_updated": None,
}

data_lock = threading.Lock()


# ──────────────────────────────────────────────
# CORE SENSOR & CONTROL LOGIC
# ──────────────────────────────────────────────
def fetch_weather():
    """Fetch current weather from OpenWeather using lat/lon."""
    params = {
        "lat": LATITUDE,
        "lon": LONGITUDE,
        "appid": OPENWEATHER_API_KEY,
        "units": "metric",
    }
    response = http_requests.get(OPENWEATHER_URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    return data["main"]["temp"], data["main"]["humidity"], data["weather"][0]["description"]


def determine_hvac(temperature, humidity):
    """Smart Climate Control decision engine."""
    if temperature >= 35.0:
        return "MAX COOLING", 20.0
    elif temperature >= 28.0 and humidity > 60:
        return "DEHUMIDIFY & COOL", 22.0
    elif temperature >= 20.0:
        return "ECO MODE (FAN ONLY)", 24.0
    else:
        return "HEATING", 24.0


def push_to_blynk(pin, value):
    """Push a single value to a Blynk virtual pin."""
    params = {"token": BLYNK_AUTH_TOKEN, pin: value}
    response = http_requests.get(BLYNK_URL, params=params, timeout=10)
    response.raise_for_status()


def run_virtual_sensor_and_control():
    """
    Core function — fetches weather, runs HVAC logic, pushes to Blynk,
    and updates the shared data store. Called by both the background
    timer loop and the /trigger-climate API endpoint.

    Returns a dict with the fetched data on success.
    Raises on failure.
    """
    temperature, humidity, condition = fetch_weather()
    hvac_status, target_temp = determine_hvac(temperature, humidity)

    print(f"[Weather]  🌡  Temp: {temperature}°C  |  💧 Humidity: {humidity}%  |  🌤  {condition}")
    print(f"[HVAC]     🏠  Status: {hvac_status}  |  🎯 Target: {target_temp}°C")

    # Push all data to Blynk
    push_to_blynk(PIN_TEMPERATURE, temperature)
    push_to_blynk(PIN_HUMIDITY, humidity)
    push_to_blynk(PIN_CONDITION, condition)
    push_to_blynk(PIN_HVAC_STATUS, hvac_status)
    push_to_blynk(PIN_TARGET_TEMP, target_temp)

    print(f"[Blynk]    ✅  All data pushed → {PIN_TEMPERATURE}, {PIN_HUMIDITY}, {PIN_CONDITION}, {PIN_HVAC_STATUS}, {PIN_TARGET_TEMP}")
    print("-" * 60)

    # Update shared store
    now = datetime.now(timezone.utc).isoformat()
    with data_lock:
        latest_data["weather"]["temperature"] = temperature
        latest_data["weather"]["humidity"] = humidity
        latest_data["weather"]["condition"] = condition
        latest_data["hvac"]["status"] = hvac_status
        latest_data["hvac"]["target_temp"] = target_temp
        latest_data["last_updated"] = now

    return {
        "temperature": temperature,
        "humidity": humidity,
        "condition": condition,
        "hvac_status": hvac_status,
        "target_temp": target_temp,
        "timestamp": now,
    }


# ──────────────────────────────────────────────
# BACKGROUND SENSOR LOOP (every 30s)
# ──────────────────────────────────────────────
def sensor_loop():
    """Background thread — runs run_virtual_sensor_and_control() every POLL_INTERVAL seconds."""
    print("=" * 60)
    print("  Smart Climate Control — Background Loop Started")
    print(f"  City       : {CITY}")
    print(f"  Interval   : {POLL_INTERVAL}s")
    print("=" * 60)
    print()

    while True:
        try:
            run_virtual_sensor_and_control()
        except http_requests.exceptions.RequestException as e:
            print(f"[Error]    ❌  Network/API error: {e}")
        except KeyError as e:
            print(f"[Error]    ❌  Missing key: {e}")
        except Exception as e:
            print(f"[Error]    ❌  Unexpected: {e}")

        time.sleep(POLL_INTERVAL)


# ──────────────────────────────────────────────
# FASTAPI APP
# ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(application: FastAPI):
    """Start the background sensor loop when the app starts."""
    sensor_thread = threading.Thread(target=sensor_loop, daemon=True)
    sensor_thread.start()

    print()
    print(f"🚀  API server running at  http://localhost:{API_PORT}")
    print(f"    GET /api/all          → everything")
    print(f"    GET /docs             → Swagger UI")
    print()

    yield


app = FastAPI(
    title="Smart Climate Control API",
    description="Real-time weather & HVAC data for Gurugram, powered by OpenWeather + Blynk.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────
# ENDPOINTS
# ──────────────────────────────────────────────
@app.get("/api/all", response_model=AllDataResponse, responses={503: {"model": ErrorResponse}})
async def get_all():
    """Return all weather + HVAC data in one response."""
    with data_lock:
        if latest_data["last_updated"] is None:
            raise HTTPException(status_code=503, detail="No data yet — sensor is still initializing")
        return latest_data


# ──────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=API_PORT)
