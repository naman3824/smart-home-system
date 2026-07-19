# logger.py

import csv
import os
import time

from detector import SensorReading


class DetectorLogger:

    def __init__(
        self,
        readings_file="readings.csv",
        alerts_file="alerts.csv"
    ):

        self.readings_file = readings_file
        self.alerts_file = alerts_file

        self._initialize_files()

    # ======================================================
    # CREATE FILES
    # ======================================================

    def _initialize_files(self):

        if not os.path.exists(
            self.readings_file
        ):

            with open(
                self.readings_file,
                "w",
                newline=""
            ) as f:

                writer = csv.writer(f)

                writer.writerow([

                    "timestamp",

                    "datetime",

                    "smoke_risk",

                    "gas_risk",

                    "co_risk",

                    "temperature",

                    "fire_index",

                    "fire_probability",

                    "confidence"

                ])

        if not os.path.exists(
            self.alerts_file
        ):

            with open(
                self.alerts_file,
                "w",
                newline=""
            ) as f:

                writer = csv.writer(f)

                writer.writerow([

                    "timestamp",

                    "datetime",

                    "alert_type",

                    "level",

                    "fire_index",

                    "fire_probability",

                    "confidence",

                    "message",

                    "smoke_risk",

                    "gas_risk",

                    "co_risk",

                    "temperature"

                ])

    # ======================================================
    # LOG SENSOR READING
    # ======================================================

    def log_reading(
        self,
        reading: SensorReading,
        fire_index=None,
        fire_probability=None,
        confidence=None
    ):

        dt = time.strftime(

            "%Y-%m-%d %H:%M:%S",

            time.localtime(
                reading.timestamp
            )
        )

        with open(
            self.readings_file,
            "a",
            newline=""
        ) as f:

            writer = csv.writer(f)

            writer.writerow([

                reading.timestamp,

                dt,

                round(
                    reading.smoke,
                    2
                ),

                round(
                    reading.gas,
                    2
                ),

                round(
                    reading.co,
                    2
                ),

                round(
                    reading.temperature,
                    2
                ),

                fire_index,

                fire_probability,

                confidence

            ])

    # ======================================================
    # LOG ALERT
    # ======================================================

    def log_alert(
        self,
        reading: SensorReading,
        alert: dict
    ):

        dt = time.strftime(

            "%Y-%m-%d %H:%M:%S",

            time.localtime(
                reading.timestamp
            )
        )

        with open(
            self.alerts_file,
            "a",
            newline=""
        ) as f:

            writer = csv.writer(f)

            writer.writerow([

                reading.timestamp,

                dt,

                alert.get(
                    "type",
                    ""
                ),

                alert.get(
                    "level",
                    ""
                ),

                alert.get(
                    "fire_index",
                    ""
                ),

                alert.get(
                    "fire_probability",
                    ""
                ),

                alert.get(
                    "confidence",
                    ""
                ),

                alert.get(
                    "message",
                    ""
                ),

                round(
                    reading.smoke,
                    2
                ),

                round(
                    reading.gas,
                    2
                ),

                round(
                    reading.co,
                    2
                ),

                round(
                    reading.temperature,
                    2
                )

            ])

    # ======================================================
    # CONSOLE SUMMARY
    # ======================================================

    def print_summary(

        self,

        reading,

        fire_index,

        fire_probability,

        confidence

    ):

        print()

        print("=" * 70)

        print("SMART HOME FIRE DETECTION SUMMARY")

        print("=" * 70)

        print(
            f"Smoke Risk      : "
            f"{reading.smoke:.1f}%"
        )

        print(
            f"Gas Risk        : "
            f"{reading.gas:.1f}%"
        )

        print(
            f"CO Risk         : "
            f"{reading.co:.1f}%"
        )

        print(
            f"Temperature     : "
            f"{reading.temperature:.1f}°C"
        )

        print()

        print(
            f"Fire Index      : "
            f"{fire_index:.1f}/100"
        )

        print(
            f"Fire Probability: "
            f"{fire_probability:.1f}%"
        )

        print(
            f"Confidence      : "
            f"{confidence}"
        )

        print("=" * 70)
