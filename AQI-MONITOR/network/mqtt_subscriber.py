# network/mqtt_subscriber.py

import paho.mqtt.client as mqtt
import json
import os
import sys
from dotenv import load_dotenv
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)
load_dotenv(os.path.join(BASE_DIR, ".env"))

from cloud.firebase_client import save_reading, get_registered_emails
from cloud.alert_system import send_email_alert, send_daily_summary

BROKER   = os.getenv("MQTT_BROKER")
USERNAME = os.getenv("MQTT_USER")
PASSWORD = os.getenv("MQTT_PASS")
PORT     = 8883
TOPIC    = "delhi/aqi"

# Daily summary tracking
_todays_readings   = []
_last_summary_date = None


def check_daily_summary():
    """
    Har subah 8 AM pe daily summary email bhejta hai.
    Subscriber mein har reading pe ye check hota hai.
    """
    global _last_summary_date, _todays_readings

    now  = datetime.now()
    date = now.strftime("%Y-%m-%d")

    # 8 AM ho gaya aur aaj summary nahi bheji
    if now.hour >= 8 and _last_summary_date != date:
        if _todays_readings:
            recipients = get_registered_emails()
            print(f"Sending daily summary for {date}...")
            send_daily_summary(_todays_readings, recipients)

        _last_summary_date = date
        _todays_readings   = []   # naye din ke liye reset
        print(f"Daily summary sent for {date}. Readings reset.")


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Subscriber connected. Waiting for messages...\n")
        client.subscribe(TOPIC)
    else:
        print(f"Connection failed. Code: {rc}")


def on_message(client, userdata, msg):
    global _todays_readings

    data = json.loads(msg.payload.decode())
    print(f"Received → AQI: {data['aqi']} ({data['category']}) "
          f"| {datetime.now().strftime('%H:%M:%S')}")

    # Firebase mein save karo
    save_reading(data)

    # Aaj ki readings track karo (daily summary ke liye)
    _todays_readings.append(data)

    # Email alert check karo
    send_email_alert(data)

    # Daily summary check karo (8 AM pe)
    check_daily_summary()


client = mqtt.Client()
client.username_pw_set(USERNAME, PASSWORD)
client.tls_set()
client.on_connect = on_connect
client.on_message = on_message

print(f"Connecting to {BROKER}...")
client.connect(BROKER, PORT)
client.loop_forever()
