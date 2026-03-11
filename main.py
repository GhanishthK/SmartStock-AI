"""
SmartStock AI — FastAPI Backend
=================================
Improvements over original:
  - /stats/            : live dashboard KPIs (total, low-stock, value, etc.)
  - /low-stock/        : filtered list of products below threshold
  - /activity-log/     : recent audit trail for Admin panel
  - /sales/ now reads from DB (SalesRecord) instead of Excel files
  - /upload-excel/     : still supported — file data is written INTO the DB
  - /export-excel/     : still supported — pulled from DB, not a cached file
  - Pydantic v2-compatible schemas with field validators
  - Proper HTTP status codes (404, 400, 409) instead of {"success": false}
  - AI prediction improved: uses all available DB sales, not just last 3 days
  - ActivityLog written on every create / update / delete
  - Seeding is idempotent — safe to restart the server multiple times
"""

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel, field_validator
from typing import Optional
import database
import pickle
import numpy as np
import os
import hashlib
import random
import pandas as pd
import io
from datetime import datetime, timedelta

# ── App bootstrap ─────────────────────────────────────────────────────────────
database.Base.metadata.create_all(bind=database.engine)

# ── Database migration helper ─────────────────────────────────────────────────
# SQLAlchemy create_all() never alters existing tables.
# This function checks for missing columns and adds them automatically,
# so old inventory.db files work with the new schema without needing deletion.
def run_migrations():
    from sqlalchemy import text, inspect
    inspector = inspect(database.engine)

    # Map of table -> list of (column_name, column_definition)
    required_columns = {
        "admins": [
            ("created_at", "DATETIME"),
        ],
        "staff": [
            ("created_at",   "DATETIME"),
            ("approved_at",  "DATETIME"),
        ],
        "products": [
            ("low_stock_threshold", "INTEGER DEFAULT 100 NOT NULL"),
            ("created_at",          "DATETIME"),
            ("updated_at",          "DATETIME"),
        ],
    }

    # Create sales_portal_staff table if it doesn't exist
    with database.engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sales_portal_staff (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                full_name     TEXT NOT NULL,
                total_sales   REAL DEFAULT 0,
                total_units   INTEGER DEFAULT 0,
                created_at    DATETIME
            )
        """))
        conn.commit()

    with database.engine.connect() as conn:
        for table, columns in required_columns.items():
            try:
                existing = {col["name"] for col in inspector.get_columns(table)}
            except Exception:
                continue  # table doesn't exist yet — create_all handles it

            for col_name, col_def in columns:
                if col_name not in existing:
                    try:
                        conn.execute(text(
                            f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"
                        ))
                        conn.commit()
                        print(f"✅ Migration: added '{col_name}' to '{table}'")
                    except Exception as e:
                        print(f"⚠️  Migration skipped ({table}.{col_name}): {e}")

run_migrations()

app = FastAPI(
    title="SmartStock AI API",
    description="AI-driven inventory management backend",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── AI Model ──────────────────────────────────────────────────────────────────
try:
    with open("ai_engine/final_ai_brain.pkl", "rb") as f:
        ai_model = pickle.load(f)
    print("✅ AI model loaded.")
except FileNotFoundError:
    ai_model = None
    print("⚠️  AI model not found — using momentum-only fallback.")


# ── Utilities ─────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def log_activity(db: Session, action: str, detail: str,
                 product_id: int = None, performed_by: str = "system"):
    """Write one row to ActivityLog. Never raises — failures are silent."""
    try:
        entry = database.ActivityLog(
            product_id=product_id,
            action=action,
            detail=detail,
            performed_by=performed_by,
        )
        db.add(entry)
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[ActivityLog] Failed to write: {exc}")


# ── Seed default admin ────────────────────────────────────────────────────────
def seed_default_admin():
    db = database.SessionLocal()
    try:
        if not db.query(database.Admin).first():
            db.add(database.Admin(
                username="admin",
                password_hash=hash_password("admin123"),
            ))
            db.commit()
            print("✅ Default admin created  (admin / admin123)")
    finally:
        db.close()

seed_default_admin()


# ── Pydantic Schemas ──────────────────────────────────────────────────────────
class AuthRequest(BaseModel):
    username: str
    password: str

    @field_validator("username", "password")
    @classmethod
    def not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()


class ProductCreate(BaseModel):
    name: str
    category: str
    stock_level: int
    price: float
    low_stock_threshold: Optional[int] = 100

    @field_validator("name", "category")
    @classmethod
    def not_empty(cls, v):
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()

    @field_validator("stock_level")
    @classmethod
    def stock_positive(cls, v):
        if v < 0:
            raise ValueError("Stock level cannot be negative")
        return v

    @field_validator("price")
    @classmethod
    def price_positive(cls, v):
        if v <= 0:
            raise ValueError("Price must be positive")
        return v

    @field_validator("low_stock_threshold")
    @classmethod
    def threshold_positive(cls, v):
        if v is not None and v < 1:
            raise ValueError("Threshold must be at least 1")
        return v


# ── Sales seed helper ─────────────────────────────────────────────────────────
def seed_sales_for_product(product: database.Product, db: Session):
    """
    Generate one month of realistic daily sales data for a new product
    and store it directly in SalesRecord. Skips silently if data already exists.
    """
    existing = db.query(database.SalesRecord)\
                 .filter(database.SalesRecord.product_id == product.id)\
                 .count()
    if existing > 0:
        return

    today = datetime.now()
    first_of_month = today.replace(day=1)
    last_day_prev  = first_of_month - timedelta(days=1)
    prev_month, prev_year, n_days = last_day_prev.month, last_day_prev.year, last_day_prev.day

    rng = random.Random(product.id)
    if product.price < 500:
        lo, hi = 15, 40
    elif product.price < 5000:
        lo, hi = 5, 15
    else:
        lo, hi = 0, 4

    records = [
        database.SalesRecord(
            product_id=product.id,
            sale_date=f"{prev_year}-{prev_month:02d}-{day:02d}",
            units_sold=rng.randint(lo, hi),
        )
        for day in range(1, n_days + 1)
    ]
    db.bulk_save_objects(records)
    db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/signup/")
def signup(req: AuthRequest, db: Session = Depends(get_db)):
    """Register a new staff account (pending admin approval)."""
    name_taken = (
        db.query(database.Admin).filter(database.Admin.username == req.username).first() or
        db.query(database.Staff).filter(database.Staff.username == req.username).first()
    )
    if name_taken:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Username already exists.")

    db.add(database.Staff(
        username=req.username,
        password_hash=hash_password(req.password),
        is_approved=False,
    ))
    db.commit()
    return {"success": True, "message": "Account created! Waiting for admin approval."}


@app.post("/login/")
def login(req: AuthRequest, db: Session = Depends(get_db)):
    """Authenticate admin or approved staff."""
    hashed = hash_password(req.password)

    admin = db.query(database.Admin).filter(database.Admin.username == req.username).first()
    if admin and admin.password_hash == hashed:
        return {"success": True, "message": "Welcome back, Admin.", "role": "admin"}

    staff = db.query(database.Staff).filter(database.Staff.username == req.username).first()
    if staff and staff.password_hash == hashed:
        if not staff.is_approved:
            return {"success": False, "message": "Your account is pending admin approval."}
        return {"success": True, "message": "Login successful.", "role": "staff"}

    return {"success": False, "message": "Incorrect username or password."}


@app.get("/pending-staff/")
def get_pending_staff(db: Session = Depends(get_db)):
    staff = db.query(database.Staff).filter(database.Staff.is_approved == False).all()
    return [{"id": s.id, "username": s.username, "created_at": str(s.created_at)} for s in staff]


@app.put("/approve-staff/{staff_id}")
def approve_staff(staff_id: int, db: Session = Depends(get_db)):
    staff = db.query(database.Staff).filter(database.Staff.id == staff_id).first()
    if not staff:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Staff member not found.")
    staff.is_approved = True
    staff.approved_at = datetime.utcnow()
    db.commit()
    return {"success": True, "message": f"✅ {staff.username} approved successfully."}


# ═══════════════════════════════════════════════════════════════════════════════
# STATS & ANALYTICS ENDPOINTS  (new in v2)
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/stats/")
def get_stats(db: Session = Depends(get_db)):
    """
    Single endpoint that powers all dashboard KPI cards.
    Returns aggregated inventory statistics.
    """
    products = db.query(database.Product).all()
    if not products:
        return {
            "total_products": 0, "low_stock_count": 0,
            "total_inventory_value": 0.0, "category_count": 0,
            "average_stock": 0, "most_stocked": None, "least_stocked": None,
        }

    total_value   = sum(p.stock_level * p.price for p in products)
    low_stock     = [p for p in products if p.is_low_stock]
    categories    = {p.category for p in products}
    avg_stock     = round(sum(p.stock_level for p in products) / len(products))
    most_stocked  = max(products, key=lambda p: p.stock_level)
    least_stocked = min(products, key=lambda p: p.stock_level)

    return {
        "total_products":       len(products),
        "low_stock_count":      len(low_stock),
        "total_inventory_value": round(total_value, 2),
        "category_count":       len(categories),
        "average_stock":        avg_stock,
        "most_stocked":  {"id": most_stocked.id,  "name": most_stocked.name,  "stock": most_stocked.stock_level},
        "least_stocked": {"id": least_stocked.id, "name": least_stocked.name, "stock": least_stocked.stock_level},
    }


@app.get("/low-stock/")
def get_low_stock(db: Session = Depends(get_db)):
    """
    Returns all products below their individual low_stock_threshold.
    Powers the alert banner and the Low Stock dashboard card.
    """
    products = db.query(database.Product).all()
    low = [
        {
            "id": p.id, "name": p.name, "category": p.category,
            "stock_level": p.stock_level, "threshold": p.low_stock_threshold,
            "price": p.price,
        }
        for p in products if p.is_low_stock
    ]
    return {"count": len(low), "items": low}


@app.get("/activity-log/")
def get_activity_log(limit: int = 20, db: Session = Depends(get_db)):
    """
    Returns the most recent audit trail entries.
    Shown in the Admin Panel so admins can see who changed what.
    """
    logs = (
        db.query(database.ActivityLog)
        .order_by(database.ActivityLog.timestamp.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id":           log.id,
            "action":       log.action,
            "detail":       log.detail,
            "performed_by": log.performed_by,
            "timestamp":    str(log.timestamp),
            "product_id":   log.product_id,
        }
        for log in logs
    ]


@app.get("/category-stats/")
def get_category_stats(db: Session = Depends(get_db)):
    """
    Returns per-category aggregates for the Analytics charts:
      - product count
      - total stock
      - total inventory value
    """
    products = db.query(database.Product).all()
    stats: dict = {}
    for p in products:
        if p.category not in stats:
            stats[p.category] = {"category": p.category, "count": 0, "total_stock": 0, "total_value": 0.0}
        stats[p.category]["count"]       += 1
        stats[p.category]["total_stock"] += p.stock_level
        stats[p.category]["total_value"] += round(p.stock_level * p.price, 2)
    return list(stats.values())


# ═══════════════════════════════════════════════════════════════════════════════
# PRODUCT CRUD
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/products/", status_code=status.HTTP_201_CREATED)
def create_product(product: ProductCreate, db: Session = Depends(get_db)):
    if product.stock_level < 100:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Initial stock level must be at least 100 units.")

    db_product = database.Product(**product.model_dump())
    db.add(db_product)
    db.commit()
    db.refresh(db_product)

    # Seed sales history for the new product
    seed_sales_for_product(db_product, db)

    log_activity(db, "CREATE",
                 f"Added '{db_product.name}' (cat: {db_product.category}, "
                 f"stock: {db_product.stock_level}, price: ₹{db_product.price})",
                 product_id=db_product.id)

    return {
        "success": True,
        "message": f"'{db_product.name}' added successfully.",
        "product": {
            "id": db_product.id, "name": db_product.name,
            "category": db_product.category, "stock_level": db_product.stock_level,
            "price": db_product.price,
        }
    }


@app.get("/products/")
def get_all_products(db: Session = Depends(get_db)):
    products = db.query(database.Product).order_by(database.Product.id).all()
    return [
        {
            "id": p.id, "name": p.name, "category": p.category,
            "stock_level": p.stock_level, "price": p.price,
            "low_stock_threshold": p.low_stock_threshold,
            "is_low_stock": p.is_low_stock,
            "inventory_value": p.inventory_value,
            "created_at": str(p.created_at),
        }
        for p in products
    ]


@app.get("/products/{product_id}")
def get_product(product_id: int, db: Session = Depends(get_db)):
    p = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found.")
    return {
        "id": p.id, "name": p.name, "category": p.category,
        "stock_level": p.stock_level, "price": p.price,
        "low_stock_threshold": p.low_stock_threshold,
        "is_low_stock": p.is_low_stock,
        "inventory_value": p.inventory_value,
    }


@app.put("/products/{product_id}")
def update_product(product_id: int, data: ProductCreate, db: Session = Depends(get_db)):
    p = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found.")

    changes = []
    if p.name        != data.name:          changes.append(f"name: '{p.name}' → '{data.name}'")
    if p.category    != data.category:      changes.append(f"category: '{p.category}' → '{data.category}'")
    if p.stock_level != data.stock_level:   changes.append(f"stock: {p.stock_level} → {data.stock_level}")
    if p.price       != data.price:         changes.append(f"price: ₹{p.price} → ₹{data.price}")

    p.name                = data.name
    p.category            = data.category
    p.stock_level         = data.stock_level
    p.price               = data.price
    p.low_stock_threshold = data.low_stock_threshold or 100
    p.updated_at          = datetime.utcnow()
    db.commit()

    log_activity(db, "UPDATE",
                 f"Updated product #{product_id}: " + ("; ".join(changes) if changes else "no changes"),
                 product_id=product_id)

    return {"success": True, "message": "Product updated successfully."}


@app.delete("/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    p = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found.")

    name = p.name
    # Cascade deletes SalesRecord and ActivityLog rows for this product
    db.delete(p)
    db.commit()

    # Write a free-standing log entry (product_id=None since it's deleted)
    log_activity(db, "DELETE", f"Deleted product #{product_id} '{name}'")

    return {"success": True, "message": f"'{name}' deleted successfully."}


# ═══════════════════════════════════════════════════════════════════════════════
# SALES DATA
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/sales/{product_id}")
def get_sales_data(product_id: int, db: Session = Depends(get_db)):
    p = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found.")

    # Seed if this product has no sales records yet (backward compat)
    seed_sales_for_product(p, db)

    records = (
        db.query(database.SalesRecord)
        .filter(database.SalesRecord.product_id == product_id)
        .order_by(database.SalesRecord.sale_date)
        .all()
    )
    return {
        "product_name": p.name,
        "total_records": len(records),
        "sales_data": [{"date": r.sale_date, "units_sold": r.units_sold} for r in records],
    }


@app.get("/export-excel/{product_id}")
def export_excel(product_id: int, db: Session = Depends(get_db)):
    """Stream sales data as an .xlsx file — data pulled from DB, not disk."""
    p = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found.")

    seed_sales_for_product(p, db)
    records = (
        db.query(database.SalesRecord)
        .filter(database.SalesRecord.product_id == product_id)
        .order_by(database.SalesRecord.sale_date)
        .all()
    )

    df = pd.DataFrame([{"Date": r.sale_date, "Units Sold": r.units_sold} for r in records])

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sales Data")
    buffer.seek(0)

    clean_name = p.name.replace(" ", "_")
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{clean_name}_Sales.xlsx"'},
    )


@app.post("/upload-excel/{product_id}")
async def upload_excel(product_id: int, file: UploadFile = File(...),
                       db: Session = Depends(get_db)):
    """
    Accept an edited .xlsx and upsert each row into SalesRecord.
    Expected columns: Date (YYYY-MM-DD), Units Sold (integer).
    """
    p = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found.")

    if not file.filename.endswith(".xlsx"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Invalid format. Please upload a .xlsx file.")

    content = await file.read()
    try:
        df = pd.read_excel(io.BytesIO(content))
    except Exception:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Could not parse the Excel file.")

    # Validate columns
    df.columns = [c.strip() for c in df.columns]
    if "Date" not in df.columns or "Units Sold" not in df.columns:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Excel must have 'Date' and 'Units Sold' columns.")

    upserted = 0
    for _, row in df.iterrows():
        try:
            date_str   = pd.to_datetime(row["Date"]).strftime("%Y-%m-%d")
            units_sold = int(row["Units Sold"])
        except (ValueError, TypeError):
            continue

        existing = (
            db.query(database.SalesRecord)
            .filter_by(product_id=product_id, sale_date=date_str)
            .first()
        )
        if existing:
            existing.units_sold = units_sold
        else:
            db.add(database.SalesRecord(
                product_id=product_id, sale_date=date_str, units_sold=units_sold
            ))
        upserted += 1

    db.commit()
    log_activity(db, "UPDATE",
                 f"Uploaded Excel for product #{product_id}: {upserted} rows upserted.",
                 product_id=product_id)

    return {"success": True, "message": f"✅ {upserted} records updated. AI will use new data."}


# ═══════════════════════════════════════════════════════════════════════════════
# AI PREDICTION
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/predict-demand/{product_id}/{days_in_future}")
def predict_demand(product_id: int, days_in_future: int, db: Session = Depends(get_db)):
    """
    Predict total demand over the next N days using:
      - Ridge Regression model (if available) for long-term seasonal trend
      - Weighted recent momentum: 7-day rolling average (more data = better signal)
      - Price elasticity adjustment
    """
    if days_in_future < 1 or days_in_future > 30:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Days must be between 1 and 30.")

    p = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found.")

    seed_sales_for_product(p, db)

    records = (
        db.query(database.SalesRecord)
        .filter(database.SalesRecord.product_id == product_id)
        .order_by(database.SalesRecord.sale_date)
        .all()
    )

    if not records:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No sales data available.")

    # ── Build momentum signal (last 7 days, or all if fewer) ──────────────────
    recent_n    = min(7, len(records))
    recent_data = records[-recent_n:]
    recent_avg  = sum(r.units_sold for r in recent_data) / recent_n

    # ── AI model prediction ───────────────────────────────────────────────────
    if ai_model:
        try:
            day_num = 1095 + days_in_future
            features = np.array([[
                day_num,
                np.sin(2 * np.pi * day_num / 365),
                np.cos(2 * np.pi * day_num / 365),
            ]])
            model_trend = float(ai_model.predict(features)[0])
        except Exception:
            model_trend = recent_avg * days_in_future
    else:
        model_trend = recent_avg * days_in_future

    # ── Price elasticity adjustment ───────────────────────────────────────────
    # Higher price → lower demand multiplier (log-scaled, centred at ₹1000)
    if p.price > 0:
        elasticity = max(0.5, 1.0 - 0.08 * np.log10(p.price / 1000 + 1))
    else:
        elasticity = 1.0

    # ── Blend: 70% momentum, 30% model trend ─────────────────────────────────
    blended     = (recent_avg * 0.7 * days_in_future) + (model_trend * 0.3)
    final       = max(0, int(blended * elasticity))

    # ── Build human-readable note ─────────────────────────────────────────────
    last_date  = datetime.strptime(recent_data[-1].sale_date, "%Y-%m-%d")
    month_name = last_date.strftime("%B %Y")
    note = (
        f"Based on the last {recent_n} days of sales in {month_name} "
        f"(avg {recent_avg:.1f} units/day). "
        f"Price elasticity factor: {elasticity:.2f}."
    )

    # ── Reorder suggestion ────────────────────────────────────────────────────
    current_stock  = p.stock_level
    reorder_needed = max(0, final - current_stock)
    stock_status   = (
        "critical"  if current_stock < final * 0.25 else
        "low"       if current_stock < final else
        "sufficient"
    )
    reorder_msg = (
        f"CRITICAL: Only {current_stock} units in stock. Reorder {reorder_needed} units immediately."
        if stock_status == "critical" else
        f"LOW: Stock ({current_stock} units) will not cover predicted demand. Reorder {reorder_needed} units."
        if stock_status == "low" else
        f"OK: Current stock ({current_stock} units) covers predicted demand ({final} units)."
    )

    return {
        "status":                 "success",
        "product_id":             p.id,
        "product_name":           p.name,
        "days_in_future":         days_in_future,
        "predicted_sales_volume": final,
        "avg_daily_sales":        round(recent_avg, 2),
        "elasticity_factor":      round(elasticity, 3),
        "note":                   note,
        "current_stock":          current_stock,
        "reorder_quantity":       reorder_needed,
        "stock_status":           stock_status,
        "reorder_message":        reorder_msg,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  SALES PORTAL — Separate login system + sales tracking endpoints
# ══════════════════════════════════════════════════════════════════════════════

class SalesPortalAuth(BaseModel):
    username: str
    password: str
    full_name: Optional[str] = None

class LogSaleRequest(BaseModel):
    product_id: int
    units_sold: int
    staff_username: str
    note: Optional[str] = ""

    @field_validator("units_sold")
    @classmethod
    def validate_units(cls, v):
        if v < 1:
            raise ValueError("Units sold must be at least 1.")
        return v


@app.post("/portal/register/")
def portal_register(req: SalesPortalAuth, db: Session = Depends(get_db)):
    """Register a new sales staff account (separate from admin/staff system)."""
    # Check if username already exists in sales_portal_staff table
    from sqlalchemy import text
    existing = db.execute(
        text("SELECT id FROM sales_portal_staff WHERE username = :u"),
        {"u": req.username}
    ).fetchone()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="Username already taken.")
    db.execute(
        text("""INSERT INTO sales_portal_staff
                (username, password_hash, full_name, created_at, total_sales, total_units)
                VALUES (:u, :p, :fn, :ca, 0, 0)"""),
        {"u": req.username, "p": hash_password(req.password),
         "fn": req.full_name or req.username, "ca": datetime.utcnow()}
    )
    db.commit()
    return {"success": True, "message": "Account created. You can now log in."}


@app.post("/portal/login/")
def portal_login(req: SalesPortalAuth, db: Session = Depends(get_db)):
    """Login for sales portal staff."""
    from sqlalchemy import text
    row = db.execute(
        text("SELECT id, username, full_name, total_sales, total_units FROM sales_portal_staff WHERE username = :u AND password_hash = :p"),
        {"u": req.username, "p": hash_password(req.password)}
    ).fetchone()
    if not row:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail="Invalid username or password.")
    return {
        "success":     True,
        "id":          row[0],
        "username":    row[1],
        "full_name":   row[2],
        "total_sales": row[3],
        "total_units": row[4],
    }


@app.post("/portal/log-sale/")
def portal_log_sale(req: LogSaleRequest, db: Session = Depends(get_db)):
    """Log a sale: deducts stock, records sale, updates staff stats."""
    from sqlalchemy import text

    p = db.query(database.Product).filter(database.Product.id == req.product_id).first()
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found.")
    if p.stock_level < req.units_sold:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"Insufficient stock. Only {p.stock_level} units available.")

    # Deduct stock
    p.stock_level -= req.units_sold

    # Upsert SalesRecord for today
    today = datetime.utcnow().strftime("%Y-%m-%d")
    existing = db.query(database.SalesRecord).filter(
        database.SalesRecord.product_id == req.product_id,
        database.SalesRecord.sale_date == today
    ).first()
    if existing:
        existing.units_sold += req.units_sold
    else:
        db.add(database.SalesRecord(
            product_id=req.product_id,
            sale_date=today,
            units_sold=req.units_sold
        ))

    # Update staff totals
    revenue = round(req.units_sold * p.price, 2)
    db.execute(
        text("""UPDATE sales_portal_staff
                SET total_sales = total_sales + :rev,
                    total_units = total_units + :u
                WHERE username = :staff"""),
        {"rev": revenue, "u": req.units_sold, "staff": req.staff_username}
    )

    # Log activity
    log_activity(db, "SALE",
                 f"{req.staff_username} sold {req.units_sold}x {p.name} = ₹{revenue}",
                 req.staff_username)

    db.commit()
    return {
        "success":       True,
        "message":       f"Sale logged: {req.units_sold}x {p.name}",
        "revenue":       revenue,
        "stock_remaining": p.stock_level,
        "low_stock":     p.stock_level < 100,
    }


@app.get("/portal/my-sales/{username}")
def portal_my_sales(username: str, db: Session = Depends(get_db)):
    """Sales history and stats for a specific staff member."""
    from sqlalchemy import text

    staff = db.execute(
        text("SELECT id, full_name, total_sales, total_units, created_at FROM sales_portal_staff WHERE username = :u"),
        {"u": username}
    ).fetchone()
    if not staff:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Staff not found.")

    # Recent activity from ActivityLog
    logs = db.query(database.ActivityLog).filter(
        database.ActivityLog.performed_by == username,
        database.ActivityLog.action == "SALE"
    ).order_by(database.ActivityLog.timestamp.desc()).limit(20).all()

    return {
        "username":    username,
        "full_name":   staff[1],
        "total_sales": round(float(staff[2] or 0), 2),
        "total_units": int(staff[3] or 0),
        "member_since": str(staff[4])[:10] if staff[4] else "",
        "recent_activity": [
            {"detail": l.detail, "time": str(l.timestamp)[:16]}
            for l in logs
        ]
    }


@app.get("/portal/leaderboard/")
def portal_leaderboard(db: Session = Depends(get_db)):
    """Top 10 sales staff by total revenue."""
    from sqlalchemy import text
    rows = db.execute(
        text("""SELECT username, full_name, total_sales, total_units
                FROM sales_portal_staff
                ORDER BY total_sales DESC LIMIT 10""")
    ).fetchall()
    return [
        {"rank": i+1, "username": r[0], "full_name": r[1],
         "total_sales": round(float(r[2] or 0), 2),
         "total_units": int(r[3] or 0)}
        for i, r in enumerate(rows)
    ]


@app.get("/portal/activity-feed/")
def portal_activity_feed(db: Session = Depends(get_db)):
    """Live feed of all recent sales activity across all staff."""
    logs = db.query(database.ActivityLog).filter(
        database.ActivityLog.action == "SALE"
    ).order_by(database.ActivityLog.timestamp.desc()).limit(30).all()
    return [
        {"detail": l.detail, "staff": l.performed_by,
         "time": str(l.timestamp)[:16]}
        for l in logs
    ]


@app.get("/portal/export-sales/{username}")
def portal_export_sales(username: str, db: Session = Depends(get_db)):
    """Export sales activity as Excel for a staff member."""
    from sqlalchemy import text
    logs = db.query(database.ActivityLog).filter(
        database.ActivityLog.performed_by == username,
        database.ActivityLog.action == "SALE"
    ).order_by(database.ActivityLog.timestamp.desc()).all()

    rows = [{"Activity": l.detail, "Time": str(l.timestamp)[:16]} for l in logs]
    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["Activity", "Time"])

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name="My Sales")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{username}_sales.xlsx"'}
    )