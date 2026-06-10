# aqi_calculator.py

# This is the official CPCB (India) breakpoint table for PM2.5
# Each row means: if PM2.5 is between C_low and C_high,
# then AQI is between I_low and I_high
# Format: (C_low, C_high, I_low, I_high)

PM25_BREAKPOINTS = [
    (0,   30,  0,   50),    # Good
    (31,  60,  51,  100),   # Satisfactory
    (61,  90,  101, 200),   # Moderate
    (91,  120, 201, 300),   # Poor
    (121, 250, 301, 400),   # Very Poor
    (251, 500, 401, 500),   # Severe
]

PM10_BREAKPOINTS = [
    (0,   50,  0,   50),
    (51,  100, 51,  100),
    (101, 250, 101, 200),
    (251, 350, 201, 300),
    (351, 430, 301, 400),
    (431, 600, 401, 500),
]


def compute_sub_index(concentration, breakpoints):
    """
    This is the CPCB formula.
    It finds which row the concentration falls in,
    then does a simple linear interpolation to get the AQI.

    Linear interpolation just means:
    where exactly does this value sit between the two endpoints?
    """
    for (C_lo, C_hi, I_lo, I_hi) in breakpoints:
        if C_lo <= concentration <= C_hi:
            aqi = ((I_hi - I_lo) / (C_hi - C_lo)) * (concentration - C_lo) + I_lo
            return round(aqi)

    return 500  # if value is off the chart, return maximum


def get_category(aqi):
    """
    Converts a number like 287 into a label like 'Poor'.
    Simple if-else chain using CPCB categories.
    """
    if aqi <= 50:
        return "Good"
    elif aqi <= 100:
        return "Satisfactory"
    elif aqi <= 200:
        return "Moderate"
    elif aqi <= 300:
        return "Poor"
    elif aqi <= 400:
        return "Very Poor"
    else:
        return "Severe"


def calculate_aqi(pm25, pm10):
    """
    Main function. Call this with pm25 and pm10 values.
    It calculates AQI for each, takes the worst one,
    and returns the final AQI score and category label.
    """
    aqi_from_pm25 = compute_sub_index(pm25, PM25_BREAKPOINTS)
    aqi_from_pm10 = compute_sub_index(pm10, PM10_BREAKPOINTS)

    # CPCB rule: take the highest (worst) sub-index
    final_aqi = max(aqi_from_pm25, aqi_from_pm10)
    category  = get_category(final_aqi)

    return final_aqi, category


# Test it directly when you run this file
if __name__ == "__main__":
    # Test with some known values
    test_cases = [
        (20,  40),   # should be Good
        (55,  90),   # should be Satisfactory
        (80,  200),  # should be Moderate
        (100, 300),  # should be Poor
        (150, 400),  # should be Very Poor
        (300, 500),  # should be Severe
    ]

    print(f"{'PM2.5':<10} {'PM10':<10} {'AQI':<10} {'Category'}")
    print("-" * 45)

    for pm25, pm10 in test_cases:
        aqi, category = calculate_aqi(pm25, pm10)
        print(f"{pm25:<10} {pm10:<10} {aqi:<10} {category}")