# logger
import csv
import os
from typing import Dict
from detector import SensorReading


class DetectorLogger:
    def __init__(self, readings_file: str = "readings.csv", alerts_file: str = "alerts.csv"):
        self.readings_file = readings_file
        self.alerts_file = alerts_file
        if not os.path.exists(self.readings_file):
            with open(self.readings_file, mode="w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "smoke", "gas", "temperature"])

        if not os.path.exists(self.alerts_file):
            with open(self.alerts_file, mode="w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "type", "level", "message", "smoke", "gas", "temperature"])

    def log_reading(self, reading: SensorReading):
        with open(self.readings_file, mode="a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([reading.timestamp, reading.smoke, reading.gas, reading.temperature])

    def log_alert(self, reading: SensorReading, alert: Dict):
        with open(self.alerts_file, mode="a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                reading.timestamp,
                alert["type"],
                alert["level"],
                alert["message"],
                reading.smoke,
                reading.gas,
                reading.temperature
            ])
