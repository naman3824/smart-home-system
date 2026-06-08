# ml/explore_data.py
# This file just prints basic info about the dataset
# so we understand what we are working with

import pandas as pd
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(BASE_DIR, "data", "city_day.csv")

df = pd.read_csv(DATA_PATH)

print("Shape of data:", df.shape)
print("\nColumn names:")
print(df.columns.tolist())

print("\nFirst 5 rows:")
print(df.head())

print("\nHow many cities are in this dataset:")
print(df["City"].unique())

print("\nFilter only Delhi:")
delhi = df[df["City"] == "Delhi"]
print(f"Delhi has {len(delhi)} rows")

print("\nDelhi missing values:")
print(delhi.isnull().sum())

print("\nDelhi date range:")
print("From:", delhi["Date"].min())
print("To:  ", delhi["Date"].max())