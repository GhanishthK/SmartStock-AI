"""
SmartStock AI — FastAPI Backend
=================================
"""

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel, field_validator
from typing import Optional, List
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
def run_migrations():
    from sqlalchemy import text, inspect
    inspector = inspect(database.engine)

    required_columns = {
        "admins": [("created_at", "DATETIME")],
        "staff": [("created_at", "DATETIME"), ("approved_at", "DATETIME")],
        "products": [
            ("low_stock_threshold", "INTEGER DEFAULT 100 NOT NULL"),
            ("created_at", "DATETIME"),
            ("updated_at", "DATETIME"),
        ],
    }

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
                continue 

            for col_name, col_def in columns:
                if col_name not in existing:
                    try:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))
                        conn.commit()
                        print(f"✅ Migration: added '{col_name}' to '{table}'")
                    except Exception as e:
                        print(f"⚠️  Migration skipped ({table}.{col_name}): {e}")

run_migrations()

app = FastAPI(title="SmartStock AI API", version="2.0.0")

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
    try:
        entry = database.ActivityLog(
            product_id=product_id, action=action,
            detail=detail, performed_by=performed_by,
        )
        db.add(entry)
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"[ActivityLog] Failed to write: {exc}")


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
    id: int  # <-- Added ID field
    name: str
    category: str
    stock_level: int
    price: float
    low_stock_threshold: Optional[int] = 100

    @field_validator("id")
    @classmethod
    def id_positive(cls, v):
        if v <= 0:
            raise ValueError("ID must be a positive number")
        return v

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


def seed_sales_for_product(product: database.Product, db: Session):
    existing = db.query(database.SalesRecord).filter(database.SalesRecord.product_id == product.id).count()
    if existing > 0: return

    today = datetime.now()
    first_of_month = today.replace(day=1)
    last_day_prev  = first_of_month - timedelta(days=1)
    prev_month, prev_year, n_days = last_day_prev.month, last_day_prev.year, last_day_prev.day

    rng = random.Random(product.id)
    if product.price < 500: lo, hi = 15, 40
    elif product.price < 5000: lo, hi = 5, 15
    else: lo, hi = 0, 4

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
    name_taken = (
        db.query(database.Admin).filter(database.Admin.username == req.username).first() or
        db.query(database.Staff).filter(database.Staff.username == req.username).first()
    )
    if name_taken: raise HTTPException(status.HTTP_409_CONFLICT, detail="Username already exists.")

    db.add(database.Staff(username=req.username, password_hash=hash_password(req.password), is_approved=False))
    db.commit()
    return {"success": True, "message": "Account created! Waiting for admin approval."}


@app.post("/login/")
def login(req: AuthRequest, db: Session = Depends(get_db)):
    hashed = hash_password(req.password)
    admin = db.query(database.Admin).filter(database.Admin.username == req.username).first()
    if admin and admin.password_hash == hashed:
        return {"success": True, "message": "Welcome back, Admin.", "role": "admin"}

    staff = db.query(database.Staff).filter(database.Staff.username == req.username).first()
    if staff and staff.password_hash == hashed:
        if not staff.is_approved: return {"success": False, "message": "Your account is pending admin approval."}
        return {"success": True, "message": "Login successful.", "role": "staff"}

    return {"success": False, "message": "Incorrect username or password."}


@app.get("/pending-staff/")
def get_pending_staff(db: Session = Depends(get_db)):
    staff = db.query(database.Staff).filter(database.Staff.is_approved == False).all()
    return [{"id": s.id, "username": s.username, "created_at": str(s.created_at)} for s in staff]


@app.put("/approve-staff/{staff_id}")
def approve_staff(staff_id: int, db: Session = Depends(get_db)):
    staff = db.query(database.Staff).filter(database.Staff.id == staff_id).first()
    if not staff: raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Staff member not found.")
    staff.is_approved = True
    staff.approved_at = datetime.utcnow()
    db.commit()
    return {"success": True, "message": f"✅ {staff.username} approved successfully."}


# ═══════════════════════════════════════════════════════════════════════════════
# STATS & ANALYTICS ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════
@app.get("/stats/")
def get_stats(db: Session = Depends(get_db)):
    products = db.query(database.Product).all()
    if not products:
        return {"total_products": 0, "low_stock_count": 0, "total_inventory_value": 0.0, "category_count": 0, "average_stock": 0}

    return {
        "total_products": len(products),
        "low_stock_count": len([p for p in products if p.is_low_stock]),
        "total_inventory_value": round(sum(p.stock_level * p.price for p in products), 2),
        "category_count": len({p.category for p in products}),
        "average_stock": round(sum(p.stock_level for p in products) / len(products)),
    }

@app.get("/low-stock/")
def get_low_stock(db: Session = Depends(get_db)):
    products = db.query(database.Product).all()
    low = [{"id": p.id, "name": p.name, "category": p.category, "stock_level": p.stock_level, "price": p.price} for p in products if p.is_low_stock]
    return {"count": len(low), "items": low}

@app.get("/activity-log/")
def get_activity_log(limit: int = 20, db: Session = Depends(get_db)):
    logs = db.query(database.ActivityLog).order_by(database.ActivityLog.timestamp.desc()).limit(limit).all()
    return [{"action": log.action, "detail": log.detail, "performed_by": log.performed_by, "timestamp": str(log.timestamp)} for log in logs]


# ═══════════════════════════════════════════════════════════════════════════════
# PRODUCT CRUD
# ═══════════════════════════════════════════════════════════════════════════════
@app.post("/products/", status_code=status.HTTP_201_CREATED)
def create_product(product: ProductCreate, db: Session = Depends(get_db)):
    # Check if ID already exists
    existing = db.query(database.Product).filter(database.Product.id == product.id).first()
    if existing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Product ID #{product.id} already exists.")

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
                 f"Added '{db_product.name}' (ID: #{db_product.id}, cat: {db_product.category}, "
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
    return [{"id": p.id, "name": p.name, "category": p.category, "stock_level": p.stock_level, "price": p.price} for p in products]

@app.put("/products/{product_id}")
def update_product(product_id: int, data: ProductCreate, db: Session = Depends(get_db)):
    p = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not p: raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found.")
    p.name = data.name; p.category = data.category; p.stock_level = data.stock_level; p.price = data.price
    db.commit()
    log_activity(db, "UPDATE", f"Updated product #{product_id}", product_id=product_id)
    return {"success": True, "message": "Product updated successfully."}

@app.delete("/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    p = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not p: raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found.")
    db.delete(p)
    db.commit()
    log_activity(db, "DELETE", f"Deleted product #{product_id}")
    return {"success": True}


# ═══════════════════════════════════════════════════════════════════════════════
# SALES DATA
# ═══════════════════════════════════════════════════════════════════════════════
@app.get("/sales/{product_id}")
def get_sales_data(product_id: int, db: Session = Depends(get_db)):
    p = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not p: raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Product not found.")
    seed_sales_for_product(p, db)
    records = db.query(database.SalesRecord).filter(database.SalesRecord.product_id == product_id).order_by(database.SalesRecord.sale_date).all()
    return {"product_name": p.name, "sales_data": [{"date": r.sale_date, "units_sold": r.units_sold} for r in records]}

@app.get("/export-excel/{product_id}")
def export_excel(product_id: int, db: Session = Depends(get_db)):
    p = db.query(database.Product).filter(database.Product.id == product_id).first()
    records = db.query(database.SalesRecord).filter(database.SalesRecord.product_id == product_id).order_by(database.SalesRecord.sale_date).all()
    df = pd.DataFrame([{"Date": r.sale_date, "Units Sold": r.units_sold} for r in records])
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sales Data")
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="{p.name}_Sales.xlsx"'})

@app.post("/upload-excel/{product_id}")
async def upload_excel(product_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    df = pd.read_excel(io.BytesIO(content))
    df.columns = [c.strip() for c in df.columns]
    for _, row in df.iterrows():
        try:
            date_str = pd.to_datetime(row["Date"]).strftime("%Y-%m-%d")
            units_sold = int(row["Units Sold"])
            existing = db.query(database.SalesRecord).filter_by(product_id=product_id, sale_date=date_str).first()
            if existing: existing.units_sold = units_sold
            else: db.add(database.SalesRecord(product_id=product_id, sale_date=date_str, units_sold=units_sold))
        except: continue
    db.commit()
    return {"success": True, "message": "Records updated successfully."}


# ═══════════════════════════════════════════════════════════════════════════════
# AI PREDICTION
# ═══════════════════════════════════════════════════════════════════════════════
@app.get("/predict-demand/{product_id}/{days_in_future}")
def predict_demand(product_id: int, days_in_future: int, db: Session = Depends(get_db)):
    p = db.query(database.Product).filter(database.Product.id == product_id).first()
    records = db.query(database.SalesRecord).filter(database.SalesRecord.product_id == product_id).all()
    recent_n = min(7, len(records))
    recent_avg = sum(r.units_sold for r in records[-recent_n:]) / recent_n if recent_n > 0 else 0

    if ai_model:
        try:
            day_num = 1095 + days_in_future
            model_trend = float(ai_model.predict(np.array([[day_num, np.sin(2 * np.pi * day_num / 365), np.cos(2 * np.pi * day_num / 365)]]))[0])
        except: model_trend = recent_avg * days_in_future
    else: model_trend = recent_avg * days_in_future

    elasticity = max(0.5, 1.0 - 0.08 * np.log10(p.price / 1000 + 1)) if p.price > 0 else 1.0
    final = max(0, int(((recent_avg * 0.7 * days_in_future) + (model_trend * 0.3)) * elasticity))

    return {"product_name": p.name, "predicted_sales_volume": final, "note": f"Based on recent averages. Elasticity factor: {elasticity:.2f}"}


# ══════════════════════════════════════════════════════════════════════════════
#  SALES PORTAL — Multi-Item Sales Logic with Discount
# ══════════════════════════════════════════════════════════════════════════════
class SalesPortalAuth(BaseModel):
    username: str
    password: str
    full_name: Optional[str] = None

class SaleItem(BaseModel):
    product_id: int
    units_sold: int

    @field_validator("units_sold")
    @classmethod
    def validate_units(cls, v):
        if v < 1: raise ValueError("Units sold must be at least 1.")
        return v

class LogSaleMultiRequest(BaseModel):
    items: List[SaleItem]
    staff_username: str
    note: Optional[str] = ""
    discount: Optional[float] = 0.0  # Added Discount Field

@app.post("/portal/register/")
def portal_register(req: SalesPortalAuth, db: Session = Depends(get_db)):
    from sqlalchemy import text
    existing = db.execute(text("SELECT id FROM sales_portal_staff WHERE username = :u"), {"u": req.username}).fetchone()
    if existing: raise HTTPException(status.HTTP_409_CONFLICT, detail="Username already taken.")
    db.execute(
        text("INSERT INTO sales_portal_staff (username, password_hash, full_name, created_at, total_sales, total_units) VALUES (:u, :p, :fn, :ca, 0, 0)"),
        {"u": req.username, "p": hash_password(req.password), "fn": req.full_name or req.username, "ca": datetime.utcnow()}
    )
    db.commit()
    return {"success": True, "message": "Account created. You can now log in."}


@app.post("/portal/login/")
def portal_login(req: SalesPortalAuth, db: Session = Depends(get_db)):
    from sqlalchemy import text
    row = db.execute(
        text("SELECT id, username, full_name, total_sales, total_units FROM sales_portal_staff WHERE username = :u AND password_hash = :p"),
        {"u": req.username, "p": hash_password(req.password)}
    ).fetchone()
    if not row: raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password.")
    return {"success": True, "id": row[0], "username": row[1], "full_name": row[2], "total_sales": row[3], "total_units": row[4]}


@app.post("/portal/log-sale/")
def portal_log_sale(req: LogSaleMultiRequest, db: Session = Depends(get_db)):
    """Logs a single transaction containing multiple products with an optional discount."""
    from sqlalchemy import text

    if not req.items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No items selected.")

    # 1. Pre-check stock for all items before making any modifications
    for item in req.items:
        p = db.query(database.Product).filter(database.Product.id == item.product_id).first()
        if not p:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Product ID #{item.product_id} not found.")
        if p.stock_level < item.units_sold:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Insufficient stock for '{p.name}'. Only {p.stock_level} available.")

    total_revenue = 0.0
    total_units = 0
    sale_summaries = []
    low_stock_alerts = []
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # 2. Process the sale for each item
    for item in req.items:
        p = db.query(database.Product).filter(database.Product.id == item.product_id).first()
        
        p.stock_level -= item.units_sold
        revenue = round(item.units_sold * p.price, 2)
        total_revenue += revenue
        total_units += item.units_sold
        sale_summaries.append(f"{item.units_sold}x {p.name}")

        if p.stock_level < 100:
            low_stock_alerts.append(p.name)

        existing = db.query(database.SalesRecord).filter(
            database.SalesRecord.product_id == item.product_id,
            database.SalesRecord.sale_date == today
        ).first()
        
        if existing:
            existing.units_sold += item.units_sold
        else:
            db.add(database.SalesRecord(product_id=item.product_id, sale_date=today, units_sold=item.units_sold))

    # 3. Apply Discount
    discount_applied = min(total_revenue, req.discount or 0.0) # Prevent negative totals
    final_revenue = total_revenue - discount_applied

    # 4. Update staff overall totals using final discounted revenue
    db.execute(
        text("""UPDATE sales_portal_staff
                SET total_sales = total_sales + :rev,
                    total_units = total_units + :u
                WHERE username = :staff"""),
        {"rev": final_revenue, "u": total_units, "staff": req.staff_username}
    )

    # 5. Log the combined activity
    items_str = ", ".join(sale_summaries)
    note_str = f" ({req.note})" if req.note else ""
    discount_str = f" (Includes ₹{discount_applied:.2f} Discount)" if discount_applied > 0 else ""
    log_activity(db, "SALE", f"{req.staff_username} sold {items_str} = ₹{final_revenue:.2f}{discount_str}{note_str}", performed_by=req.staff_username)

    db.commit()
    
    return {
        "success": True,
        "revenue": final_revenue,
        "items_summary": items_str,
        "low_stock_alerts": low_stock_alerts
    }


@app.get("/portal/my-sales/{username}")
def portal_my_sales(username: str, db: Session = Depends(get_db)):
    from sqlalchemy import text
    staff = db.execute(text("SELECT full_name, total_sales, total_units, created_at FROM sales_portal_staff WHERE username = :u"), {"u": username}).fetchone()
    if not staff: raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Staff not found.")

    logs = db.query(database.ActivityLog).filter(database.ActivityLog.performed_by == username, database.ActivityLog.action == "SALE").order_by(database.ActivityLog.timestamp.desc()).limit(20).all()
    return {"full_name": staff[0], "total_sales": staff[1], "total_units": staff[2], "recent_activity": [{"detail": l.detail, "time": str(l.timestamp)[:16]} for l in logs]}

@app.get("/portal/leaderboard/")
def portal_leaderboard(db: Session = Depends(get_db)):
    from sqlalchemy import text
    rows = db.execute(text("SELECT username, full_name, total_sales, total_units FROM sales_portal_staff ORDER BY total_sales DESC LIMIT 10")).fetchall()
    return [{"rank": i+1, "username": r[0], "full_name": r[1], "total_sales": r[2], "total_units": r[3]} for i, r in enumerate(rows)]

@app.get("/portal/export-sales/{username}")
def portal_export_sales(username: str, db: Session = Depends(get_db)):
    """Export sales activity as Excel for a staff member."""
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