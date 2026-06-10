# network/mqtt_publisher.py

import paho.mqtt.client as mqtt
import json
import time
import os
import sys
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)
load_dotenv(os.path.join(BASE_DIR, ".env"))

from hardware.sensor_simulator import read_all_sensors

BROKER   = os.getenv("MQTT_BROKER")
USERNAME = os.getenv("MQTT_USER")
PASSWORD = os.getenv("MQTT_PASS")
PORT     = 8883
TOPIC    = "delhi/aqi"


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connected to HiveMQ broker successfully.")
    else:
        print(f"Connection failed. Code: {rc}")


client = mqtt.Client()
client.username_pw_set(USERNAME, PASSWORD)
client.tls_set()
client.on_connect = on_connect
client.connect(BROKER, PORT)
client.loop_start()
time.sleep(2)

print("Sending sensor data every 5 seconds. Press Ctrl+C to stop.\n")

while True:
    reading = read_all_sensors()
    payload = json.dumps(reading)
    client.publish(TOPIC, payload, qos=1)
    print(f"Sent → AQI: {reading['aqi']} ({reading['category']})")
    time.sleep(5)