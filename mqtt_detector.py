# detector
import os
import time
import json
from typing import Optional

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

from detector import SmokeGasFireDetector, SensorReading
from alerts_twilio import TwilioWhatsAppAlertSender
from logger import DetectorLogger  

load_dotenv()

BROKER_HOST = "test.mosquitto.org"
BROKER_PORT = 1883
TOPIC_SMOKE = "home/livingroom/smoke"
TOPIC_GAS = "home/livingroom/gas"
TOPIC_TEMP = "home/livingroom/temperature"


class MqttDetectorService:
    def __init__(self):
        debounce_seconds = int(os.getenv("ALERT_DEBOUNCE_SECONDS", "300"))
        false_alarm_consecutive = int(os.getenv("FALSE_ALARM_CONSECUTIVE", "3"))

        self.detector = SmokeGasFireDetector(
            smoke_threshold=40.0,     # random thresholds
            gas_threshold=40.0,
            temp_threshold=30.0,
            temp_spike_threshold=6.0,
            debounce_seconds=debounce_seconds,
            false_alarm_consecutive=false_alarm_consecutive
        )

        self.alert_sender = TwilioWhatsAppAlertSender()
        self.logger = DetectorLogger()  # dummy

        # Last values from MQTT (we build SensorReading when all 3 present)
        self.last_smoke: Optional[float] = None
        self.last_gas: Optional[float] = None
        self.last_temp: Optional[float] = None
        self.last_timestamp: Optional[float] = None

        self.client = mqtt.Client()
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def start(self):
        print(f"Connecting to MQTT broker {BROKER_HOST}:{BROKER_PORT} as detector...")
        self.client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
        self.client.loop_forever()

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        print("Connected to MQTT broker with result code", reason_code)
        client.subscribe(TOPIC_SMOKE)
        client.subscribe(TOPIC_GAS)
        client.subscribe(TOPIC_TEMP)
        print(f"Subscribed to topics: {TOPIC_SMOKE}, {TOPIC_GAS}, {TOPIC_TEMP}")

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            timestamp = payload.get("timestamp", time.time())
            value = float(payload.get("value"))
        except Exception as e:
            print(f"[ERROR] Failed to parse message on {topic}: {e}")
            return

        # Update the sens val
        if topic == TOPIC_SMOKE:
            self.last_smoke = value
        elif topic == TOPIC_GAS:
            self.last_gas = value
        elif topic == TOPIC_TEMP:
            self.last_temp = value

        # latest timestamp 
        self.last_timestamp = timestamp

        #  SensorReading:all three vals
        if self.last_smoke is not None and self.last_gas is not None and self.last_temp is not None:
            reading = SensorReading(
                timestamp=self.last_timestamp,
                smoke=self.last_smoke,
                gas=self.last_gas,
                temperature=self.last_temp
            )

            print(
                f"[READING] {time.strftime('%H:%M:%S', time.localtime(reading.timestamp))} | "
                f"Smoke={reading.smoke:5.1f}% | Gas={reading.gas:5.1f}% | "
                f"Temp={reading.temperature:5.1f}°C"
            )

            # reading the log file
            self.logger.log_reading(reading)

            alerts = self.detector.evaluate(reading)
            for alert in alerts:
                title = f"{alert['type']} {alert['level']}"
                body = (
                    alert["message"] + "\n\n" +
                    f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(reading.timestamp))}\n"
                    f"Smoke: {reading.smoke:.1f}%\n"
                    f"Gas: {reading.gas:.1f}%\n"
                    f"Temp: {reading.temperature:.1f}°C"
                )

                print(f"[ALERT] {title} - sending WhatsApp...")
                self.alert_sender.send_alert(title, body)
                self.logger.log_alert(reading, alert)


def main():
    service = MqttDetectorService()
    try:
        service.start()
    except KeyboardInterrupt:
        print("\nDetector service stopped.")


if __name__ == "__main__":
    main()
