import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Dict, List


# ==========================================================
# SENSOR READING
# ==========================================================

@dataclass
class SensorReading:
    timestamp: float

    smoke: float          # 0-100
    gas: float            # 0-100
    co: float             # 0-100
    temperature: float    # °C


# ==========================================================
# ALERT STATE
# ==========================================================

@dataclass
class AlertState:
    last_trigger_time: Optional[float] = None


# ==========================================================
# DETECTOR
# ==========================================================

class SmokeGasFireDetector:

    def __init__(
        self,

        smoke_warning=40,
        smoke_critical=60,

        gas_warning=40,
        gas_critical=60,

        co_warning=35,
        co_critical=60,

        temp_warning=45,
        temp_critical=65,

        temp_spike_threshold=8,

        debounce_seconds=300,
        false_alarm_consecutive=3,

        history_size=30
    ):

        self.smoke_warning = smoke_warning
        self.smoke_critical = smoke_critical

        self.gas_warning = gas_warning
        self.gas_critical = gas_critical

        self.co_warning = co_warning
        self.co_critical = co_critical

        self.temp_warning = temp_warning
        self.temp_critical = temp_critical

        self.temp_spike_threshold = temp_spike_threshold

        self.debounce_seconds = debounce_seconds
        self.false_alarm_consecutive = false_alarm_consecutive

        self.history = deque(maxlen=history_size)

        self.alert_states: Dict[str, AlertState] = {}

    # ======================================================
    # HISTORY
    # ======================================================

    def add_reading(self, reading):

        self.history.append(reading)

    # ======================================================
    # DEBOUNCE
    # ======================================================

    def _should_debounce(self, key, now):

        state = self.alert_states.get(key)

        if state is None:
            return False

        if state.last_trigger_time is None:
            return False

        return (
            now - state.last_trigger_time
        ) < self.debounce_seconds

    def _mark_alert_sent(self, key, now):

        if key not in self.alert_states:

            self.alert_states[key] = (
                AlertState(now)
            )

        else:

            self.alert_states[key].last_trigger_time = now

    # ======================================================
    # FALSE ALARM FILTER
    # ======================================================

    def _require_consecutive(
        self,
        predicate,
        count=None
    ):

        if count is None:
            count = self.false_alarm_consecutive

        if len(self.history) < count:
            return False

        recent = list(self.history)[-count:]

        return all(
            predicate(r)
            for r in recent
        )

    # ======================================================
    # FIRE INDEX
    # ======================================================

    def calculate_fire_index(
        self,
        reading
    ):

        temp_score = max(
            0,
            min(
                100,
                (reading.temperature - 25) * 2
            )
        )

        fire_index = (

            0.35 * reading.smoke +

            0.25 * reading.gas +

            0.25 * reading.co +

            0.15 * temp_score

        )

        return round(
            min(100, fire_index),
            2
        )

    # ======================================================
    # TEMPERATURE SPIKE
    # ======================================================

    def detect_temp_spike(self):

        if len(self.history) < 2:
            return False

        latest = self.history[-1]
        previous = self.history[-2]

        rise = (
            latest.temperature -
            previous.temperature
        )

        return (
            rise >=
            self.temp_spike_threshold
        )

    # ======================================================
    # FIRE PROBABILITY
    # ======================================================

    def calculate_fire_probability(
        self,
        reading
    ):

        probability = 0

        probability += (
            reading.smoke * 0.30
        )

        probability += (
            reading.gas * 0.20
        )

        probability += (
            reading.co * 0.30
        )

        if reading.temperature > 40:

            probability += min(
                20,
                (
                    reading.temperature - 40
                )
            )

        if self.detect_temp_spike():

            probability += 15

        return round(
            min(100, probability),
            1
        )

    # ======================================================
    # CONFIDENCE
    # ======================================================

    def get_confidence(
        self,
        probability
    ):

        if probability >= 85:
            return "HIGH"

        if probability >= 60:
            return "MEDIUM"

        return "LOW"

    # ======================================================
    # CREATE ALERT
    # ======================================================

    def build_alert(
        self,
        alert_type,
        level,
        reading,
        message
    ):

        fire_index = (
            self.calculate_fire_index(
                reading
            )
        )

        fire_probability = (
            self.calculate_fire_probability(
                reading
            )
        )

        confidence = (
            self.get_confidence(
                fire_probability
            )
        )

        return {

            "type": alert_type,

            "level": level,

            "message": message,

            "fire_index": fire_index,

            "fire_probability": fire_probability,

            "confidence": confidence
        }

    # ======================================================
    # MAIN EVALUATION
    # ======================================================

    def evaluate(
        self,
        reading
    ):

        self.add_reading(reading)

        alerts = []

        now = reading.timestamp

        fire_index = (
            self.calculate_fire_index(
                reading
            )
        )

        fire_probability = (
            self.calculate_fire_probability(
                reading
            )
        )

        # --------------------------------------------------
        # SMOKE
        # --------------------------------------------------

        if (
            reading.smoke >=
            self.smoke_critical
        ):

            if self._require_consecutive(
                lambda r:
                r.smoke >= self.smoke_critical
            ):

                key = "SMOKE_CRITICAL"

                if not self._should_debounce(
                    key,
                    now
                ):

                    alerts.append(

                        self.build_alert(

                            "SMOKE",

                            "CRITICAL",

                            reading,

                            f"Smoke level critical ({reading.smoke:.1f}%)"

                        )

                    )

                    self._mark_alert_sent(
                        key,
                        now
                    )

        elif (
            reading.smoke >=
            self.smoke_warning
        ):

            if self._require_consecutive(
                lambda r:
                r.smoke >= self.smoke_warning
            ):

                key = "SMOKE_WARNING"

                if not self._should_debounce(
                    key,
                    now
                ):

                    alerts.append(

                        self.build_alert(

                            "SMOKE",

                            "WARNING",

                            reading,

                            f"Smoke level elevated ({reading.smoke:.1f}%)"

                        )

                    )

                    self._mark_alert_sent(
                        key,
                        now
                    )

        # --------------------------------------------------
        # GAS
        # --------------------------------------------------

        if (
            reading.gas >=
            self.gas_critical
        ):

            if self._require_consecutive(
                lambda r:
                r.gas >= self.gas_critical
            ):

                key = "GAS_CRITICAL"

                if not self._should_debounce(
                    key,
                    now
                ):

                    alerts.append(

                        self.build_alert(

                            "GAS",

                            "CRITICAL",

                            reading,

                            f"Gas level critical ({reading.gas:.1f}%)"

                        )

                    )

                    self._mark_alert_sent(
                        key,
                        now
                    )

        # --------------------------------------------------
        # CO
        # --------------------------------------------------

        if (
            reading.co >=
            self.co_critical
        ):

            if self._require_consecutive(
                lambda r:
                r.co >= self.co_critical
            ):

                key = "CO_CRITICAL"

                if not self._should_debounce(
                    key,
                    now
                ):

                    alerts.append(

                        self.build_alert(

                            "CO",

                            "CRITICAL",

                            reading,

                            f"Carbon monoxide elevated ({reading.co:.1f}%)"

                        )

                    )

                    self._mark_alert_sent(
                        key,
                        now
                    )

        # --------------------------------------------------
        # FIRE DETECTION
        # --------------------------------------------------

        fire_confirmed = (

            fire_probability >= 75

            and

            (
                reading.smoke >=
                self.smoke_warning
            )

            and

            (
                reading.temperature >=
                self.temp_warning

                or

                self.detect_temp_spike()
            )
        )

        if fire_confirmed:

            key = "FIRE_CRITICAL"

            if not self._should_debounce(
                key,
                now
            ):

                alerts.append(

                    self.build_alert(

                        "FIRE",

                        "CRITICAL",

                        reading,

                        "Likely active fire detected using multi-sensor fusion."

                    )

                )

                self._mark_alert_sent(
                    key,
                    now
                )

        # --------------------------------------------------
        # EARLY FIRE WARNING
        # --------------------------------------------------

        elif fire_index >= 55:

            key = "FIRE_WARNING"

            if not self._should_debounce(
                key,
                now
            ):

                alerts.append(

                    self.build_alert(

                        "FIRE",

                        "WARNING",

                        reading,

                        "Elevated fire risk detected."

                    )

                )

                self._mark_alert_sent(
                    key,
                    now
                )

        return alerts
