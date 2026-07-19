# cloud/alert_system.py

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from dotenv import load_dotenv
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

GMAIL_USER     = os.getenv("GMAIL_USER")
GMAIL_PASSWORD = os.getenv("GMAIL_APP_PASS")
AQI_THRESHOLD  = 300
SAFE_THRESHOLD = 200
SEVERE_THRESHOLD = 400


def send_email_alert(data: dict):
    """
    AQI > 300 pe email bhejta hai.
    Flag ab Firebase mein store hota hai — restart pe bhi persist karta hai.
    AQI > 400 pe escalation alert bhejta hai.
    """
    from cloud.firebase_client import (
        get_alert_status,
        set_alert_sent,
        reset_alert_flag,
        get_registered_emails
    )

    aqi      = data["aqi"]
    category = data["category"]
    pm25     = data["pm25"]
    pm10     = data["pm10"]

    status     = get_alert_status()
    recipients = get_registered_emails()

    if not recipients:
        print("No registered emails found.")
        return

    # Case 1: AQI > 400 (Severe) — urgent escalation alert
    if aqi > SEVERE_THRESHOLD and not status["is_sent"]:
        _send_bulk_email(
            recipients, aqi, category, pm25, pm10,
            subject=f"🚨 URGENT — Severe AQI Alert ({aqi}) — Stay Indoors Immediately",
            urgent=True
        )
        set_alert_sent(aqi, category)

    # Case 2: AQI > 300 — normal danger alert (sirf ek baar)
    elif aqi > AQI_THRESHOLD and not status["is_sent"]:
        _send_bulk_email(
            recipients, aqi, category, pm25, pm10,
            subject=f"⚠️ AQI Alert — {category} ({aqi})",
            urgent=False
        )
        set_alert_sent(aqi, category)

    # Case 3: AQI safe ho gaya — flag reset karo
    elif aqi <= SAFE_THRESHOLD and status["is_sent"]:
        reset_alert_flag()


def _send_bulk_email(recipients, aqi, category, pm25, pm10, subject, urgent=False):
    """Saare registered users ko email bhejta hai."""
    for recipient in recipients:
        _send_single_email(recipient, aqi, category, pm25, pm10, subject, urgent)


def _send_single_email(recipient, aqi, category, pm25, pm10, subject, urgent=False):
    """Ek email bhejta hai."""
    try:
        msg            = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = GMAIL_USER
        msg["To"]      = recipient

        bg_color = "#8B0000" if urgent else "#CC0000"

        text = f"""
AQI {'SEVERE ALERT' if urgent else 'ALERT'}

Current AQI : {aqi} ({category})
PM2.5       : {pm25} µg/m3
PM10        : {pm10} µg/m3

Health Advisory:
- Stay indoors immediately
- Wear N95 mask if going outside
- Avoid all outdoor exercise
- Keep windows and doors closed
- Use air purifier if available
{'- This is a SEVERE alert — take immediate action' if urgent else ''}

-- Delhi AQI Monitor
        """

        html = f"""
<html>
<body style="font-family:Arial,sans-serif;padding:20px;max-width:600px;margin:auto;">

  <div style="background:{bg_color};color:white;padding:20px;
              border-radius:8px;text-align:center;">
    <h2 style="margin:0;">{'🚨 SEVERE ALERT' if urgent else '⚠️ AQI ALERT'}</h2>
    <h1 style="font-size:60px;margin:10px 0;">{aqi}</h1>
    <h3 style="margin:0;">{category}</h3>
  </div>

  <br>

  <table style="width:100%;border-collapse:collapse;">
    <tr style="background:#f5f5f5;">
      <td style="padding:10px;border:1px solid #ddd;"><b>PM2.5</b></td>
      <td style="padding:10px;border:1px solid #ddd;">{pm25} µg/m3</td>
    </tr>
    <tr>
      <td style="padding:10px;border:1px solid #ddd;"><b>PM10</b></td>
      <td style="padding:10px;border:1px solid #ddd;">{pm10} µg/m3</td>
    </tr>
    <tr style="background:#f5f5f5;">
      <td style="padding:10px;border:1px solid #ddd;"><b>Category</b></td>
      <td style="padding:10px;border:1px solid #ddd;">{category}</td>
    </tr>
  </table>

  <br>

  <div style="background:#FFF3CD;padding:15px;border-radius:8px;
              border-left:4px solid #FFC107;">
    <h3 style="margin-top:0;">Health Advisory</h3>
    <ul>
      <li>Stay indoors as much as possible</li>
      <li>Wear N95 mask if going outside</li>
      <li>Avoid all outdoor exercise</li>
      <li>Keep windows and doors closed</li>
      <li>Use air purifier if available</li>
      {'<li><b>This is a SEVERE alert — take immediate action</b></li>' if urgent else ''}
    </ul>
  </div>

  <br>
  <p style="color:#999;font-size:12px;text-align:center;">
    Delhi AQI Monitor — Automated Alert System<br>
    You received this because you registered for AQI alerts.
  </p>

</body>
</html>
        """

        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            server.sendmail(GMAIL_USER, recipient, msg.as_string())

        print(f"Email sent to {recipient} — AQI: {aqi} ({category})")

    except Exception as e:
        print(f"Email failed to {recipient}: {e}")


def send_daily_summary(readings_today: list, recipients: list):
    """
    Automation: Har subah 8 AM pe daily AQI summary email bhejta hai.
    mqtt_subscriber.py mein schedule se call hoga.
    """
    if not readings_today or not recipients:
        return

    avg_aqi  = round(sum(r["aqi"] for r in readings_today) / len(readings_today))
    max_aqi  = max(r["aqi"] for r in readings_today)
    min_aqi  = min(r["aqi"] for r in readings_today)
    avg_pm25 = round(sum(r["pm25"] for r in readings_today) / len(readings_today), 1)

    from hardware.aqi_calculator import get_category
    avg_cat = get_category(avg_aqi)

    subject = f"🌅 Daily AQI Summary — {datetime.now().strftime('%d %B %Y')} — Avg AQI: {avg_aqi}"

    html = f"""
<html>
<body style="font-family:Arial,sans-serif;padding:20px;max-width:600px;margin:auto;">

  <h2 style="color:#1F4E79;">🌅 Good Morning — Daily AQI Report</h2>
  <p style="color:#555;">{datetime.now().strftime('%A, %d %B %Y')}</p>

  <div style="background:#E8F4FD;padding:15px;border-radius:8px;margin-bottom:15px;">
    <h3 style="margin:0;color:#1F4E79;">Yesterday's Summary</h3>
  </div>

  <table style="width:100%;border-collapse:collapse;">
    <tr style="background:#1F4E79;color:white;">
      <td style="padding:10px;"><b>Metric</b></td>
      <td style="padding:10px;"><b>Value</b></td>
    </tr>
    <tr style="background:#f5f5f5;">
      <td style="padding:10px;border:1px solid #ddd;">Average AQI</td>
      <td style="padding:10px;border:1px solid #ddd;">{avg_aqi} ({avg_cat})</td>
    </tr>
    <tr>
      <td style="padding:10px;border:1px solid #ddd;">Maximum AQI</td>
      <td style="padding:10px;border:1px solid #ddd;">{max_aqi}</td>
    </tr>
    <tr style="background:#f5f5f5;">
      <td style="padding:10px;border:1px solid #ddd;">Minimum AQI</td>
      <td style="padding:10px;border:1px solid #ddd;">{min_aqi}</td>
    </tr>
    <tr>
      <td style="padding:10px;border:1px solid #ddd;">Average PM2.5</td>
      <td style="padding:10px;border:1px solid #ddd;">{avg_pm25} µg/m3</td>
    </tr>
    <tr style="background:#f5f5f5;">
      <td style="padding:10px;border:1px solid #ddd;">Total Readings</td>
      <td style="padding:10px;border:1px solid #ddd;">{len(readings_today)}</td>
    </tr>
  </table>

  <br>
  <p style="color:#999;font-size:12px;text-align:center;">
    Delhi AQI Monitor — Daily Summary<br>
    Sent automatically every morning at 8 AM
  </p>

</body>
</html>
    """

    for recipient in recipients:
        try:
            msg            = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = GMAIL_USER
            msg["To"]      = recipient
            msg.attach(MIMEText(html, "html"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(GMAIL_USER, GMAIL_PASSWORD)
                server.sendmail(GMAIL_USER, recipient, msg.as_string())

            print(f"Daily summary sent to {recipient}")

        except Exception as e:
            print(f"Daily summary failed to {recipient}: {e}")


# --- Test directly ---
if __name__ == "__main__":
    print("Testing email alert...")
    fake_data = {
        "aqi":      350,
        "category": "Very Poor",
        "pm25":     220.5,
        "pm10":     310.2
    }
    send_email_alert(fake_data)
