# SmartStock AI: Predictive Inventory Management System

**SmartStock AI** is a data-driven inventory management solution that bridges the gap between traditional record-keeping and predictive analytics.

## 🚀 Key Features
- **AI Forecasting:** Ridge Regression with Fourier Features for seasonal demand tracking.
- **Secure Auth:** SHA-256 Hashing for robust Admin security.
- **RESTful API:** High-performance CRUD (Create, Read, Delete) operations via FastAPI.
- **Dynamic UI:** Modern, responsive dashboard built with Tailwind CSS.

## 📦 One-Click Setup (Windows)
1. **Clone the repo:**
   `git clone https://github.com/YOUR_USERNAME/SmartStock-AI.git`
2. **Run the Project:**
   Double-click `run_project.bat`. This script automates environment setup and AI model training.

## 🧠 The AI Logic
The system decomposes sales data into:
1. **Trend Line:** Long-term business growth mapping.
2. **Seasonality:** Sine/Cosine waves for yearly market cycles.
3. **Price Elasticity:** Logarithmic scaling to adjust demand based on product cost.