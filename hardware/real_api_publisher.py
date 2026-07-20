# real_api_publisher.py
import os
import time
import json

from typing import Optional

import requests
import paho.mqtt.client as mqtt

from dotenv import load_dotenv

load_dotenv()

# ==========================================================
# MQTT
# ==========================================================

BROKER_HOST = "test.mosquitto.org"
BROKER_PORT = 1883

TOPIC_SMOKE = "home/livingroom/smoke"
TOPIC_GAS = "home/livingroom/gas"
TOPIC_CO = "home/livingroom/co"
TOPIC_TEMP = "home/livingroom/temperature"

# ==========================================================
# OPENWEATHER
# ==========================================================

OPENWEATHER_API_KEY = os.getenv(
    "OPENWEATHER_API_KEY",
    ""
)

LAT = os.getenv(
    "OPENWEATHER_LAT",
    "28.6139"
)

LON = os.getenv(
    "OPENWEATHER_LON",
    "77.2090"
)

POLL_INTERVAL_SECONDS = 300

# ==========================================================
# REFERENCE LIMITS
# ==========================================================

PM25_LIMIT = 60.0
PM10_LIMIT = 100.0

NO2_LIMIT = 80.0
SO2_LIMIT = 80.0

CO_LIMIT = 10000.0

# ==========================================================
# NORMALIZATION
# ==========================================================

def normalize(value, reference):

    if reference <= 0:
        return 0

    score = (
        value / reference
    ) * 100

    return max(
        0,
        min(100, score)
    )

# ==========================================================
# WEATHER
# ==========================================================

def fetch_weather():

    url = (
        "https://api.openweathermap.org/data/2.5/weather"
        f"?lat={LAT}"
        f"&lon={LON}"
        f"&appid={OPENWEATHER_API_KEY}"
        "&units=metric"
    )

    try:

        response = requests.get(
            url,
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        return data

    except Exception as e:

        print(
            "[ERROR] Weather API:",
            e
        )

        return None

# ==========================================================
# AIR POLLUTION
# ==========================================================

def fetch_air_quality():

    url = (
        "https://api.openweathermap.org/data/2.5/air_pollution"
        f"?lat={LAT}"
        f"&lon={LON}"
        f"&appid={OPENWEATHER_API_KEY}"
    )

    try:

        response = requests.get(
            url,
            timeout=10
        )

        response.raise_for_status()

        data = response.json()

        return data

    except Exception as e:

        print(
            "[ERROR] Air Pollution API:",
            e
        )

        return None

# ==========================================================
# RISK CALCULATION
# ==========================================================

def calculate_smoke_risk(
    pm25,
    pm10
):

    smoke = (

        normalize(
            pm25,
            PM25_LIMIT
        ) * 0.7

        +

        normalize(
            pm10,
            PM10_LIMIT
        ) * 0.3

    )

    return round(
        min(100, smoke),
        2
    )

def calculate_gas_risk(
    no2,
    so2
):

    gas = (

        normalize(
            no2,
            NO2_LIMIT
        ) * 0.6

        +

        normalize(
            so2,
            SO2_LIMIT
        ) * 0.4

    )

    return round(
        min(100, gas),
        2
    )

def calculate_co_risk(
    co
):

    return round(

        normalize(
            co,
            CO_LIMIT
        ),

        2
    )

# ==========================================================
# MQTT PUBLISH
# ==========================================================

def publish_reading(
    client,
    topic,
    timestamp,
    value
):

    payload = json.dumps({

        "timestamp": timestamp,

        "value": value

    })

    client.publish(
        topic,
        payload
    )

# ==========================================================
# MAIN
# ==========================================================

def main():

    if not OPENWEATHER_API_KEY:

        print(
            "[ERROR] OPENWEATHER_API_KEY missing."
        )

        return

    client = mqtt.Client()

    print(
        f"Connecting to MQTT Broker "
        f"{BROKER_HOST}:{BROKER_PORT}"
    )

    client.connect(
        BROKER_HOST,
        BROKER_PORT,
        60
    )

    client.loop_start()

    try:

        while True:

            air_data = (
                fetch_air_quality()
            )

            weather_data = (
                fetch_weather()
            )

            if (
                air_data is None
                or
                weather_data is None
            ):

                time.sleep(
                    POLL_INTERVAL_SECONDS
                )

                continue

            item = (
                air_data["list"][0]
            )

            components = (
                item["components"]
            )

            timestamp = (
                item["dt"]
            )

            aqi = (
                item["main"]["aqi"]
            )

            pm25 = (
                components.get(
                    "pm2_5",
                    0
                )
            )

            pm10 = (
                components.get(
                    "pm10",
                    0
                )
            )

            no2 = (
                components.get(
                    "no2",
                    0
                )
            )

            so2 = (
                components.get(
                    "so2",
                    0
                )
            )

            co = (
                components.get(
                    "co",
                    0
                )
            )

            temperature = (
                weather_data["main"]["temp"]
            )

            smoke_risk = (
                calculate_smoke_risk(
                    pm25,
                    pm10
                )
            )

            gas_risk = (
                calculate_gas_risk(
                    no2,
                    so2
                )
            )

            co_risk = (
                calculate_co_risk(
                    co
                )
            )

            # ==========================================
            # MQTT
            # ==========================================

            publish_reading(
                client,
                TOPIC_SMOKE,
                timestamp,
                smoke_risk
            )

            publish_reading(
                client,
                TOPIC_GAS,
                timestamp,
                gas_risk
            )

            publish_reading(
                client,
                TOPIC_CO,
                timestamp,
                co_risk
            )

            publish_reading(
                client,
                TOPIC_TEMP,
                timestamp,
                temperature
            )

            # ==========================================
            # CONSOLE
            # ==========================================

            print()

            print("=" * 70)

            print(
                time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.localtime(
                        timestamp
                    )
                )
            )

            print()

            print(
                f"AQI: {aqi}"
            )

            print()

            print(
                f"PM2.5 : {pm25:.2f}"
            )

            print(
                f"PM10  : {pm10:.2f}"
            )

            print(
                f"NO2   : {no2:.2f}"
            )

            print(
                f"SO2   : {so2:.2f}"
            )

            print(
                f"CO    : {co:.2f}"
            )

            print()

            print(
                f"Smoke Risk : {smoke_risk:.1f}%"
            )

            print(
                f"Gas Risk   : {gas_risk:.1f}%"
            )

            print(
                f"CO Risk    : {co_risk:.1f}%"
            )

            print(
                f"Temp       : {temperature:.1f}°C"
            )

            print("=" * 70)

            time.sleep(
                POLL_INTERVAL_SECONDS
            )

    except KeyboardInterrupt:

        print(
            "\nPublisher stopped."
        )

    finally:

        client.loop_stop()

        client.disconnect()

# ==========================================================
# ENTRY
# ==========================================================

if __name__ == "__main__":

    main()
