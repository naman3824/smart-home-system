# alerts_twilio
import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()


@dataclass
class TwilioConfig:
    account_sid: str
    auth_token: str
    whatsapp_from: str
    whatsapp_to: str


class TwilioWhatsAppAlertSender:
    def __init__(self, config: Optional[TwilioConfig] = None):
        if config is None:
            config = TwilioConfig(
                account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
                auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
                whatsapp_from=os.getenv("TWILIO_WHATSAPP_FROM", ""),
                whatsapp_to=os.getenv("ALERT_WHATSAPP_TO", "")
            )
        self.config = config
        self.client = Client(config.account_sid, config.auth_token)

    def send_alert(self, title: str, body: str):
        message_text = f"*{title}*\n{body}"

        msg = self.client.messages.create(
            from_=self.config.whatsapp_from,
            to=self.config.whatsapp_to,
            body=message_text
        )
        print(f"[ALERT SENT] WhatsApp SID={msg.sid}, title={title}")
