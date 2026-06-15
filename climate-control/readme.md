# Smart Climate Control API

A Python-based smart home climate control system. It fetches real-time weather data using the **OpenWeather API**, runs a smart HVAC decision engine to determine the optimal home temperature and HVAC mode, and exposes all data via a **FastAPI** REST server for integration with the custom dashboard.

---

## 🚀 Features
- **Background Sensor Loop:** Automatically fetches weather every 60 seconds using a single persistent HTTP connection.
- **Smart HVAC Logic:** Automatically decides between `MAX COOLING`, `DEHUMIDIFY & COOL`, `ECO MODE`, and `HEATING` based on outdoor temperature and humidity.
- **REST API:** Exposes endpoints to retrieve real-time weather and HVAC data.
- **Interactive Docs:** Built-in Swagger UI for testing API endpoints.
- **Ngrok Ready:** Easy to expose to the public internet for remote dashboard integration.

---

## 🛠️ Prerequisites & Installation

1. **Python 3.11+** installed on your system.
2. Clone or download this repository.
3. Install the required Python packages:
   ```bash
   pip install fastapi uvicorn pydantic python-dotenv httpx
   ```

---

## ⚙️ Configuration

1. In the root of the project folder, create a file named `.env`.
2. Add your OpenWeather API key and location:
   ```env
   OPENWEATHER_API_KEY=your_openweather_api_key_here
   CITY=Gurugram
   LATITUDE=28.477511
   LONGITUDE=77.080851
   ```
*(Note: Your `.env` file is safely ignored by Git thanks to the `.gitignore` file, keeping your keys secure.)*

---

## 🏃‍♂️ Running the Server

Start the FastAPI server from your terminal:
```bash
python api_server.py
```

The server will start on `http://localhost:8000`. You will see terminal output showing the background sensor loop actively fetching data and updating the in-memory store.

---

## 🌐 API Endpoints

Once the server is running, you can access the following local endpoints:

| Method | Endpoint | Description |
|---|---|---|
| **GET** | `/api/all` | Returns all cached weather, HVAC, and location data in a single JSON response. |
| **GET** | `/docs` | Opens the interactive Swagger UI to view and test all endpoints. |

Example response from `/api/all`:
```json
{
  "weather": {
    "temperature": 41.91,
    "humidity": 19,
    "condition": "clear sky"
  },
  "hvac": {
    "status": "MAX COOLING",
    "target_temp": 20.0
  },
  "location": {
    "city": "Gurugram",
    "latitude": 28.477511,
    "longitude": 77.080851
  },
  "last_updated": "2026-06-08T06:30:00+00:00"
}
```

---

## 🌍 Exposing the API to the Public (Ngrok)

If you want an external dashboard to access your API over the internet, you can use **ngrok**.

1. Download and install [ngrok](https://ngrok.com/).
2. Open a **new** terminal tab (keep your FastAPI server running in the original tab).
3. Run the following command:
   ```bash
   ngrok http 8000
   ```
4. Look for the **Forwarding** URL in the ngrok terminal output (e.g., `https://xxxx-xxxx.ngrok-free.app`).
5. Point your dashboard to this URL — e.g., `https://xxxx-xxxx.ngrok-free.app/api/all`.
