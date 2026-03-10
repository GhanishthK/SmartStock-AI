"""
SmartStock AI — Database Models
=================================
Improvements over original:
  - SalesRecord table: sales data now lives in the DB, not scattered Excel files
  - ActivityLog table: audit trail of every create/update/delete action
  - Timestamps on Product (created_at, updated_at)
  - low_stock_threshold per product (defaults to 100, but can be customised)
  - Proper indexes for faster queries
"""

from sqlalchemy import (
    Column, Integer, String, Float, Boolean,
    DateTime, ForeignKey, Text, create_engine, Index
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime

# ── Database URL ────────────────────────────────────────────────────────────
SQLALCHEMY_DATABASE_URL = "sqlite:///./inventory.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ── Helper ───────────────────────────────────────────────────────────────────
def now():
    return datetime.utcnow()


# ── Models ───────────────────────────────────────────────────────────────────

class Admin(Base):
    """System administrator — full access."""
    __tablename__ = "admins"

    id            = Column(Integer, primary_key=True, index=True)
    username      = Column(String(64), unique=True, index=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    created_at    = Column(DateTime, default=now)


class Staff(Base):
    """
    Regular staff accounts.
    New registrations land here with is_approved=False
    until an Admin approves them.
    """
    __tablename__ = "staff"

    id            = Column(Integer, primary_key=True, index=True)
    username      = Column(String(64), unique=True, index=True, nullable=False)
    password_hash = Column(String(128), nullable=False)
    is_approved   = Column(Boolean, default=False, nullable=False)
    created_at    = Column(DateTime, default=now)
    approved_at   = Column(DateTime, nullable=True)


class Product(Base):
    """
    Core inventory item.
    low_stock_threshold: alert fires when stock_level falls below this value.
    """
    __tablename__ = "products"

    id                  = Column(Integer, primary_key=True, index=True)
    name                = Column(String(128), index=True, nullable=False)
    category            = Column(String(64),  index=True, nullable=False)
    stock_level         = Column(Integer, default=0, nullable=False)
    price               = Column(Float, nullable=False)
    low_stock_threshold = Column(Integer, default=100, nullable=False)
    created_at          = Column(DateTime, default=now)
    updated_at          = Column(DateTime, default=now, onupdate=now)

    # Relationships
    sales_records = relationship("SalesRecord", back_populates="product",
                                 cascade="all, delete-orphan")
    activity_logs = relationship("ActivityLog", back_populates="product",
                                 cascade="all, delete-orphan")

    # Composite index for common filter pattern
    __table_args__ = (
        Index("ix_products_category_stock", "category", "stock_level"),
    )

    @property
    def is_low_stock(self):
        return self.stock_level < self.low_stock_threshold

    @property
    def inventory_value(self):
        return round(self.stock_level * self.price, 2)


class SalesRecord(Base):
    """
    Daily sales entry per product.
    Replaces the old per-product Excel files with a proper relational table,
    making analytics queries fast and reliable.
    """
    __tablename__ = "sales_records"

    id          = Column(Integer, primary_key=True, index=True)
    product_id  = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    sale_date   = Column(String(10), nullable=False)   # stored as "YYYY-MM-DD"
    units_sold  = Column(Integer, default=0, nullable=False)
    created_at  = Column(DateTime, default=now)

    product = relationship("Product", back_populates="sales_records")

    __table_args__ = (
        # Ensures one record per product per day; speeds up range queries
        Index("ix_sales_product_date", "product_id", "sale_date", unique=True),
    )


class ActivityLog(Base):
    """
    Audit trail.
    Every create / update / delete on a product writes a row here
    so admins can see who changed what and when.
    """
    __tablename__ = "activity_logs"

    id         = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"),
                        nullable=True, index=True)
    action     = Column(String(32), nullable=False)   # "CREATE" | "UPDATE" | "DELETE"
    detail     = Column(Text, nullable=True)           # human-readable description
    performed_by = Column(String(64), nullable=True)  # username (from auth token / session)
    timestamp  = Column(DateTime, default=now, index=True)

    product = relationship("Product", back_populates="activity_logs")