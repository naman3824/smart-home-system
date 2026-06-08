# Add this at the very top of sensor_simulator.py
from hardware.aqi_calculator import calculate_aqi


import random
from datetime import datetime

def read_all_sensors():
    """
    This function pretends to be real sensors.
    It returns a dictionary with all sensor readings.
    Later we will replace this with real sensor code.
    A dictionary is just a collection of key-value pairs.
    Example: {"name": "Delhi", "aqi": 200}
    """

    # random.uniform(a, b) gives a random decimal number between a and b
    # These ranges are realistic for Delhi air quality

    reading = {
        "timestamp":   datetime.now().isoformat(),
        "pm25":        round(random.uniform(50, 300), 2),
        "pm10":        round(random.uniform(80, 400), 2),
        "co2_ppm":     round(random.uniform(800, 2000), 2),
        "temperature": round(random.uniform(28, 42), 2),
        "humidity":    round(random.uniform(40, 90), 2),
        "latitude":    28.6139,   # fixed: Connaught Place, Delhi
        "longitude":   77.2090,
    }

    # NEW: calculate AQI from the pm25 and pm10 we just generated
    aqi, category = calculate_aqi(reading["pm25"], reading["pm10"])
    reading["aqi"]      = aqi
    reading["category"] = category

    return reading


# This block only runs when you run THIS file directly
# It does NOT run when another file imports this function
if __name__ == "__main__":
    print("Reading sensors every 3 seconds. Press Ctrl+C to stop.\n")

    import time

    while True:
        data = read_all_sensors()
        print(data)
        print("---")
        time.sleep(3)   # wait 3 seconds then read again