# # Add this at the very top of sensor_simulator.py
# from hardware.aqi_calculator import calculate_aqi


# import random
# from datetime import datetime

# def read_all_sensors():
#     """
#     This function pretends to be real sensors.
#     It returns a dictionary with all sensor readings.
#     Later we will replace this with real sensor code.
#     A dictionary is just a collection of key-value pairs.
#     Example: {"name": "Delhi", "aqi": 200}
#     """

#     # random.uniform(a, b) gives a random decimal number between a and b
#     # These ranges are realistic for Delhi air quality

#     reading = {
#         "timestamp":   datetime.now().isoformat(),
#         "pm25":        round(random.uniform(50, 300), 2),
#         "pm10":        round(random.uniform(80, 400), 2),
#         "co2_ppm":     round(random.uniform(800, 2000), 2),
#         "temperature": round(random.uniform(28, 42), 2),
#         "humidity":    round(random.uniform(40, 90), 2),
#         "latitude":    28.6139,   # fixed: Connaught Place, Delhi
#         "longitude":   77.2090,
#     }

#     # NEW: calculate AQI from the pm25 and pm10 we just generated
#     aqi, category = calculate_aqi(reading["pm25"], reading["pm10"])
#     reading["aqi"]      = aqi
#     reading["category"] = category

#     return reading


# # This block only runs when you run THIS file directly
# # It does NOT run when another file imports this function
# if __name__ == "__main__":
#     print("Reading sensors every 3 seconds. Press Ctrl+C to stop.\n")

#     import time

#     while True:
#         data = read_all_sensors()
#         print(data)
#         print("---")
#         time.sleep(3)   # wait 3 seconds then read again



# hardware/sensor_simulator.py
# Fetches REAL Delhi AQI for your home location once,
# then simulates a sensor that fluctuates realistically around that real value

import random
import requests
import os
import sys
import time
from datetime import datetime, timedelta
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)
load_dotenv(os.path.join(BASE_DIR, ".env"))

from hardware.aqi_calculator import calculate_aqi

WAQI_TOKEN = os.getenv("WAQI_TOKEN")

# --- CHANGE THESE TWO LINES to your home coordinates (ATNT LOCATION)---
HOME_LAT = 28.47789142765956
HOME_LON = 77.08135446822514

# ---------------------------------------------------------

# These store the "real" baseline values
# They get refreshed every 30 minutes
_baseline = {
    "pm25": 100.0,   # default fallback values
    "pm10": 150.0,
    "station_name": "Unknown",
    "last_fetched": None
}


def fetch_real_baseline():
    """
    Fetches real current AQI from the nearest station to HOME_LAT, HOME_LON
    using WAQI's geo-based lookup.
    Updates the _baseline dict.
    """
    url = f"https://api.waqi.info/feed/geo:{HOME_LAT};{HOME_LON}/?token={WAQI_TOKEN}"

    try:
        response = requests.get(url, timeout=10)
        data = response.json()

        if data["status"] != "ok":
            print(f"WAQI error: {data}. Using last known baseline.")
            return

        d = data["data"]

        # Extract pm25 and pm10 if available, else estimate from overall AQI
        iaqi = d.get("iaqi", {})
        pm25 = iaqi.get("pm25", {}).get("v")
        pm10 = iaqi.get("pm10", {}).get("v")

        # If pm25/pm10 not directly available, back-calculate a rough estimate
        # from the overall AQI value (reverse of CPCB formula, approximate)
        overall_aqi = d.get("aqi", 100)
        if pm25 is None:
            pm25 = overall_aqi * 0.6   # rough approximation
        if pm10 is None:
            pm10 = overall_aqi * 0.9   # rough approximation

        _baseline["pm25"] = float(pm25)
        _baseline["pm10"] = float(pm10)
        _baseline["station_name"] = d.get("city", {}).get("name", "Unknown")
        _baseline["last_fetched"] = datetime.now()

        print(f"Real baseline updated → Station: {_baseline['station_name']}, "
              f"PM2.5: {_baseline['pm25']}, PM10: {_baseline['pm10']}")

    except Exception as e:
        print(f"Could not fetch real baseline: {e}. Using last known values.")


def read_all_sensors():
    """
    Returns one sensor reading.
    PM2.5 and PM10 fluctuate slightly (±8%) around the real baseline,
    simulating natural minute-to-minute sensor variation.
    Refreshes the real baseline every 30 minutes.
    """
    # Refresh baseline every 60 minutes (or on very first call)
    if (_baseline["last_fetched"] is None or
            datetime.now() - _baseline["last_fetched"] > timedelta(minutes=60)):
        fetch_real_baseline()

    # Add small realistic fluctuation (±8%) around real baseline
    pm25 = _baseline["pm25"] * random.uniform(0.92, 1.08)
    pm10 = _baseline["pm10"] * random.uniform(0.92, 1.08)

    pm25 = round(max(pm25, 0), 2)
    pm10 = round(max(pm10, 0), 2)

    aqi, category = calculate_aqi(pm25, pm10)

    reading = {
        "timestamp":   datetime.now().isoformat(),
        "pm25":        pm25,
        "pm10":        pm10,
        "co2_ppm":     round(random.uniform(800, 1500), 2),
        "temperature": round(random.uniform(28, 38), 2),
        "humidity":    round(random.uniform(40, 80), 2),
        "latitude":    HOME_LAT,
        "longitude":   HOME_LON,
        "nearest_station": _baseline["station_name"],
        "aqi":         aqi,
        "category":    category,
    }

    return reading


if __name__ == "__main__":
    print("Fetching real baseline for your location...\n")
    fetch_real_baseline()

    print("\nSimulating sensor readings every 5 seconds. Press Ctrl+C to stop.\n")
    while True:
        data = read_all_sensors()
        print(data)
        print("---")
        time.sleep(60)
