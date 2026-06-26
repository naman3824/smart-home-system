<div align="center">

# 🏠 Smart Home Dashboard

**A full-stack IoT smart home monitoring & control system**

Built with FastAPI · Vanilla JS · face-api.js · SVG Floor Plan · WebSocket

[![Python](https://img.shields.io/badge/Python-3.9%2B-blue?logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

*BTech Computer Science team project — SRM IST Delhi-NCR (2024–2028)*

</div>

---

## Overview

Smart Home Dashboard is a real-time home automation system that integrates five independent hardware/software modules into a unified web dashboard — no app install required. It runs entirely in the browser via a single FastAPI server.

| Module | Team Member | What it does |
|--------|------------|--------------|
| Face Recognition | Aditya | Browser-based face ID via face-api.js + webcam |
| Smoke / Gas / Fire | Agrim | Real-time detector with threshold alerts & MQTT |
| AQI Monitor | Naman | PM2.5/PM10 CPCB-formula AQI + LSTM forecasting |
| Climate Control | Kamakshi | Smart HVAC decisions from live weather data |
| Dashboard & Integration | Diksha | Single-page frontend, API layer, WebSocket |

---

## Features

### 🗺 Interactive Floor Plan
- SVG floor plan of the house with clickable rooms
- Real ceiling fan animations (speed-controlled)
- Ambient room glow when devices are active (amber = light, cyan = AC, blue = fan)
- Occupancy dots showing which members are home
- Entrance pulse on face recognition events

### 💡 Device Control
- Per-room control: lights (brightness), fans (speed 1–5), AC (temp + mode), TV, exhaust
- Scene shortcuts: Morning / Away / Movie / Good Night / All Off
- Live wattage calculation per device and per room

### 📊 Monitoring Tabs
- **Climate** — temperature, humidity, HVAC mode, history charts
- **Air Quality** — AQI gauge, PM2.5/PM10/CO₂, health advisory, 7-day forecast
- **Energy** — live watts, daily kWh, ₹ cost, per-room breakdown, device list
- **Safety** — smoke %, gas %, temperature gauges with threshold alerts and chart history

### 🔒 Security
- Face recognition via webcam (face-api.js, runs in-browser — no Python ML dependencies needed for this)
- Security log with filter by type: members / guests / deliveries / intruders
- Door lock toggle
- Browser notifications + audio siren on intruder detection

### 🧪 Simulations Tab
- Trigger any alert pipeline with one click
- Smoke/gas/fire spikes appear on the Safety charts with **SIM** markers
- Face recognition simulations (all 5 members + unknown intruder)
- Scene activations — all logged with timestamps

---

## Quick Start

### 1. Clone the repository
```bash
git clone https://github.com/yourusername/smart-home-dashboard.git
cd smart-home-dashboard
```

### 2. Install core dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure environment (optional)
```bash
cp .env.example .env
# Edit .env and fill in API keys for Twilio / OpenWeather etc.
# The dashboard runs fully without these — they enable real alerts/weather
```

### 4. Add face photos
Place reference photos in the `images/` folder:
```
images/
├── Aditya.jpeg
├── Diksha.jpeg
├── Agrim.jpeg
├── Naman.jpeg
└── Kamakshi.jpeg
```
> **Note:** Photos are excluded from git (`.gitignore`) to protect privacy.  
> The in-browser face recognition loads these at `/images/*.jpeg` served by FastAPI.

### 5. Run the server
```bash
python server.py
```

Open **http://localhost:8000** in Chrome or Edge.

---

## Project Structure

```
smart-home-dashboard/
│
├── server.py                    ← Main FastAPI server — run this
├── requirements.txt             ← Core dependencies
├── requirements-full.txt        ← All modules (optional)
├── .env.example                 ← Environment variable template
│
├── static/
│   └── index.html               ← Complete single-file frontend
│
├── images/                      ← Reference face photos (gitignored)
│   └── .gitkeep
│
├── FaceRecognition.py           ← Standalone Python face recognition (OpenCV/dlib)
├── detector.py                  ← Smoke/gas/fire detection class
├── logger.py                    ← CSV sensor data logger
├── main.py                      ← Smoke + WhatsApp alert entry point
├── mqtt_publisher.py            ← MQTT sensor data publisher
├── mqtt_detector.py             ← MQTT sensor subscriber + alert evaluator
├── alerts_twilio.py             ← Twilio WhatsApp/SMS alert sender
│
├── climate-control/
│   ├── api_server.py            ← Flask climate server (OpenWeather + Blynk)
│   └── climate_sensor.py        ← Sensor data simulator
│
└── AQI-MONITOR/
    ├── dashboard/app.py         ← Streamlit AQI dashboard (port 8501)
    ├── hardware/                ← AQI calculator + sensor simulator
    ├── ml/                      ← LSTM model training, inference, chatbot
    ├── cloud/                   ← Firebase sync + Twilio AQI alerts
    └── network/                 ← MQTT publisher/subscriber
```

---

## Running Optional Modules

The main dashboard (`server.py`) runs standalone with simulated sensor data.  
These modules are independent and each runs in its own terminal.

### AQI Streamlit Dashboard
```bash
pip install streamlit plotly pandas
cd AQI-MONITOR
streamlit run dashboard/app.py
# Opens at http://localhost:8501
```

### Climate Control (real weather data)
```bash
# Requires OPENWEATHER_API_KEY in .env
cd climate-control
python api_server.py
```

### Smoke/Gas/Fire via MQTT Hardware
```bash
pip install paho-mqtt
python mqtt_publisher.py      # Terminal 1 — publishes sensor readings
python mqtt_detector.py       # Terminal 2 — evaluates and triggers alerts
```

### WhatsApp Alerts (Twilio)
```bash
# Requires TWILIO_* variables in .env
pip install twilio
python main.py
```

### Python Face Recognition (OpenCV + dlib)
```bash
# Alternative to in-browser face-api.js — runs on the machine, not in browser
pip install opencv-python face-recognition numpy
python FaceRecognition.py
```
> **Windows users:** `face-recognition` requires CMake + Visual Studio build tools.  
> See [dlib installation guide](https://github.com/davisking/dlib) for your OS.

---

## Environment Variables

Copy `.env.example` to `.env` and fill in values you need:

| Variable | Used by | Required for |
|---|---|---|
| `OPENWEATHER_API_KEY` | `climate-control/api_server.py` | Real weather data |
| `TWILIO_ACCOUNT_SID` | `alerts_twilio.py`, `main.py` | WhatsApp/SMS alerts |
| `TWILIO_AUTH_TOKEN` | `alerts_twilio.py`, `main.py` | WhatsApp/SMS alerts |
| `TWILIO_WHATSAPP_FROM` | `alerts_twilio.py` | WhatsApp alerts |
| `ALERT_WHATSAPP_TO` | `alerts_twilio.py` | WhatsApp alerts |
| `BLYNK_AUTH_TOKEN` | `climate-control/api_server.py` | Blynk IoT display |
| `MQTT_BROKER` | `mqtt_publisher.py`, `mqtt_detector.py` | MQTT sensor hardware |
| `MQTT_USER` / `MQTT_PASS` | MQTT modules | Authenticated brokers |
| `FIREBASE_URL` | `AQI-MONITOR/cloud/` | Firebase cloud sync |
| `GROQ_API_KEY` | `AQI-MONITOR/ml/chatbot.py` | AQI chatbot |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.9+, FastAPI, Uvicorn, WebSocket |
| Frontend | Vanilla JS (ES2022), HTML5 Canvas, SVG, Web Audio API |
| Face Recognition | face-api.js (TinyFaceDetector, in-browser) |
| AQI | CPCB breakpoint formula, PyTorch LSTM forecasting |
| Climate | OpenWeather API, Blynk IoT |
| Alerts | Twilio WhatsApp/SMS, Web Notifications API |
| Sensors | MQTT (paho-mqtt), simulated fallback |

---

## Troubleshooting

**Dashboard shows "Polling (no WS)" instead of "Connected"**
```bash
pip install "uvicorn[standard]"
# Then restart server.py
```
WebSocket requires the `websockets` package bundled with `uvicorn[standard]`.

**Port already in use**
```bash
# Windows
netstat -ano | findstr :8000
taskkill /PID <pid_number> /F

# macOS / Linux
lsof -ti:8000 | xargs kill -9
```

**Camera not working for face recognition**
- Chrome requires HTTPS for camera on non-localhost URLs. For LAN access from your phone, either set up a self-signed cert or use localhost on the server machine directly.
- Allow camera permissions when the browser asks.
- The Simulations tab lets you test all alert pipelines without a camera.

**Face recognition slow on first start**
- face-api.js downloads ~6 MB of model weights from jsDelivr CDN on the first camera session. This is a one-time download per browser — subsequent starts are instant because the browser caches the files.

**face-recognition Python library fails to install**
- Windows: Install [CMake](https://cmake.org/download/) and [Visual Studio Build Tools](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022), then retry.
- macOS: `brew install cmake` then `pip install face-recognition`
- This only affects `FaceRecognition.py` — the in-browser face-api.js approach has no such dependency.

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<div align="center">
Made with ❤️ by the Smart Home team — SRM IST Delhi-NCR
</div>
