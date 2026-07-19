# dect


import json
import os
import time

from typing import Optional

import paho.mqtt.client as mqtt

from dotenv import load_dotenv

try:
    import winsound
except ImportError:
    winsound = None

from detector import (
    SmokeGasFireDetector,
    SensorReading
)

from alerts import AlertSender

from logger import DetectorLogger


load_dotenv()

# ==========================================================
# MQTT CONFIG
# ==========================================================

BROKER_HOST = "test.mosquitto.org"
BROKER_PORT = 1883

TOPIC_SMOKE = "home/livingroom/smoke"
TOPIC_GAS = "home/livingroom/gas"
TOPIC_CO = "home/livingroom/co"
TOPIC_TEMP = "home/livingroom/temperature"

# ==========================================================
# DETECTOR SERVICE
# ==========================================================

class MqttDetectorService:

    def __init__(self):

        debounce_seconds = int(
            os.getenv(
                "ALERT_DEBOUNCE_SECONDS",
                "300"
            )
        )

        false_alarm_consecutive = int(
            os.getenv(
                "FALSE_ALARM_CONSECUTIVE",
                "3"
            )
        )

        self.detector = SmokeGasFireDetector(

            smoke_warning=40,
            smoke_critical=60,

            gas_warning=40,
            gas_critical=60,

            co_warning=35,
            co_critical=60,

            temp_warning=45,
            temp_critical=65,

            temp_spike_threshold=8,

            debounce_seconds=debounce_seconds,

            false_alarm_consecutive=
            false_alarm_consecutive
        )

        self.alert_sender = (
            AlertSender()
        )

        self.logger = (
            DetectorLogger()
        )

        self.last_smoke = None
        self.last_gas = None
        self.last_co = None
        self.last_temp = None

        self.last_timestamp = None

        self.client = mqtt.Client()

        self.client.on_connect = (
            self.on_connect
        )

        self.client.on_message = (
            self.on_message
        )

    # ======================================================
    # START
    # ======================================================

    def start(self):

        print()

        print(
            f"Connecting to MQTT broker "
            f"{BROKER_HOST}:{BROKER_PORT}"
        )

        self.client.connect(
            BROKER_HOST,
            BROKER_PORT,
            keepalive=60
        )

        self.client.loop_forever()

    # ======================================================
    # CONNECT
    # ======================================================

    def on_connect(
        self,
        client,
        userdata,
        flags,
        reason_code,
        properties=None
    ):

        print()

        print(
            "Connected to MQTT broker."
        )

        client.subscribe(
            TOPIC_SMOKE
        )

        client.subscribe(
            TOPIC_GAS
        )

        client.subscribe(
            TOPIC_CO
        )

        client.subscribe(
            TOPIC_TEMP
        )

        print()

        print("Subscribed Topics:")

        print(TOPIC_SMOKE)
        print(TOPIC_GAS)
        print(TOPIC_CO)
        print(TOPIC_TEMP)

    # ======================================================
    # MESSAGE
    # ======================================================

    def on_message(
        self,
        client,
        userdata,
        msg
    ):

        try:

            payload = json.loads(
                msg.payload.decode(
                    "utf-8"
                )
            )

            timestamp = payload.get(
                "timestamp",
                time.time()
            )

            value = float(
                payload.get(
                    "value"
                )
            )

        except Exception as e:

            print(
                "[ERROR]",
                e
            )

            return

        # ==========================================
        # UPDATE VALUES
        # ==========================================

        if msg.topic == TOPIC_SMOKE:

            self.last_smoke = value

        elif msg.topic == TOPIC_GAS:

            self.last_gas = value

        elif msg.topic == TOPIC_CO:

            self.last_co = value

        elif msg.topic == TOPIC_TEMP:

            self.last_temp = value

        self.last_timestamp = timestamp

        # ==========================================
        # WAIT FOR FULL DATA
        # ==========================================

        if (

            self.last_smoke is None

            or

            self.last_gas is None

            or

            self.last_co is None

            or

            self.last_temp is None

        ):

            return

        reading = SensorReading(

            timestamp=self.last_timestamp,

            smoke=self.last_smoke,

            gas=self.last_gas,

            co=self.last_co,

            temperature=self.last_temp
        )

        fire_index = (
            self.detector
            .calculate_fire_index(
                reading
            )
        )

        fire_probability = (
            self.detector
            .calculate_fire_probability(
                reading
            )
        )

        confidence = (
            self.detector
            .get_confidence(
                fire_probability
            )
        )

        # ==========================================
        # DISPLAY
        # ==========================================

        print()

        print("=" * 80)

        print(
            time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(
                    reading.timestamp
                )
            )
        )

        print()

        print(
            f"Smoke Risk : "
            f"{reading.smoke:.1f}%"
        )

        print(
            f"Gas Risk   : "
            f"{reading.gas:.1f}%"
        )

        print(
            f"CO Risk    : "
            f"{reading.co:.1f}%"
        )

        print(
            f"Temperature: "
            f"{reading.temperature:.1f}°C"
        )

        print()

        print(
            f"Fire Index       : "
            f"{fire_index:.1f}/100"
        )

        print(
            f"Fire Probability : "
            f"{fire_probability:.1f}%"
        )

        print(
            f"Confidence       : "
            f"{confidence}"
        )

        print("=" * 80)

        # ==========================================
        # LOG
        # ==========================================

        self.logger.log_reading(
            reading,fire_index,
    fire_probability,
    confidence
        )

        # ==========================================
        # ALERTS
        # ==========================================

        alerts = (
            self.detector.evaluate(
                reading
            )
        )

        for alert in alerts:

            title = (
                f"{alert['type']} "
                f"{alert['level']}"
            )

            body = f"""
Fire Index: {alert['fire_index']}

Fire Probability:
{alert['fire_probability']}%

Confidence:
{alert['confidence']}

Smoke Risk:
{reading.smoke:.1f}%

Gas Risk:
{reading.gas:.1f}%

CO Risk:
{reading.co:.1f}%

Temperature:
{reading.temperature:.1f}°C

{alert['message']}
"""

            # ==================================
            # FIRE SOUND
            # ==================================

            if alert["type"] == "FIRE":

                try:

                    if (
                        winsound
                        and
                        os.path.exists(
                            "alarm.wav"
                        )
                    ):

                        winsound.PlaySound(
                            "alarm.wav",
                            winsound.SND_FILENAME
                            |
                            winsound.SND_ASYNC
                        )

                    elif winsound:

                        winsound.Beep(
                            1500,
                            1200
                        )

                except Exception as e:

                    print(
                        "[AUDIO ERROR]",
                        e
                    )

            print()

            print(
                f"[ALERT] {title}"
            )

            self.alert_sender.send_alert(
                title,
                body
            )

            self.logger.log_alert(
                reading,
                alert
            )

# ==========================================================
# MAIN
# ==========================================================

def main():

    service = (
        MqttDetectorService()
    )

    try:

        service.start()

    except KeyboardInterrupt:

        print()

        print(
            "Detector stopped."
        )

# ==========================================================
# ENTRY
# ==========================================================

if __name__ == "__main__":

    main()
