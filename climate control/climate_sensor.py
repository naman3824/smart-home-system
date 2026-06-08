"""
Virtual Climate Control Sensor
Fetches real-time weather data from OpenWeather API for Noida
and pushes it to a Blynk dashboard via HTTP REST API.
"""

import os
import time
import requests
from dotenv import load_dotenv

# ──────────────────────────────────────────────
# CONFIGURATION — Loaded from .env file
# ──────────────────────────────────────────────
load_dotenv()

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
BLYNK_AUTH_TOKEN = os.getenv("BLYNK_AUTH_TOKEN")

# Location for weather data (lat/lon for central Gurugram)
CITY = "Gurugram"
LATITUDE = 28.477511
LONGITUDE = 77.080851
OPENWEATHER_URL = "https://api.openweathermap.org/data/2.5/weather"
BLYNK_URL = "https://blynk.cloud/external/api/update"

# Blynk Virtual Pin assignments
PIN_TEMPERATURE = "V0"
PIN_HUMIDITY = "V1"
PIN_CONDITION = "V2"
PIN_HVAC_STATUS = "V3"
PIN_TARGET_TEMP = "V4"

# Polling interval in seconds
POLL_INTERVAL = 30


def fetch_weather():
    """Fetch current weather data from OpenWeather API using lat/lon coordinates."""
    params = {
        "lat": LATITUDE,
        "lon": LONGITUDE,
        "appid": OPENWEATHER_API_KEY,
        "units": "metric",  # Celsius
    }
    response = requests.get(OPENWEATHER_URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    temperature = data["main"]["temp"]
    humidity = data["main"]["humidity"]
    condition = data["weather"][0]["description"]

    return temperature, humidity, condition


def determine_hvac(temperature, humidity):
    """Smart Climate Control decision engine.

    Returns (hvac_status, target_internal_temp) based on external conditions.
    Rules are evaluated in priority order (highest temp first).
    """
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
    params = {
        "token": BLYNK_AUTH_TOKEN,
        pin: value,
    }
    response = requests.get(BLYNK_URL, params=params, timeout=10)
    response.raise_for_status()


def main():
    """Main loop — fetch weather and push to Blynk every POLL_INTERVAL seconds."""
    print("=" * 60)
    print("  Smart Climate Control Sensor — Starting Up")
    print(f"  City       : {CITY}")
    print(f"  Interval   : {POLL_INTERVAL}s")
    print(f"  Sensors    : Temp→{PIN_TEMPERATURE}  Hum→{PIN_HUMIDITY}  Cond→{PIN_CONDITION}")
    print(f"  HVAC       : Status→{PIN_HVAC_STATUS}  Target→{PIN_TARGET_TEMP}")
    print("=" * 60)
    print()

    while True:
        try:
            # --- Fetch external weather ---
            temperature, humidity, condition = fetch_weather()

            print(f"[Weather]  🌡  Temp: {temperature}°C  |  💧 Humidity: {humidity}%  |  🌤  {condition}")

            # --- Smart Climate Control decision ---
            hvac_status, target_internal_temp = determine_hvac(temperature, humidity)

            print(f"[HVAC]     🏠  Status: {hvac_status}  |  🎯 Target: {target_internal_temp}°C")

            # --- Push sensor data to Blynk ---
            push_to_blynk(PIN_TEMPERATURE, temperature)
            push_to_blynk(PIN_HUMIDITY, humidity)
            push_to_blynk(PIN_CONDITION, condition)

            # --- Push HVAC decisions to Blynk ---
            push_to_blynk(PIN_HVAC_STATUS, hvac_status)
            push_to_blynk(PIN_TARGET_TEMP, target_internal_temp)

            print(f"[Blynk]    ✅  All data pushed → {PIN_TEMPERATURE}, {PIN_HUMIDITY}, {PIN_CONDITION}, {PIN_HVAC_STATUS}, {PIN_TARGET_TEMP}")
            print("-" * 60)

        except requests.exceptions.RequestException as e:
            print(f"[Error]    ❌  Network/API error: {e}")
        except KeyError as e:
            print(f"[Error]    ❌  Unexpected API response — missing key: {e}")
        except Exception as e:
            print(f"[Error]    ❌  Unexpected error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
