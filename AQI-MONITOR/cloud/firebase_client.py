# cloud/firebase_client.py

import firebase_admin
from firebase_admin import credentials, db
import os
import streamlit as st
from dotenv import load_dotenv

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
    ref = db.reference("readings")
    latest = ref.order_by_key().limit_to_last(1).get()
    if latest:
        return list(latest.values())[0]
    return None