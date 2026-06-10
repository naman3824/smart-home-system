# ml/prepare_data.py
# This file cleans the Delhi data and prepares it for LSTM training
# It saves two files: X.npy (inputs) and y.npy (outputs)

import pandas as pd
import numpy as np
from sklearn.preprocessing import MinMaxScaler
import os, pickle

BASE_DIR  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(BASE_DIR, "data", "city_day.csv")
ML_DIR    = os.path.join(BASE_DIR, "ml")

# --- Step 1: Load and filter Delhi only ---
df = pd.read_csv(DATA_PATH)
df = df[df["City"] == "Delhi"].copy()
df = df.sort_values("Date").reset_index(drop=True)
print(f"Delhi rows: {len(df)}")

# --- Step 2: Keep only the columns we need ---
features = ["PM2.5", "PM10", "NO2", "CO", "O3", "AQI"]
df = df[features]

# --- Step 3: Fill missing values with column average ---
# This is the simplest and safest approach for small gaps
df = df.fillna(df.mean())
print("Missing values after fill:", df.isnull().sum().sum())

# --- Step 4: Scale all values to range 0 to 1 ---
# LSTM trains much better when all numbers are between 0 and 1
# MinMaxScaler does: (value - min) / (max - min)
scaler = MinMaxScaler()
scaled = scaler.fit_transform(df)
print("Data scaled. Shape:", scaled.shape)

# Save the scaler so we can reverse scaling later
# when we want to show real AQI numbers
pickle.dump(scaler, open(os.path.join(ML_DIR, "scaler.pkl"), "wb"))
print("Scaler saved.")

# --- Step 5: Create sliding windows ---
# LSTM needs sequences to learn patterns
# We say: given the last 7 days, predict the next day AQI
# So we slide a 7-day window across all the data

WINDOW_SIZE = 7   # use last 7 days as input
X = []            # inputs  — shape will be (samples, 7, 6)
y = []            # outputs — shape will be (samples, 1)

for i in range(len(scaled) - WINDOW_SIZE):
    # Input: 7 rows of all 6 features
    X.append(scaled[i : i + WINDOW_SIZE])
    # Output: AQI of the next day (column index 5 = AQI)
    y.append(scaled[i + WINDOW_SIZE][5])

X = np.array(X)
y = np.array(y)

print(f"X shape: {X.shape}")   # should be (2001, 7, 6)
print(f"y shape: {y.shape}")   # should be (2001,)

# --- Step 6: Save X and y as numpy files ---
np.save(os.path.join(ML_DIR, "X.npy"), X)
np.save(os.path.join(ML_DIR, "y.npy"), y)
print("X.npy and y.npy saved to ml/ folder.")
print("\nData preparation complete. Ready to train.")