import numpy as np
from sklearn.linear_model import Ridge
import pickle
import os

print("Generating Smoothed, Highly Accurate Market Data...")
np.random.seed(42)
days_past = np.arange(1, 1096).reshape(-1, 1) # 3 Years of data

# Smoothed Equation: Base(50) + Slow Growth + Gentle Yearly Cycle
sales_history = 50 + (days_past * 0.01) + (15 * np.sin(2 * np.pi * days_past / 365))

print("Training Conservative Ridge Regression AI...")
# FEATURE ENGINEERING: We feed the AI the Trend and the Yearly Cycle
X_train = np.column_stack((
    days_past,
    np.sin(2 * np.pi * days_past / 365),
    np.cos(2 * np.pi * days_past / 365)
))

# Higher alpha = smoother, less jumpy predictions
model = Ridge(alpha=10.0)
model.fit(X_train, sales_history.ravel())

# Save the final brain
os.makedirs('ai_engine', exist_ok=True)
with open('ai_engine/final_ai_brain.pkl', 'wb') as f:
    pickle.dump(model, f)

print("✅ Success! Smooth AI Model trained and saved as final_ai_brain.pkl")