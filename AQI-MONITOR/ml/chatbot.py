# ml/chatbot.py
# This file talks to Groq API and gives personalised health advice
# based on the live AQI data

from groq import Groq
import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

client = Groq(api_key=os.getenv("GROQ_API_KEY"))


def build_system_prompt(aqi: int, category: str, pm25: float, pm10: float) -> str:
    """
    This is the secret sauce of the chatbot.
    We inject live AQI data into the system prompt
    so the AI answers as a knowledgeable health advisor
    with access to real Delhi air quality data.
    """
    return f"""
You are a helpful air quality health advisor for Delhi residents.

CURRENT DELHI AIR QUALITY (live data right now):
- AQI: {aqi} ({category})
- PM2.5: {pm25} µg/m³
- PM10: {pm10} µg/m³

AQI SCALE FOR REFERENCE:
- 0-50: Good
- 51-100: Satisfactory  
- 101-200: Moderate
- 201-300: Poor
- 301-400: Very Poor
- 401-500: Severe

YOUR RULES:
- Give advice specific to the current AQI level shown above
- Be direct, practical and concise
- Maximum 3 bullet points per response
- If AQI is above 300 always recommend staying indoors
- If user mentions asthma, heart disease or elderly person be extra cautious
- Never give medical diagnoses
- Always end with one emoji that matches the air quality severity
"""


def chat(user_message: str, aqi: int, category: str,
         pm25: float, pm10: float, history: list) -> tuple:
    """
    Sends user message to Groq and returns AI response.

    user_message: what the user typed
    aqi, category, pm25, pm10: live sensor data
    history: list of past messages in this conversation
    
    Returns: (reply text, updated history)
    """
    system = build_system_prompt(aqi, category, pm25, pm10)

    # Add user message to history
    history.append({
        "role":    "user",
        "content": user_message
    })

    # Send full conversation history to Groq
    # This is how the bot remembers what was said before
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",   # free and fast Groq model,   # free and fast Groq model
        messages=[
            {"role": "system", "content": system},
            *history              # unpack full conversation history
        ],
        max_tokens=300,
        temperature=0.7
    )

    reply = response.choices[0].message.content

    # Add AI reply to history for next turn
    history.append({
        "role":    "assistant",
        "content": reply
    })

    return reply, history


# --- Test it directly ---
if __name__ == "__main__":
    print("Testing chatbot...\n")

    history = []

    # First message
    reply, history = chat(
        user_message="Is it safe to go for a morning run today?",
        aqi=340,
        category="Very Poor",
        pm25=210.5,
        pm10=315.2,
        history=history
    )
    print(f"User: Is it safe to go for a morning run today?")
    print(f"Bot:  {reply}\n")

    # Second message — tests if bot remembers context
    reply, history = chat(
        user_message="What if I wear a mask?",
        aqi=340,
        category="Very Poor",
        pm25=210.5,
        pm10=315.2,
        history=history
    )
    print(f"User: What if I wear a mask?")
    print(f"Bot:  {reply}")