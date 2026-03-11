# SmartStock AI — Predictive Inventory Management System

<div align="center">

![Version](https://img.shields.io/badge/version-2.0.0-00d4ff?style=flat-square)
![Python](https://img.shields.io/badge/python-3.9+-00e5a0?style=flat-square)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-a78bfa?style=flat-square)
![License](https://img.shields.io/badge/license-MIT-f59e0b?style=flat-square)

**An AI-driven inventory management system with demand forecasting, real-time analytics, and role-based access control.**

*Final Year Project — B.Tech Computer Science & Engineering*

</div>

---

## 📌 Overview

SmartStock AI bridges the gap between traditional record-keeping and predictive analytics. It combines a FastAPI backend, SQLite database, and an ML-powered forecasting engine with a modern dark-themed dashboard — all running locally with a single double-click.

---

## 🚀 Key Features

| Feature | Description |
|---|---|
| **AI Demand Forecasting** | Ridge Regression + Fourier Features for seasonal trend decomposition |
| **Live Analytics Dashboard** | Chart.js charts — stock levels, category splits, value distribution, sales history |
| **Low Stock Alerts** | Per-product configurable thresholds with banner + badge notifications |
| **Audit Trail** | Every create / update / delete is logged with timestamp and actor |
| **Role-Based Access** | Admin (full control) and Staff (view + edit) with approval workflow |
| **Secure Auth** | SHA-256 password hashing, session-based login |
| **RESTful API** | FastAPI with proper HTTP status codes (201, 400, 404, 409) |
| **Excel Import/Export** | Upload edited `.xlsx` files; data upserted directly into the database |
| **Sales History** | Per-product daily sales stored in DB — no scattered files |

---

## 🗂️ Project Structure

```
SmartStock-AI/
├── frontend/
│   ├── index.html          # Main dashboard (tabs: Dashboard, Inventory, Analytics, AI Forecast)
│   └── login.html          # Login & staff signup page
│
├── ai_engine/
│   ├── train_model.py      # Ridge Regression training script
│   └── final_ai_brain.pkl  # Trained model (auto-generated on first run)
│
├── main.py                 # FastAPI application — all API routes
├── database.py             # SQLAlchemy models (Product, SalesRecord, Admin, Staff, ActivityLog)
├── requirements.txt        # Python dependencies
├── run_project.bat         # One-click Windows launcher
└── inventory.db            # SQLite database (auto-created on first run)
```

---

## 📦 Quick Setup (Windows — One Click)

```
1. Clone the repository
   git clone https://github.com/GhanishthK/SmartStock-AI.git

2. Double-click run_project.bat
```

That's it. The script will:
- Create a Python virtual environment
- Install all dependencies
- Train the AI model
- Open the login page in your browser
- Start the FastAPI server at http://127.0.0.1:8000

**Default admin credentials:** `admin` / `admin123`

---

## 🛠️ Manual Setup

```bash
# Clone
git clone https://github.com/GhanishthK/SmartStock-AI.git
cd SmartStock-AI

# Create virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux

# Install dependencies
pip install -r requirements.txt

# Train AI model
python ai_engine/train_model.py

# Start server
uvicorn main:app --reload

# Open frontend
# Open frontend/login.html in your browser
```

---

## 🌐 API Reference

### Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/login/` | Login as admin or staff |
| `POST` | `/signup/` | Register new staff account |
| `GET`  | `/pending-staff/` | List unapproved staff (admin only) |
| `PUT`  | `/approve-staff/{id}` | Approve a staff account (admin only) |

### Products

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`    | `/products/` | List all products |
| `GET`    | `/products/{id}` | Get a single product |
| `POST`   | `/products/` | Add a new product |
| `PUT`    | `/products/{id}` | Update a product |
| `DELETE` | `/products/{id}` | Delete a product |

### Analytics & Stats

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/stats/` | Dashboard KPIs (totals, averages, value) |
| `GET` | `/low-stock/` | Products below threshold |
| `GET` | `/category-stats/` | Per-category aggregates for charts |
| `GET` | `/activity-log/` | Audit trail (last 20 actions) |

### Sales & AI

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/sales/{id}` | Full sales history for a product |
| `GET`  | `/export-excel/{id}` | Download sales data as `.xlsx` |
| `POST` | `/upload-excel/{id}` | Upload edited sales data |
| `GET`  | `/predict-demand/{id}/{days}` | AI demand forecast (max 30 days) |

Interactive API docs available at **http://127.0.0.1:8000/docs**

---

## 🧠 AI Model Architecture

The forecasting engine decomposes demand into three components:

```
Predicted Demand = (Trend × 0.30) + (Momentum × 0.70) × Elasticity
```

| Component | Method | Description |
|-----------|--------|-------------|
| **Trend** | Ridge Regression | Long-term business growth mapping over a 3-year window |
| **Seasonality** | Fourier Features (sin/cos) | Captures yearly market cycles |
| **Momentum** | 7-day Rolling Average | Weights recent sales patterns heavily |
| **Price Elasticity** | Log scaling | Higher-priced items forecast lower demand |

---

## 🗄️ Database Schema

```
admins          — id, username, password_hash, created_at
staff           — id, username, password_hash, is_approved, created_at, approved_at
products        — id, name, category, stock_level, price, low_stock_threshold, created_at, updated_at
sales_records   — id, product_id (FK), sale_date, units_sold, created_at
activity_logs   — id, product_id (FK), action, detail, performed_by, timestamp
```

---

## 🖥️ Dashboard Tabs

- **Dashboard** — KPI stat cards, low stock list, category chart, recent inventory table
- **Inventory** — Full product table with search, category filter, inline edit/delete/export
- **Analytics** — Stock levels bar chart, category doughnut, value-by-category chart, per-product sales line chart
- **AI Forecast** — Run predictions, view model explanation cards
- **Add Product** — Form to add new inventory items
- **Admin Panel** — Pending approvals, activity audit log *(admin only)*

---

## 👥 Role Permissions

| Action | Admin | Staff |
|--------|-------|-------|
| View inventory & analytics | ✅ | ✅ |
| Add / Edit / Delete products | ✅ | ✅ |
| Run AI forecasts | ✅ | ✅ |
| Approve staff accounts | ✅ | ❌ |
| View activity log | ✅ | ❌ |

---

## 📋 Requirements

- Python 3.9+
- Windows (for `run_project.bat`; manual setup works on macOS/Linux)
- Modern browser (Chrome, Firefox, Edge)

---

## 🎓 Academic Context

This project was developed as a Final Year Major Project for B.Tech in Computer Science & Engineering. It demonstrates integration of:

- Machine Learning (demand forecasting with scikit-learn)
- RESTful API design (FastAPI + Pydantic v2)
- Relational database design (SQLAlchemy ORM + SQLite)
- Frontend engineering (vanilla JS + Chart.js + Tailwind CSS)
- Software engineering practices (audit logging, input validation, error handling)

---

<div align="center">
Made with ☕ by <a href="https://github.com/GhanishthK">GhanishthK</a>
</div>