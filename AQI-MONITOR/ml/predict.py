# ml/predict.py
# This file loads the trained model and makes predictions
# Other files will import the predict() function from here

import torch
import torch.nn as nn
import numpy as np
import pickle
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ML_DIR   = os.path.join(BASE_DIR, "ml")


# --- Define the same model architecture as in train_model.py ---
# We must define it again here so PyTorch knows what to load
class AQIForecaster(nn.Module):
    def __init__(self):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=6,
            hidden_size=64,
            num_layers=2,
            batch_first=True,
            dropout=0.2
        )
        self.fc = nn.Linear(64, 1)

    def forward(self, x):
        out, _ = self.lstm(x)
        out = out[:, -1, :]
        return self.fc(out).squeeze()


# --- Load model and scaler once when file is imported ---
model = AQIForecaster()
model.load_state_dict(
    torch.load(os.path.join(ML_DIR, "aqi_lstm.pt"),
    map_location=torch.device("cpu"))
)
model.eval()  # set to evaluation mode — turns off dropout

scaler = pickle.load(open(os.path.join(ML_DIR, "scaler.pkl"), "rb"))


def predict_next_7_days(last_7_days: list) -> list:
    """
    Takes last 7 days of real readings as input.
    Returns predicted AQI for the next 7 days.

    last_7_days: list of 7 dicts, each with keys:
                 pm25, pm10, no2, co, o3, aqi

    Returns: list of 7 predicted AQI values (real numbers)
    """
    # Step 1: convert list of dicts to numpy array
    features = ["pm25", "pm10", "no2", "co", "o3", "aqi"]
    raw = np.array([[d.get(f, 0) for f in features] for d in last_7_days])

    # Step 2: scale using same scaler used during training
    import pandas as pd
    raw_df = pd.DataFrame(raw, columns=["PM2.5", "PM10", "NO2", "CO", "O3", "AQI"])
    scaled = scaler.transform(raw_df)

    predictions = []
    window = scaled.copy()  # start with the last 7 real days

    # Step 3: predict one day at a time for 7 days
    # each prediction becomes part of input for next prediction
    for _ in range(7):
        x = torch.FloatTensor(window).unsqueeze(0)  # shape: (1, 7, 6)

        with torch.no_grad():
            pred_scaled = model(x).item()

        # Step 4: reverse scale to get real AQI number
        # We need a full row to reverse scale, so pad with zeros
        dummy_row = np.zeros((1, 6))
        dummy_row[0][5] = pred_scaled   # AQI is column index 5
        real_aqi = scaler.inverse_transform(dummy_row)[0][5]
        predictions.append(round(real_aqi))

        # Step 5: slide the window forward by one day
        new_row = window[-1].copy()
        new_row[5] = pred_scaled   # update AQI with prediction
        window = np.vstack([window[1:], new_row])

    return predictions


# --- Test it directly ---
if __name__ == "__main__":
    # Simulate 7 days of input data using Delhi averages
    fake_last_7_days = [
        {"pm25": 120, "pm10": 200, "no2": 40, "co": 1.5, "o3": 30, "aqi": 250},
        {"pm25": 145, "pm10": 230, "no2": 45, "co": 1.8, "o3": 28, "aqi": 280},
        {"pm25": 160, "pm10": 250, "no2": 50, "co": 2.0, "o3": 25, "aqi": 310},
        {"pm25": 130, "pm10": 210, "no2": 42, "co": 1.6, "o3": 32, "aqi": 260},
        {"pm25": 170, "pm10": 270, "no2": 55, "co": 2.2, "o3": 22, "aqi": 330},
        {"pm25": 155, "pm10": 245, "no2": 48, "co": 1.9, "o3": 27, "aqi": 300},
        {"pm25": 140, "pm10": 225, "no2": 44, "co": 1.7, "o3": 29, "aqi": 270},
    ]

    forecast = predict_next_7_days(fake_last_7_days)
    print("Predicted AQI for next 7 days:")
    for i, aqi in enumerate(forecast, 1):
        print(f"  Day {i}: AQI {aqi}")
