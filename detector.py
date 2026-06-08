# detector
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Dict, Callable


@dataclass
class SensorReading:
    timestamp: float
    smoke: float          # 0-100
    gas: float            # 0-100
    temperature: float    # Celsius


@dataclass
class AlertState:
    last_trigger_time: Optional[float] = None


class SmokeGasFireDetector:
    def __init__(
        self,
        smoke_threshold: float = 60.0,
        gas_threshold: float = 60.0,
        temp_threshold: float = 60.0,
        temp_spike_threshold: float = 10.0,
        debounce_seconds: int = 300,
        false_alarm_consecutive: int = 3,
        history_size: int = 10
    ):
        self.smoke_threshold = smoke_threshold
        self.gas_threshold = gas_threshold
        self.temp_threshold = temp_threshold
        self.temp_spike_threshold = temp_spike_threshold
        self.debounce_seconds = debounce_seconds
        self.false_alarm_consecutive = false_alarm_consecutive
        self.history = deque(maxlen=history_size)

        self.alert_states: Dict[str, AlertState] = {
            "SMOKE": AlertState(),
            "GAS": AlertState(),
            "FIRE": AlertState(),
        }

    def add_reading(self, reading: SensorReading):
        self.history.append(reading)

    def _should_debounce(self, alert_type: str, now: float) -> bool:
        state = self.alert_states[alert_type]
        if state.last_trigger_time is None:
            return False
        return (now - state.last_trigger_time) < self.debounce_seconds

    def _mark_alert_sent(self, alert_type: str, now: float):
        self.alert_states[alert_type].last_trigger_time = now

    def _is_false_alarm_filtered(self, predicate: Callable[[SensorReading], bool]) -> bool:
        if len(self.history) < self.false_alarm_consecutive:
            return True
        recent = list(self.history)[-self.false_alarm_consecutive:]
        return not all(predicate(r) for r in recent)

    def evaluate(self, reading: SensorReading):
        self.add_reading(reading)
        alerts = []
        now = reading.timestamp

        def smoke_critical(r: SensorReading) -> bool:
            return r.smoke >= self.smoke_threshold

        def gas_critical(r: SensorReading) -> bool:
            return r.gas >= self.gas_threshold

        def fire_critical(r: SensorReading) -> bool:
            if r.temperature >= self.temp_threshold:
                return True
            if len(self.history) >= 2:
                prev = self.history[-2]
                return (r.temperature - prev.temperature) >= self.temp_spike_threshold
            return False

        # Smoke alert
        if smoke_critical(reading):
            if not self._is_false_alarm_filtered(smoke_critical):
                if not self._should_debounce("SMOKE", now):
                    alerts.append({
                        "type": "SMOKE",
                        "level": "CRITICAL",
                        "message": (
                            f"SMOKE ALERT: Smoke {reading.smoke:.1f}% >= "
                            f"{self.smoke_threshold:.1f}%."
                        )
                    })
                    self._mark_alert_sent("SMOKE", now)

        # Gas alert
        if gas_critical(reading):
            if not self._is_false_alarm_filtered(gas_critical):
                if not self._should_debounce("GAS", now):
                    alerts.append({
                        "type": "GAS",
                        "level": "CRITICAL",
                        "message": (
                            f"GAS LEAK ALERT: Gas {reading.gas:.1f}% >= "
                            f"{self.gas_threshold:.1f}%."
                        )
                    })
                    self._mark_alert_sent("GAS", now)

        # Fire alert
        if fire_critical(reading):
            if not self._is_false_alarm_filtered(fire_critical):
                if not self._should_debounce("FIRE", now):
                    alerts.append({
                        "type": "FIRE",
                        "level": "CRITICAL",
                        "message": (
                            f"FIRE ALERT: Temp {reading.temperature:.1f}°C >= "
                            f"{self.temp_threshold:.1f}°C or sudden spike."
                        )
                    })
                    self._mark_alert_sent("FIRE", now)

        return alerts
