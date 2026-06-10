# network/mqtt_subscriber.py

import paho.mqtt.client as mqtt
import json
import os
import sys
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)
load_dotenv(os.path.join(BASE_DIR, ".env"))

from cloud.firebase_client import save_reading
from cloud.alert_system import send_sms_alert

BROKER   = os.getenv("MQTT_BROKER")
USERNAME = os.getenv("MQTT_USER")
PASSWORD = os.getenv("MQTT_PASS")
PORT     = 8883
TOPIC    = "delhi/aqi"


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Subscriber connected. Waiting for messages...\n")
        client.subscribe(TOPIC)
    else:
        print(f"Connection failed. Code: {rc}")


def on_message(client, userdata, msg):
    data = json.loads(msg.payload.decode())

    print(f"Received → AQI: {data['aqi']} ({data['category']})")

    # Save every reading to Firebase
    save_reading(data)

    # Send SMS alert if AQI is dangerous
    send_sms_alert(data)


client = mqtt.Client()
client.username_pw_set(USERNAME, PASSWORD)
client.tls_set()
client.on_connect = on_connect
client.on_message = on_message

print(f"Connecting to {BROKER}...")
client.connect(BROKER, PORT)
client.loop_forever()