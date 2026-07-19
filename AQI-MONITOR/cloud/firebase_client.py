# cloud/firebase_client.py

import firebase_admin
from firebase_admin import credentials, db
import os
import streamlit as st
from dotenv import load_dotenv
from datetime import datetime
from collections import defaultdict

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

FIREBASE_URL = os.getenv("FIREBASE_URL")


def get_credentials():
    try:
        key_dict = dict(st.secrets["firebase"])
        key_dict["private_key"] = key_dict["private_key"].replace("\\n", "\n")
        return credentials.Certificate(key_dict)
    except:
        return credentials.Certificate(os.path.join(BASE_DIR, "firebase_key.json"))


def get_firebase_url():
    try:
        return st.secrets["FIREBASE_URL"]
    except:
        return FIREBASE_URL


if not firebase_admin._apps:
    cred = get_credentials()
    firebase_admin.initialize_app(cred, {
        "databaseURL": get_firebase_url()
    })


def save_reading(data: dict):
    ref = db.reference("readings")
    key = data["timestamp"].replace(":", "-").replace(".", "-")
    ref.child(key).set(data)
    print(f"Saved to Firebase → AQI: {data['aqi']} ({data['category']})")


def get_latest_reading():
    ref    = db.reference("readings")
    latest = ref.order_by_key().limit_to_last(1).get()
    if latest:
        return list(latest.values())[0]
    return None


def register_email(email: str):
    ref = db.reference("registered_emails")
    key = email.replace(".", ",")
    ref.child(key).set({"email": email})
    print(f"Registered email: {email}")


def get_registered_emails() -> list:
    ref  = db.reference("registered_emails")
    data = ref.get()
    if not data:
        return []
    return [v["email"] for v in data.values()]


# ── Alert flag functions (Firebase mein store — restart pe persist karta hai) ──

def get_alert_status() -> dict:
    """Firebase se current alert status fetch karta hai."""
    ref  = db.reference("alert_status")
    data = ref.get()
    if not data:
        return {"is_sent": False, "last_aqi": 0}
    return data


def set_alert_sent(aqi: int, category: str):
    """Alert bhejne ke baad Firebase mein flag set karo."""
    ref = db.reference("alert_status")
    ref.set({
        "is_sent":   True,
        "last_aqi":  aqi,
        "category":  category,
        "sent_at":   datetime.now().isoformat()
    })
    print(f"Alert flag set in Firebase → AQI: {aqi}")


def reset_alert_flag():
    """AQI safe level pe aane ke baad flag reset karo."""
    ref = db.reference("alert_status")
    ref.set({
        "is_sent":   False,
        "last_aqi":  0,
        "reset_at":  datetime.now().isoformat()
    })
    print("Alert flag reset in Firebase")


# ── 7 din ka real Firebase history LSTM ke liye ──

def get_last_7_days_avg() -> list:
    """
    Firebase se last 7 din ki average readings fetch karta hai.
    Har din ke liye ek average reading return karta hai.
    LSTM input ke liye use hota hai.
    """
    ref      = db.reference("readings")
    all_data = ref.order_by_key().limit_to_last(10080).get()
    # 10080 = 7 days × 24 hours × 60 minutes

    if not all_data:
        return None

    readings = list(all_data.values())

    # Group readings by date
    daily = defaultdict(list)
    for r in readings:
        try:
            date = r["timestamp"][:10]  # "2026-05-26T20:29:54" → "2026-05-26"
            daily[date].append(r)
        except:
            continue

    # Last 7 days sort karo
    sorted_days = sorted(daily.keys())[-7:]

    if len(sorted_days) < 7:
        return None  # abhi enough data nahi hai

    # Har din ka average nikalo
    result = []
    for day in sorted_days:
        day_readings = daily[day]
        avg = {
            "pm25": round(sum(r.get("pm25", 0) for r in day_readings) / len(day_readings), 2),
            "pm10": round(sum(r.get("pm10", 0) for r in day_readings) / len(day_readings), 2),
            "no2":  20,
            "co":   1.5,
            "o3":   30,
            "aqi":  round(sum(r.get("aqi", 0) for r in day_readings) / len(day_readings)),
        }
        result.append(avg)

    return result
