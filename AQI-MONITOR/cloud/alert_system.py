# cloud/alert_system.py
# This file sends an SMS alert when AQI crosses a danger threshold

from twilio.rest import Client
import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

TWILIO_SID   = os.getenv("TWILIO_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
TWILIO_FROM  = os.getenv("TWILIO_FROM")
TWILIO_TO    = os.getenv("TWILIO_TO")

# We only send one alert per danger event
# This variable tracks whether we already sent an alert
# so we do not spam the user every 5 seconds
alert_sent = False


def send_sms_alert(data: dict):
    """
    Sends an SMS if AQI is above 300.
    Only sends once per danger event — not every reading.
    """
    global alert_sent

    aqi      = data["aqi"]
    category = data["category"]
    pm25     = data["pm25"]

    # Only send if dangerous AND we have not already sent
    
    if aqi > 350 and not alert_sent:
        try:
            client = Client(TWILIO_SID, TWILIO_TOKEN)

            message = client.messages.create(
                body=(
                    f"⚠️ Delhi AQI ALERT!\n"
                    f"AQI: {aqi} ({category})\n"
                    f"PM2.5: {pm25} µg/m³\n"
                    f"Stay indoors. Wear N95 mask if going out.\n"
                    f"-- Delhi AQI Monitor"
                ),
                from_=TWILIO_FROM,
                to=TWILIO_TO
            )

            print(f"SMS alert sent! Message SID: {message.sid}")
            alert_sent = True   # do not send again until AQI drops

        except Exception as e:
            print(f"SMS failed: {e}")

    # Reset alert if AQI drops back below 300
    # so next danger event triggers a new alert
    elif aqi <= 300:
        alert_sent = False


# --- Test it directly ---
if __name__ == "__main__":
    print("Testing SMS alert...")
    fake_data = {
        "aqi":      350,
        "category": "Very Poor",
        "pm25":     220.5
    }
    send_sms_alert(fake_data)