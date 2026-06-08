#publisher
import time
import json
import random

import paho.mqtt.client as mqtt


BROKER_HOST = "test.mosquitto.org"
BROKER_PORT = 1883
TOPIC_SMOKE = "home/livingroom/smoke"
TOPIC_GAS = "home/livingroom/gas"
TOPIC_TEMP = "home/livingroom/temperature"

# Demo-friendly base ranges and spike probability
SMOKE_BASE = (0, 20)
GAS_BASE = (0, 20)
TEMP_BASE = (25, 35)
SPIKE_PROBABILITY = 0.3  # 30% chance of an incident
READ_INTERVAL_SECONDS = 2


def generate_reading():
    """Generate one fake sensor reading (similar to SensorSimulator)."""
    timestamp = time.time()

    smoke = random.uniform(*SMOKE_BASE)
    gas = random.uniform(*GAS_BASE)
    temperature = random.uniform(*TEMP_BASE)

    # adding spoke
    if random.random() < SPIKE_PROBABILITY:
        which = random.choice(["smoke", "gas", "temp"])
        if which == "smoke":
            smoke = random.uniform(70, 100)
        elif which == "gas":
            gas = random.uniform(70, 100)
        else:
            temperature = random.uniform(65, 90)

    return timestamp, smoke, gas, temperature


def main():
    client = mqtt.Client()

    print(f"Connecting to MQTT broker {BROKER_HOST}:{BROKER_PORT} as publisher...")
    client.connect(BROKER_HOST, BROKER_PORT, keepalive=60)
    client.loop_start()

    try:
        while True:
            timestamp, smoke, gas, temperature = generate_reading()

            # send JSON payloads (could also just send raw numbers but i's more flexi) 
            payload_smoke = json.dumps({"timestamp": timestamp, "value": smoke})
            payload_gas = json.dumps({"timestamp": timestamp, "value": gas})
            payload_temp = json.dumps({"timestamp": timestamp, "value": temperature})

            client.publish(TOPIC_SMOKE, payload_smoke)
            client.publish(TOPIC_GAS, payload_gas)
            client.publish(TOPIC_TEMP, payload_temp)

            print(
                f"[PUBLISH] {time.strftime('%H:%M:%S', time.localtime(timestamp))} | "
                f"Smoke={smoke:5.1f}% | Gas={gas:5.1f}% | Temp={temperature:5.1f}°C"
            )

            time.sleep(READ_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        print("\nPublisher stopped.")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
