# ml/train_model.py
# This file builds the LSTM neural network and trains it
# on the Delhi AQI data we prepared in the previous step

import numpy as np
import torch
import torch.nn as nn
from sklearn.model_selection import train_test_split
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ML_DIR   = os.path.join(BASE_DIR, "ml")

# --- Step 1: Load the prepared data ---
X = np.load(os.path.join(ML_DIR, "X.npy"))
y = np.load(os.path.join(ML_DIR, "y.npy"))
print(f"Loaded X: {X.shape}, y: {y.shape}")

# --- Step 2: Split into training and testing sets ---
# 80% of data used to train, 20% used to test accuracy
# shuffle=False because order matters in time series data
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, shuffle=False
)
print(f"Training samples: {len(X_train)}")
print(f"Testing samples:  {len(X_test)}")

# --- Step 3: Convert numpy arrays to PyTorch tensors ---
# PyTorch works with tensors not numpy arrays
# Think of tensors as numpy arrays with extra GPU powers
X_train = torch.FloatTensor(X_train)
X_test  = torch.FloatTensor(X_test)
y_train = torch.FloatTensor(y_train)
y_test  = torch.FloatTensor(y_test)

# --- Step 4: Define the LSTM model ---
class AQIForecaster(nn.Module):
    """
    LSTM = Long Short Term Memory
    It is a type of neural network that is very good at
    learning patterns in sequences — like time series data.

    Input:  7 days of 6 features = shape (batch, 7, 6)
    Output: next day AQI = shape (batch, 1)
    """
    def __init__(self):
        super().__init__()

        self.lstm = nn.LSTM(
            input_size=6,    # 6 features: PM2.5, PM10, NO2, CO, O3, AQI
            hidden_size=64,  # 64 memory cells inside LSTM
            num_layers=2,    # two stacked LSTM layers
            batch_first=True,# input shape: (batch, time, features)
            dropout=0.2      # randomly turn off 20% neurons to prevent overfitting
        )

        # Final layer converts 64 LSTM outputs → 1 AQI prediction
        self.fc = nn.Linear(64, 1)

    def forward(self, x):
        # Run input through LSTM
        out, _ = self.lstm(x)
        # Only take the last time step output
        out = out[:, -1, :]
        # Pass through final layer
        return self.fc(out).squeeze()


# --- Step 5: Set up training ---
model     = AQIForecaster()
optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
loss_fn   = nn.MSELoss()
# MSELoss = Mean Squared Error
# It measures how far predictions are from real values
# Lower is better

EPOCHS = 100  # how many times we loop through all training data

print("\nStarting training...")
print("-" * 40)

# --- Step 6: Training loop ---
for epoch in range(EPOCHS):
    model.train()

    # Forward pass: make predictions
    predictions = model(X_train)

    # Calculate how wrong we are
    loss = loss_fn(predictions, y_train)

    # Backward pass: adjust weights to be less wrong
    optimizer.zero_grad()
    loss.backward()
    optimizer.step()

    # Print progress every 10 epochs
    if (epoch + 1) % 10 == 0:
        model.eval()
        with torch.no_grad():
            test_predictions = model(X_test)
            test_loss = loss_fn(test_predictions, y_test)
        print(f"Epoch {epoch+1:3d}/100 | "
              f"Train Loss: {loss.item():.4f} | "
              f"Test Loss: {test_loss.item():.4f}")

# --- Step 7: Save the trained model ---
torch.save(model.state_dict(), os.path.join(ML_DIR, "aqi_lstm.pt"))
print("\nModel saved to ml/aqi_lstm.pt")
print("Training complete.")