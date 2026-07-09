"""
Smart Climate Control — FastAPI Server
Runs the climate sensor loop in an async background task (every 60s).

Endpoints:
    GET /api/all              → everything in one response
    GET /docs                 → Swagger UI

Note: Blynk integration has been removed. Data is served directly
      to the custom dashboard via the REST API.
"""

import os
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

# ──────────────────────────────────────────────
# CONFIGURATION & LOGGING
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("climate_api")

# Load .env from this script's own directory, not the current working
# directory — so the OpenWeather key is found no matter where the service
# is launched from (e.g. the repo root vs inside climate-control/).
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")

if not OPENWEATHER_API_KEY:
    raise RuntimeError("OPENWEATHER_API_KEY is not set in the environment or .env file.")

CITY = os.getenv("CITY", "Gurugram")
try:
    LATITUDE = float(os.getenv("LATITUDE", "28.477511"))
    LONGITUDE = float(os.getenv("LONGITUDE", "77.080851"))
except ValueError as e:
    raise RuntimeError(f"Invalid LATITUDE or LONGITUDE value in .env: {e}")

OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"

POLL_INTERVAL = 60
API_PORT = 3000


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

# asyncio.Lock is non-blocking and safe for use inside async functions.
data_lock = asyncio.Lock()

# Shared long-lived HTTP client — initialized at startup, reused across all poll cycles
# to avoid the overhead of a new TCP connection every 60 seconds.
http_client: Optional[httpx.AsyncClient] = None


# ──────────────────────────────────────────────
# CORE SENSOR & CONTROL LOGIC
# ──────────────────────────────────────────────
async def fetch_weather(client: httpx.AsyncClient):
    """Fetch current weather from OpenWeather using lat/lon."""
    params = {
        "lat": LATITUDE,
        "lon": LONGITUDE,
        "appid": OPENWEATHER_API_KEY,
        "units": "metric",
    }
    response = await client.get(OPENWEATHER_URL, params=params, timeout=10.0)
    response.raise_for_status()
    data = response.json()
    return data["main"]["temp"], data["main"]["humidity"], data["weather"][0]["description"]


def determine_hvac(temperature: float, humidity: int):
    """Smart Climate Control decision engine."""
    if temperature >= 35.0:
        return "MAX COOLING", 20.0
    elif temperature >= 28.0 and humidity > 60:
        return "DEHUMIDIFY & COOL", 22.0
    elif temperature >= 20.0:
        return "ECO MODE (FAN ONLY)", 24.0
    else:
        return "HEATING", 24.0


async def run_sensor_and_control():
    """
    Core function — fetches weather, runs HVAC logic,
    and updates the shared data store.
    Uses the shared long-lived http_client (no new connection per cycle).
    """
    temperature, humidity, condition = await fetch_weather(http_client)
    hvac_status, target_temp = determine_hvac(temperature, humidity)

    logger.info(f"[Weather] Temp: {temperature}°C | Humidity: {humidity}% | Condition: {condition}")
    logger.info(f"[HVAC] Status: {hvac_status} | Target: {target_temp}°C")

    # Update shared store so /api/all always serves latest data
    async with data_lock:
        latest_data["weather"]["temperature"] = temperature
        latest_data["weather"]["humidity"] = humidity
        latest_data["weather"]["condition"] = condition
        latest_data["hvac"]["status"] = hvac_status
        latest_data["hvac"]["target_temp"] = target_temp
        latest_data["last_updated"] = datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────
# BACKGROUND SENSOR LOOP (every 60s)
# ──────────────────────────────────────────────
async def sensor_loop():
    """Background async task — runs the sensor flow every POLL_INTERVAL seconds."""
    logger.info("=" * 60)
    logger.info("Smart Climate Control — Background Loop Started")
    logger.info(f"City: {CITY} ({LATITUDE}, {LONGITUDE})")
    logger.info(f"Interval: {POLL_INTERVAL}s")
    logger.info("=" * 60)

    while True:
        try:
            await run_sensor_and_control()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error from API: {e.response.status_code} — {e}")
        except httpx.RequestError as e:
            logger.error(f"Network/API error: {e}")
        except KeyError as e:
            logger.error(f"Missing key in API response: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")

        try:
            await asyncio.sleep(POLL_INTERVAL)
        except asyncio.CancelledError:
            raise  # propagate cleanly on shutdown


# ──────────────────────────────────────────────
# FASTAPI APP
# ──────────────────────────────────────────────
@asynccontextmanager
async def lifespan(application: FastAPI):
    """Initialize shared resources, start the background sensor loop, and clean up on shutdown."""
    global http_client

    # Single shared client for all poll cycles — avoids new TCP handshake every 60s
    http_client = httpx.AsyncClient()

    task = asyncio.create_task(sensor_loop())

    logger.info(f"🚀 API server running at http://localhost:{API_PORT}")
    logger.info(f"   GET /api/all  → everything")
    logger.info(f"   GET /docs     → Swagger UI")

    yield

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        await http_client.aclose()
        logger.info("HTTP client closed — shutdown complete.")


app = FastAPI(
    title="Smart Climate Control API",
    description="Real-time weather & HVAC data, powered by OpenWeather. Served to the custom dashboard via REST API.",
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

# ── Rate limiting (slowapi) ──────────────────────────────────────────────
# Internal service (only server.py calls it in production), but limited
# anyway as a safety measure. default_limits + SlowAPIMiddleware cover any
# route without its own @limiter.limit decorator at 30/minute.
limiter = Limiter(key_func=get_remote_address, default_limits=["30/minute"])
app.state.limiter = limiter


def _rate_limit_exceeded(request: Request, exc: RateLimitExceeded):
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
app.add_middleware(SlowAPIMiddleware)


# ──────────────────────────────────────────────
# ENDPOINTS
# ──────────────────────────────────────────────
@app.get("/api/all", response_model=AllDataResponse, responses={503: {"model": ErrorResponse}})
@limiter.limit("60/minute")
async def get_all(request: Request):
    """Return all weather + HVAC data in one response."""
    async with data_lock:
        if latest_data["last_updated"] is None:
            raise HTTPException(status_code=503, detail="No data yet — sensor is still initializing")
        return latest_data


# ──────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=API_PORT)
