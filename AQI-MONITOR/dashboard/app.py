# dashboard/app.py

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import sys, os, time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from cloud.firebase_client import get_latest_reading
from ml.predict import predict_next_7_days
from ml.chatbot import chat

st.set_page_config(
    page_title="Delhi AQI Monitor",
    page_icon="🌫️",
    layout="wide"
)

st.title("🌫️ Delhi Real-Time Air Quality Monitor")


def get_color(category):
    colors = {
        "Good":         "green",
        "Satisfactory": "blue",
        "Moderate":     "orange",
        "Poor":         "red",
        "Very Poor":    "darkred",
        "Severe":       "maroon"
    }
    return colors.get(category, "gray")


def build_gauge(aqi, category):
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=aqi,
        title={"text": f"AQI — {category}"},
        gauge={
            "axis": {"range": [0, 500]},
            "bar":  {"color": get_color(category)},
            "steps": [
                {"range": [0,   100], "color": "#90EE90"},
                {"range": [100, 200], "color": "#FFFF00"},
                {"range": [200, 300], "color": "#FFA500"},
                {"range": [300, 400], "color": "#FF0000"},
                {"range": [400, 500], "color": "#800000"},
            ]
        }
    ))
    fig.update_layout(height=300)
    return fig


data = get_latest_reading()

tab1, tab2, tab3 = st.tabs([
    "Live Monitor",
    "7-Day Forecast",
    "Health Advisor"
])


# ── TAB 1: Live Monitor ───────────────────────────────────────
with tab1:
    if data is None:
        st.warning("No data found. Make sure publisher and subscriber are running.")
    else:
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("AQI",         data["aqi"],        data["category"])
        col2.metric("PM2.5",       data["pm25"],        "µg/m³")
        col3.metric("PM10",        data["pm10"],        "µg/m³")
        col4.metric("Temperature", data["temperature"], "°C")

        st.divider()

        col_left, col_right = st.columns(2)

        with col_left:
            st.plotly_chart(
                build_gauge(data["aqi"], data["category"]),
                use_container_width=True
            )

        with col_right:
            st.subheader("Current Reading Details")
            st.write(f"**Timestamp:** {data['timestamp']}")
            st.write(f"**PM2.5:** {data['pm25']} µg/m³")
            st.write(f"**PM10:** {data['pm10']} µg/m³")
            st.write(f"**CO2:** {data['co2_ppm']} ppm")
            st.write(f"**Humidity:** {data['humidity']} %")
            st.write(f"**Location:** {data['latitude']}, {data['longitude']}")

            color = get_color(data["category"])
            st.markdown(
                f"""<div style="background-color:{color};padding:15px;
                border-radius:10px;text-align:center;color:white;
                font-size:20px;font-weight:bold;margin-top:20px">
                {data['category'].upper()}</div>""",
                unsafe_allow_html=True
            )


# ── TAB 2: 7-Day Forecast ─────────────────────────────────────
with tab2:
    st.subheader("7-Day AQI Forecast (LSTM Model)")
    st.caption("Predicted using a neural network trained on Delhi CPCB data 2015–2020")

    if data is None:
        st.warning("No live data available.")
    else:
        last_7_days = [{
            "pm25": data["pm25"],
            "pm10": data["pm10"],
            "no2":  20,
            "co":   1.5,
            "o3":   30,
            "aqi":  data["aqi"]
        }] * 7

        with st.spinner("Running LSTM forecast..."):
            forecast = predict_next_7_days(last_7_days)

        days = [f"Day {i+1}" for i in range(7)]
        fig = px.line(
            x=days, y=forecast, markers=True,
            labels={"x": "Day", "y": "Predicted AQI"},
            title="Predicted AQI for Next 7 Days"
        )
        fig.update_traces(line_color="royalblue", line_width=2)
        fig.add_hline(y=100, line_dash="dash",
                      line_color="orange",
                      annotation_text="Moderate threshold")
        fig.add_hline(y=200, line_dash="dash",
                      line_color="red",
                      annotation_text="Poor threshold")
        fig.update_layout(height=400)
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Forecast Table")
        for i, aqi in enumerate(forecast, 1):
            cat = "Good" if aqi <= 50 else \
                  "Satisfactory" if aqi <= 100 else \
                  "Moderate" if aqi <= 200 else \
                  "Poor" if aqi <= 300 else \
                  "Very Poor" if aqi <= 400 else "Severe"
            col_a, col_b, col_c = st.columns(3)
            col_a.write(f"Day {i}")
            col_b.write(f"AQI: {aqi}")
            col_c.write(cat)


# ── TAB 3: Health Advisor ─────────────────────────────────────
with tab3:
    st.subheader("AI Health Advisor")
    st.caption("Ask anything about air quality and your health. Powered by Groq AI.")

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    if data is None:
        st.warning("No live AQI data available.")
    else:
        col1, col2, col3 = st.columns(3)
        col1.metric("Current AQI",  data["aqi"],  data["category"])
        col2.metric("PM2.5",        data["pm25"], "µg/m³")
        col3.metric("PM10",         data["pm10"], "µg/m³")

        st.divider()

        for msg in st.session_state.chat_history:
            st.chat_message(msg["role"]).write(msg["content"])

        if prompt := st.chat_input("Ask about air quality or your health..."):
            st.chat_message("user").write(prompt)
            with st.spinner("Thinking..."):
                reply, st.session_state.chat_history = chat(
                    user_message=prompt,
                    aqi=data["aqi"],
                    category=data["category"],
                    pm25=data["pm25"],
                    pm10=data["pm10"],
                    history=st.session_state.chat_history
                )
            st.chat_message("assistant").write(reply)
            st.rerun()


# --- Refresh button ---
if st.sidebar.button("Refresh Data"):
    st.rerun()

st.sidebar.caption("Data updates every 5 seconds from simulator")