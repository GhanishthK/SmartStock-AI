from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from pydantic import BaseModel
import database
import pickle
import numpy as np
import os
import hashlib
import math

# 1. Initialize the Database Tables
database.Base.metadata.create_all(bind=database.engine)

# 2. Initialize the FastAPI Application
app = FastAPI(title="SmartStock AI API")

# 3. Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 4. Load the BRAND NEW AI Model securely
model_path = "ai_engine/final_ai_brain.pkl"

try:
    with open(model_path, "rb") as f:
        ai_model = pickle.load(f)
    print("✅ Final AI Brain loaded successfully!")
except FileNotFoundError:
    ai_model = None
    print("⚠️ Warning: Final AI Brain not found. Did you run train_model.py?")

# 5. Password Hashing Utility
def get_password_hash(password: str):
    return hashlib.sha256(password.encode()).hexdigest()

# 6. Startup Event: Create Default Admin
@app.on_event("startup")
def create_default_admin():
    db = database.SessionLocal()
    admin = db.query(database.Admin).filter(database.Admin.username == "admin").first()
    if not admin:
        hashed_pw = get_password_hash("password123")
        new_admin = database.Admin(username="admin", password_hash=hashed_pw)
        db.add(new_admin)
        db.commit()
        print("✅ Default admin account created!")
    db.close()

# 7. Database Session Dependency
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

# --- API ENDPOINTS ---

@app.post("/login/")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    admin = db.query(database.Admin).filter(database.Admin.username == req.username).first()
    if not admin or admin.password_hash != get_password_hash(req.password):
        return {"success": False, "message": "Incorrect username or password."}
    return {"success": True, "message": "Login successful!"}

@app.post("/products/")
def create_product(product: ProductCreate, db: Session = Depends(get_db)):
    db_product = database.Product(**product.dict())
    db.add(db_product)
    db.commit()
    db.refresh(db_product)
    return {"message": "Product added successfully", "product": db_product}

@app.get("/products/")
def get_all_products(db: Session = Depends(get_db)):
    return db.query(database.Product).all()

@app.delete("/products/{product_id}")
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not product:
        return {"success": False, "message": "Product not found."}
    
    db.delete(product)
    db.commit()
    return {"success": True, "message": f"Product #{product_id} deleted successfully."}

# --- HIGH ACCURACY AI ENDPOINT ---
@app.get("/predict-demand/{product_id}/{days_in_future}")
def predict_future_demand(product_id: int, days_in_future: int, db: Session = Depends(get_db)):
    if not ai_model:
        return {"error": "AI model not found. Run train_model.py!"}
    
    product = db.query(database.Product).filter(database.Product.id == product_id).first()
    if not product:
        return {"error": f"Product with ID #{product_id} not found."}
    
    # We trained on 3 years (1095 days). Target day is 1095 + user input
    target_day = 1095 + days_in_future
    
    # Get the baseline market prediction using Trend + Yearly Seasonality
    future_features = np.array([[
        target_day,
        np.sin(2 * np.pi * target_day / 365),
        np.cos(2 * np.pi * target_day / 365)
    ]])
    base_prediction = ai_model.predict(future_features)[0]
    
    # LOGARITHMIC PRICE ELASTICITY
    safe_price = product.price if product.price > 0 else 1
    price_adjustment = math.log((500 / safe_price) + 1.5) 
    
    final_prediction = base_prediction * price_adjustment
    
    return {
        "status": "Success",
        "product_name": product.name,
        "days_in_future": days_in_future,
        "predicted_sales_volume": max(0, int(final_prediction)) 
    }