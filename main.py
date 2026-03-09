from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
import database
import pickle
import numpy as np
import os
import hashlib
import random
import pandas as pd
import calendar
import shutil
from datetime import datetime, timedelta

# 1. Initialize Database (will auto-create the new Staff table)
database.Base.metadata.create_all(bind=database.engine)

app = FastAPI(title="SmartStock AI API")

# 2. Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. Load the AI Model securely
try:
    with open("ai_engine/final_ai_brain.pkl", "rb") as f:
        ai_model = pickle.load(f)
    print("✅ Final AI Brain loaded successfully!")
except FileNotFoundError:
    ai_model = None
    print("⚠️ Warning: Final AI Brain not found. Using fallback logic.")

# 4. Utilities
def get_password_hash(password: str):
    return hashlib.sha256(password.encode()).hexdigest()

def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- AUTO-CREATE ADMIN ---
def create_default_admin():
    db = database.SessionLocal()
    try:
        admin = db.query(database.Admin).first()
        if not admin:
            print("⚙️ No admin found. Creating default admin account...")
            default_admin = database.Admin(
                username="admin", 
                password_hash=get_password_hash("admin123")
            )
            db.add(default_admin)
            db.commit()
            print("✅ Default admin created! Username: admin | Password: admin123")
    finally:
        db.close()

create_default_admin()

# --- PYDANTIC SCHEMAS ---
class ProductCreate(BaseModel):
    name: str
    category: str
    stock_level: int
    price: float

class AuthRequest(BaseModel):
    username: str
    password: str

# --- EXCEL (.XLSX) HELPER FUNCTION ---
def get_or_create_sales_data(product):
    filename = f"sales_data_{product.id}.xlsx"
    sales_history = []
    
    today = datetime.now()
    first_of_this_month = today.replace(day=1)
    last_day_prev_month = first_of_this_month - timedelta(days=1)
    prev_month = last_day_prev_month.month
    prev_year = last_day_prev_month.year
    num_days_in_month = last_day_prev_month.day

    if os.path.exists(filename):
        df = pd.read_excel(filename)
        df['Date'] = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d') 
        for _, row in df.iterrows():
            sales_history.append({"date": row["Date"], "units_sold": int(row["Units Sold"])})
    else:
        random.seed(product.id)
        if product.price < 500:
            min_sales, max_sales = 15, 40
        elif product.price < 5000:
            min_sales, max_sales = 5, 15
        else:
            min_sales, max_sales = 0, 4

        for i in range(1, num_days_in_month + 1):
            date_str = f"{prev_year}-{prev_month:02d}-{i:02d}"
            units = random.randint(min_sales, max_sales)
            sales_history.append({"date": date_str, "units_sold": units})
        random.seed()
        
        df = pd.DataFrame([{"Date": item["date"], "Units Sold": item["units_sold"]} for item in sales_history])
        df.to_excel(filename, index=False)
                
    return sales_history

# --- AUTHENTICATION & APPROVAL ENDPOINTS ---

@app.post("/signup/")
def signup(req: AuthRequest, db: Session = Depends(get_db)):
    # Check if username already exists anywhere
    if db.query(database.Admin).filter(database.Admin.username == req.username).first() or \
       db.query(database.Staff).filter(database.Staff.username == req.username).first():
        return {"success": False, "message": "Username already exists!"}
    
    new_staff = database.Staff(
        username=req.username,
        password_hash=get_password_hash(req.password),
        is_approved=False
    )
    db.add(new_staff)
    db.commit()
    return {"success": True, "message": "Account created! Please wait for Admin approval."}

@app.post("/login/")
def login(req: AuthRequest, db: Session = Depends(get_db)):
    hashed_pw = get_password_hash(req.password)
    
    # 1. Try logging in as Admin
    admin = db.query(database.Admin).filter(database.Admin.username == req.username).first()
    if admin and admin.password_hash == hashed_pw:
        return {"success": True, "message": "Admin Login successful!", "role": "admin"}
    
    # 2. Try logging in as Staff
    staff = db.query(database.Staff).filter(database.Staff.username == req.username).first()
    if staff and staff.password_hash == hashed_pw:
        if not staff.is_approved:
            return {"success": False, "message": "Your account is pending admin approval."}
        return {"success": True, "message": "Login successful!", "role": "staff"}
        
    return {"success": False, "message": "Incorrect username or password."}

@app.get("/pending-staff/")
def get_pending_staff(db: Session = Depends(get_db)):
    staff = db.query(database.Staff).filter(database.Staff.is_approved == False).all()
    return [{"id": s.id, "username": s.username} for s in staff]

@app.put("/approve-staff/{staff_id}")
def approve_staff(staff_id: int, db: Session = Depends(get_db)):
    staff = db.query(database.Staff).filter(database.Staff.id == staff_id).first()
    if staff:
        staff.is_approved = True
        db.commit()
        return {"success": True, "message": f"User {staff.username} approved!"}
    return {"success": False, "message": "Staff not found."}

# --- PRODUCT & AI ENDPOINTS ---

@app.post("/products/")
def create_product(product: ProductCreate, db: Session = Depends(get_db)):
    if product.stock_level < 100:
        return {"success": False, "message": "Initial stock level must be at least 100 units."}
        
    db_product = database.Product(**product.dict())
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    return {"success": True, "message": "Product added successfully", "product": db_product}

@app.get("/products/")
def get_all_products(db: Session = Depends(get_db)):
    return db.query(database.Product).all()

@app.put("/products/{product_id}")
def update_product(product_id: int, product_data: ProductCreate, db: Session = Depends(get_db)):
    product = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not product:
        return {"success": False, "message": "Product not found."}
    
    product.name = product_data.name
    product.category = product_data.category
    product.stock_level = product_data.stock_level
    product.price = product_data.price
    db.commit()
    return {"success": True, "message": "Product updated successfully!"}

@app.delete("/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not product:
        return {"success": False, "message": "Product not found."}
    db.delete(product)
    db.commit()
    
    filename = f"sales_data_{product_id}.xlsx"
    if os.path.exists(filename):
        os.remove(filename)
        
    return {"success": True, "message": f"Product #{product_id} deleted successfully."}

@app.get("/sales/{product_id}")
def get_sales_data(product_id: int, db: Session = Depends(get_db)):
    product = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not product:
        return {"error": "Product not found."}
    
    sales_history = get_or_create_sales_data(product)
    return {"product_name": product.name, "sales_data": sales_history}

@app.get("/export-excel/{product_id}")
def export_excel(product_id: int, db: Session = Depends(get_db)):
    product = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not product:
        return {"error": "Product not found."}
    
    get_or_create_sales_data(product)
    filename = f"sales_data_{product_id}.xlsx"
    clean_name = product.name.replace(" ", "_")
    
    return FileResponse(
        path=filename, 
        filename=f"{clean_name}_Sales_Data.xlsx", 
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

@app.post("/upload-excel/{product_id}")
async def upload_excel_data(product_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    product = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not product:
        return {"error": "Product not found."}
    
    if not file.filename.endswith('.xlsx'):
        return {"error": "Invalid file format. Please upload the .xlsx file."}
        
    filename = f"sales_data_{product_id}.xlsx"
    with open(filename, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    return {"success": True, "message": "Data successfully updated! The AI is now using your new numbers."}

@app.get("/predict-demand/{product_id}/{days_in_future}")
def predict_future_demand(product_id: int, days_in_future: int, db: Session = Depends(get_db)):
    if days_in_future > 30:
        return {"error": "Maximum prediction limit is 30 days."}
        
    product = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not product:
        return {"error": "Product not found."}
    
    sales_history = get_or_create_sales_data(product)
    if not sales_history:
        return {"error": "No sales data found."}

    sorted_sales = sorted(sales_history, key=lambda x: x["date"])
    recent_sales_data = sorted_sales[-3:]
    
    recent_sales = [item["units_sold"] for item in recent_sales_data]
    last_3_days_avg = sum(recent_sales) / len(recent_sales)
    
    last_date_obj = datetime.strptime(recent_sales_data[-1]["date"], "%Y-%m-%d")
    month_name = last_date_obj.strftime("%B")
    date_list = [str(datetime.strptime(item["date"], "%Y-%m-%d").day) for item in recent_sales_data]
    date_str_formatted = ", ".join(date_list)
    
    if not ai_model:
        final_prediction = last_3_days_avg * days_in_future
    else:
        target_day = 1095 + days_in_future
        future_features = np.array([[target_day, np.sin(2 * np.pi * target_day / 365), np.cos(2 * np.pi * target_day / 365)]])
        base_trend = ai_model.predict(future_features)[0]
        final_prediction = (base_trend * 0.3) + (last_3_days_avg * 0.7 * days_in_future)
    
    return {
        "status": "Success",
        "product_name": product.name,
        "days_in_future": days_in_future,
        "predicted_sales_volume": max(0, int(final_prediction)),
        "note": f"Based on the latest data found for {month_name} ({date_str_formatted}): {last_3_days_avg:.1f} units/day."
    }