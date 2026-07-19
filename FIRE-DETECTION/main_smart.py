# main_smart.py

import subprocess
import sys
import time
import threading
import random

from core.models import (
    SensorReading, Event, EventType, AlertSeverity, HomeMode
)
from core.event_bus import get_event_bus
from core.home_state import get_home_state
from core.automation_engine import get_automation_engine
from core.smart_detector import get_smart_detector
from core.security_module import get_security_module


# ==========================================================
# SMART HOME ORCHESTRATOR
# ==========================================================

class SmartHomeOrchestrator:
    """
    Main orchestrator that coordinates all smart home modules.
    """
    
    def __init__(self):
        print("\n" + "=" * 70)
        print("SMART HOME SYSTEM - INITIALIZING")
        print("=" * 70 + "\n")
        
        # Initialize core systems
        self.event_bus = get_event_bus()
        self.home_state = get_home_state()
        self.automation_engine = get_automation_engine()
        self.detector = get_smart_detector()
        self.security = get_security_module()
        
        # Simulation state
        self.running = False
        self.simulation_thread = None
        
        print("\n[SmartHome] All systems initialized")
        print(f"[SmartHome] Home mode: {self.home_state.get_mode().value}")
        print(f"[SmartHome] Rooms: {list(self.home_state.get_state().rooms.keys())}")
        print()
    
    def start(self):
        """Start the smart home system."""
        print("\n" + "=" * 70)
        print("SMART HOME SYSTEM - RUNNING")
        print("=" * 70)
        print("\nPress CTRL+C to stop\n")
        
        self.running = True
        
        # Start simulation in background
        self.simulation_thread = threading.Thread(
            target=self._simulation_loop,
            daemon=True
        )
        self.simulation_thread.start()
        
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()
    
    def stop(self):
        """Stop the smart home system."""
        print("\n\nStopping Smart Home System...")
        self.running = False
        if self.simulation_thread:
            self.simulation_thread.join(timeout=2)
        print("System stopped.\n")
    
    # ==========================================================
    # SIMULATION
    # ==========================================================
    
    def _simulation_loop(self):
        """Main simulation loop that generates events."""
        rooms = ["kitchen", "living_room", "bedroom", "entry"]
        
        while self.running:
            # Generate sensor readings for each room
            for room in rooms:
                reading = self._generate_reading(room)
                self.detector.process_reading(reading)
            
            # Occasionally simulate security events
            if random.random() < 0.1:  # 10% chance
                self._simulate_security_event()
            
            # Occasionally change home mode for demo
            if random.random() < 0.02:  # 2% chance
                self._simulate_mode_change()
            
            time.sleep(3)  # Reading interval
    
    def _generate_reading(self, room: str) -> SensorReading:
        """Generate a simulated sensor reading."""
        scenario = self._pick_scenario()
        
        if scenario == "normal":
            smoke = random.uniform(5, 20)
            gas = random.uniform(5, 15)
            co = random.uniform(2, 12)
            temp = random.uniform(24, 32)
        
        elif scenario == "cooking":
            smoke = random.uniform(30, 55)
            gas = random.uniform(15, 35)
            co = random.uniform(10, 25)
            temp = random.uniform(28, 42)
        
        elif scenario == "gas_leak":
            smoke = random.uniform(5, 20)
            gas = random.uniform(55, 85)
            co = random.uniform(10, 30)
            temp = random.uniform(24, 32)
        
        elif scenario == "smoldering":
            smoke = random.uniform(45, 70)
            gas = random.uniform(40, 60)
            co = random.uniform(35, 55)
            temp = random.uniform(38, 52)
        
        elif scenario == "fire":
            smoke = random.uniform(70, 95)
            gas = random.uniform(60, 85)
            co = random.uniform(55, 80)
            temp = random.uniform(55, 85)
        
        else:  # developed fire
            smoke = random.uniform(85, 100)
            gas = random.uniform(80, 100)
            co = random.uniform(75, 100)
            temp = random.uniform(75, 120)
        
        # Kitchen adjustments during meal time
        hour = time.localtime().tm_hour
        if room == "kitchen" and hour in [7, 8, 12, 13, 18, 19, 20]:
            smoke = min(100, smoke * 1.3)
            temp = min(120, temp * 1.1)
        
        return SensorReading(
            timestamp=time.time(),
            room=room,
            smoke=smoke,
            gas=gas,
            co=co,
            temperature=temp,
            humidity=random.uniform(40, 70),
            aqi=random.randint(30, 150),
            motion=random.random() < 0.3,
            occupancy_count=random.randint(0, 2) if random.random() < 0.5 else 0,
            power_usage=random.uniform(50, 500),
            source="simulation"
        )
    
    def _pick_scenario(self) -> str:
        """Pick a simulation scenario."""
        scenarios = [
            "normal",
            "cooking",
            "gas_leak",
            "smoldering",
            "fire",
            "developed_fire"
        ]
        weights = [65, 18, 6, 5, 4, 2]
        
        return random.choices(scenarios, weights=weights, k=1)[0]
    
    def _simulate_security_event(self):
        """Simulate a security event."""
        event_type = random.choice(["motion", "face_known", "face_unknown"])
        room = random.choice(["entry", "living_room"])
        
        if event_type == "motion":
            event = self.security.process_motion(room)
            if event:
                self.event_bus.publish(event)
        
        elif event_type == "face_known":
            event = self.security.process_face_recognition(
                room=room,
                face_id="Naman",
                confidence=0.95
            )
            self.event_bus.publish(event)
        
        else:
            event = self.security.process_face_recognition(
                room=room,
                face_id=None,
                confidence=0.88
            )
            self.event_bus.publish(event)
    
    def _simulate_mode_change(self):
        """Simulate a mode change."""
        current = self.home_state.get_mode()
        modes = [m for m in HomeMode if m != current and m != HomeMode.EMERGENCY]
        
        if modes:
            new_mode = random.choice(modes)
            self.home_state.set_mode(new_mode)


# ==========================================================
# DEMO SCENARIOS
# ==========================================================

def demo_fire_scenario():
    """Demonstrate fire detection and response."""
    print("\n" + "=" * 70)
    print("DEMO: FIRE SCENARIO")
    print("=" * 70 + "\n")
    
    orchestrator = SmartHomeOrchestrator()
    detector = orchestrator.detector
    event_bus = orchestrator.event_bus
    
    # Simulate escalating fire
    readings = [
        SensorReading(
            timestamp=time.time(),
            room="kitchen",
            smoke=25, gas=15, co=10, temperature=32
        ),
        SensorReading(
            timestamp=time.time() + 3,
            room="kitchen",
            smoke=45, gas=30, co=25, temperature=40
        ),
        SensorReading(
            timestamp=time.time() + 6,
            room="kitchen",
            smoke=65, gas=50, co=45, temperature=52
        ),
        SensorReading(
            timestamp=time.time() + 9,
            room="kitchen",
            smoke=80, gas=65, co=60, temperature=65
        ),
    ]
    
    for reading in readings:
        print(f"\n--- Reading: smoke={reading.smoke:.0f}, "
              f"temp={reading.temperature:.0f}°C ---")
        events = detector.process_reading(reading)
        time.sleep(1)
    
    print("\n" + "=" * 70)
    print("Demo complete. Check the automation responses above.")
    print("=" * 70 + "\n")


def demo_intruder_scenario():
    """Demonstrate intruder detection and response."""
    print("\n" + "=" * 70)
    print("DEMO: INTRUDER SCENARIO (AWAY MODE)")
    print("=" * 70 + "\n")
    
    orchestrator = SmartHomeOrchestrator()
    home_state = orchestrator.home_state
    security = orchestrator.security
    event_bus = orchestrator.event_bus
    
    # Set home to away mode
    print("Setting home to AWAY mode...")
    home_state.set_mode(HomeMode.AWAY)
    time.sleep(1)
    
    # Simulate motion
    print("\nMotion detected at entry...")
    motion_event = security.process_motion("entry")
    if motion_event:
        event_bus.publish(motion_event)
    time.sleep(1)
    
    # Simulate unknown face
    print("\nUnknown face detected...")
    face_event = security.process_face_recognition(
        room="entry",
        face_id=None,
        confidence=0.92
    )
    event_bus.publish(face_event)
    
    print("\n" + "=" * 70)
    print("Demo complete. Check the automation responses above.")
    print("=" * 70 + "\n")


# ==========================================================
# MAIN
# ==========================================================

def main():
    import sys
    
    if len(sys.argv) > 1:
        command = sys.argv[1]
        
        if command == "demo-fire":
            demo_fire_scenario()
        elif command == "demo-intruder":
            demo_intruder_scenario()
        else:
            print(f"Unknown command: {command}")
            print("Available: demo-fire, demo-intruder")
    else:
        # Normal operation
        orchestrator = SmartHomeOrchestrator()
        orchestrator.start()


if __name__ == "__main__":
    main()
