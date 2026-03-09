from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
import database
import pickle
import numpy as np
import os
import hashlib
import random
import csv
import calendar
from datetime import datetime, timedelta

# 1. Initialize Database
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

# --- PYDANTIC SCHEMAS ---
class ProductCreate(BaseModel):
    name: str
    category: str
    stock_level: int
    price: float

class LoginRequest(BaseModel):
    username: str
    password: str

# --- CSV HELPER FUNCTION ---
def get_or_create_sales_data(product):
    filename = f"sales_data_{product.id}.csv"
    sales_history = []
    
    # Check if the file already exists (reads your manual edits)
    if os.path.exists(filename):
        with open(filename, mode='r') as file:
            reader = csv.DictReader(file)
            for row in reader:
                sales_history.append({"date": row["Date"], "units_sold": int(row["Units Sold"])})
    else:
        # Generate baseline data once, then save it to the file
        random.seed(product.id)
        if product.price < 500:
            min_sales, max_sales = 15, 40
        elif product.price < 5000:
            min_sales, max_sales = 5, 15
        else:
            min_sales, max_sales = 0, 4

        today = datetime.now()
        for i in range(30):
            date_str = (today - timedelta(days=29-i)).strftime("%Y-%m-%d")
            units = random.randint(min_sales, max_sales)
            sales_history.append({"date": date_str, "units_sold": units})
        random.seed() # Reset
        
        # Create the physical file
        with open(filename, mode='w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(["Date", "Units Sold"])
            for item in sales_history:
                writer.writerow([item["date"], item["units_sold"]])
                
    return sales_history

# --- API ENDPOINTS ---

@app.post("/login/")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    admin = db.query(database.Admin).filter(database.Admin.username == req.username).first()
    if not admin or admin.password_hash != get_password_hash(req.password):
        return {"success": False, "message": "Incorrect username or password."}
    return {"success": True, "message": "Login successful!"}

@app.post("/products/")
def create_product(product: ProductCreate, db: Session = Depends(get_db)):
    # Strict Backend Validation for Minimum Stock
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
    
    # Optional clean-up: remove the CSV file when the product is deleted
    filename = f"sales_data_{product_id}.csv"
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

@app.get("/predict-demand/{product_id}/{days_in_future}")
def predict_future_demand(product_id: int, days_in_future: int, db: Session = Depends(get_db)):
    if days_in_future > 30:
        return {"error": "Maximum prediction limit is 30 days."}
        
    product = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not product:
        return {"error": "Product not found."}
    
    sales_history = get_or_create_sales_data(product)
    
    # Grab the exact last 3 entries from the file
    recent_sales = [item["units_sold"] for item in sales_history[-3:]] 
    last_3_days_avg = sum(recent_sales) / 3 if recent_sales else 0
    
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
        "note": f"Based on the last 3 days of your static CSV file: {last_3_days_avg:.1f} units/day."
    }