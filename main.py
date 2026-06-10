# main
import os
import time

from dotenv import load_dotenv
from logger import DetectorLogger
from detector import SmokeGasFireDetector
from sensor_simulator import SensorSimulator
from alerts_twilio import TwilioWhatsAppAlertSender

load_dotenv()
logger = DetectorLogger()


def main():
    debounce_seconds = int(os.getenv("ALERT_DEBOUNCE_SECONDS", "300"))
    false_alarm_consecutive = int(os.getenv("FALSE_ALARM_CONSECUTIVE", "3"))
    read_interval = float(os.getenv("READ_INTERVAL_SECONDS", "2"))

    detector = SmokeGasFireDetector(
        smoke_threshold=5.0,
        gas_threshold=7.0,
        temp_threshold=20.0,
        temp_spike_threshold=10.0,
        debounce_seconds=debounce_seconds,
        false_alarm_consecutive=false_alarm_consecutive
    )

    simulator = SensorSimulator()
    alert_sender = TwilioWhatsAppAlertSender()

    print("Starting Smoke/Gas/Fire detection (simulation + WhatsApp alerts).")
    print(f"Debounce: {debounce_seconds}s, False-alarm consecutive: {false_alarm_consecutive}")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            reading = simulator.read()
            logger.log_reading(reading)


            print(
                f"[READING] {time.strftime('%H:%M:%S', time.localtime(reading.timestamp))} | "
                f"Smoke={reading.smoke:5.1f}% | Gas={reading.gas:5.1f}% | "
                f"Temp={reading.temperature:5.1f}°C"
            )

            alerts = detector.evaluate(reading)

            for alert in alerts:
                logger.log_alert(reading, alert)
                title = f"{alert['type']} {alert['level']}"
                body = (
                    alert["message"] + "\n\n" +
                    f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(reading.timestamp))}\n"
                    f"Smoke: {reading.smoke:.1f}%\n"
                    f"Gas: {reading.gas:.1f}%\n"
                    f"Temp: {reading.temperature:.1f}°C"
                )

                # WhatsApp alert Sending
                alert_sender.send_alert(title, body)

            time.sleep(read_interval)

    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
