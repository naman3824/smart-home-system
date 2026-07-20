"""
Shared pytest fixtures.

Each test gets its own throwaway SQLite file rather than touching the real
smarthome.db — db.DB_PATH is monkeypatched directly (it's already been read
from the environment at import time, so setting the env var alone wouldn't
do anything once db.py is imported).
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import db


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """A fresh, initialized SQLite database for a single test."""
    db_file = tmp_path / "test_smarthome.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_file))
    db.init_db()
    return db


@pytest.fixture
def sample_devices():
    """A minimal but realistic devices dict, matching server.py's shape,
    for tests that exercise automation.py without booting the full server."""
    return {
        "living_room": {
            "light": {"on": True, "brightness": 80, "watts": 12},
            "fan": {"on": False, "speed": 0, "watts": 45},
            "ac": {"on": False, "temp": 24, "mode": "cool", "watts": 1500},
            "air_purifier": {"on": False, "speed": 2, "watts": 50},
        },
        "kitchen": {
            "light": {"on": True, "brightness": 100, "watts": 15},
            "exhaust": {"on": False, "watts": 30},
        },
        "security": {
            "door_lock": {"on": True, "watts": 0},
            "siren": {"on": False, "watts": 10},
            "mains_power": {"on": True, "watts": 0},
        },
    }


@pytest.fixture
def sample_sensors():
    return {"aqi": 142, "aqi_category": "Moderate", "temperature": 26,
            "humidity": 55, "smoke": 2.0, "gas": 1.5, "co2_ppm": 480}


@pytest.fixture
def sample_family():
    return [
        {"id": 1, "name": "Aditya", "status": "home"},
        {"id": 2, "name": "Naman", "status": "away"},
    ]
