import os
import threading
import requests
import winsound
from dotenv import load_dotenv

load_dotenv()


class AlertSender:

    def __init__(self):

        self.topic = os.getenv(
            "NTFY_TOPIC",
            "kcj_detector"
        )

        self.url = f"https://ntfy.sh/{self.topic}"

    # =====================================================
    # PLAY LOCAL SIREN
    # =====================================================

    def play_alarm(self):

        try:

            alarm = os.path.join(
                os.path.dirname(__file__),
                "alarm.wav"
            )

            if os.path.exists(alarm):

                print("[INFO] Playing alarm.wav...")

                winsound.PlaySound(
                    alarm,
                    winsound.SND_FILENAME
                )

            else:

                print("[WARNING] alarm.wav not found. Using beeps.")

                for _ in range(6):
                    winsound.Beep(1200, 400)
                    winsound.Beep(800, 400)

        except Exception as e:

            print("[ALARM ERROR]", e)

    # =====================================================
    # SEND PHONE NOTIFICATION
    # =====================================================

    def send_alert(
        self,
        title,
        body,
        priority="urgent",
        tags="fire,warning,house,rotating_light"
    ):

        # Play alarm in background
        threading.Thread(
            target=self.play_alarm,
            daemon=True
        ).start()

        # IMPORTANT:
        # HTTP headers CANNOT contain emojis.
        # So Title must remain plain text.

        headers = {

            "Title": "SMART HOME FIRE DETECTOR",

            "Priority": priority,

            "Tags": tags

        }

        try:

            response = requests.post(

                self.url,

                data=body.encode("utf-8"),

                headers=headers,

                timeout=10

            )

            print("\n" + "=" * 60)
            print("PHONE NOTIFICATION SENT")
            print("Status Code :", response.status_code)
            print("Topic       :", self.topic)
            print("=" * 60)

            if response.status_code != 200:
                print(response.text)

        except Exception as e:

            print("\nNotification Error:")
            print(e)
