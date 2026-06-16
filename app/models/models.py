from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Date,
    ForeignKey, Boolean, Text, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    employee_no = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    department = Column(String(100))
    position = Column(String(100))
    hire_date = Column(Date, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    accounts = relationship("LeaveAccount", back_populates="employee")
    applications = relationship("LeaveApplication", back_populates="employee")


class LeaveType(Base):
    __tablename__ = "leave_types"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    annual_grant_days = Column(Float, default=0.0)
    carry_over_days = Column(Float, default=0.0)
    expire_months = Column(Integer, default=12)
    unit = Column(String(20), default="day")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())

    accounts = relationship("LeaveAccount", back_populates="leave_type")
    transactions = relationship("LeaveTransaction", back_populates="leave_type")
    applications = relationship("LeaveApplication", back_populates="leave_type")


class LeaveAccount(Base):
    __tablename__ = "leave_accounts"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    leave_type_id = Column(Integer, ForeignKey("leave_types.id"), nullable=False)
    year = Column(Integer, nullable=False)
    balance = Column(Float, default=0.0, nullable=False)
    frozen_balance = Column(Float, default=0.0)
    version = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    employee = relationship("Employee", back_populates="accounts")
    leave_type = relationship("LeaveType", back_populates="accounts")
    transactions = relationship("LeaveTransaction", back_populates="account")

    __table_args__ = (
        UniqueConstraint("employee_id", "leave_type_id", "year", name="uix_emp_type_year"),
        Index("idx_emp_year", "employee_id", "year"),
    )


class LeaveTransaction(Base):
    __tablename__ = "leave_transactions"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("leave_accounts.id"), nullable=False)
    leave_type_id = Column(Integer, ForeignKey("leave_types.id"), nullable=False)
    employee_id = Column(Integer, nullable=False)
    year = Column(Integer, nullable=False)
    change_type = Column(String(50), nullable=False)
    change_days = Column(Float, nullable=False)
    balance_after = Column(Float, nullable=False)
    reason = Column(String(500))
    source_id = Column(String(100))
    source_type = Column(String(50))
    operator = Column(String(100))
    expire_date = Column(Date)
    is_reversed = Column(Boolean, default=False)
    reversed_by_id = Column(Integer, ForeignKey("leave_transactions.id"))
    created_at = Column(DateTime, server_default=func.now())

    account = relationship("LeaveAccount", back_populates="transactions")
    leave_type = relationship("LeaveType", back_populates="transactions")


class LeaveApplication(Base):
    __tablename__ = "leave_applications"

    id = Column(Integer, primary_key=True, index=True)
    application_no = Column(String(50), unique=True, index=True, nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    leave_type_id = Column(Integer, ForeignKey("leave_types.id"), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    days = Column(Float, nullable=False)
    status = Column(String(30), default="pending", nullable=False)
    reason = Column(Text)
    approver = Column(String(100))
    approved_at = Column(DateTime)
    reject_reason = Column(Text)
    transaction_id = Column(Integer, ForeignKey("leave_transactions.id"))
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    employee = relationship("Employee", back_populates="applications")
    leave_type = relationship("LeaveType", back_populates="applications")


class ApprovalRecord(Base):
    __tablename__ = "approval_records"

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("leave_applications.id"), nullable=False)
    approver = Column(String(100), nullable=False)
    action = Column(String(30), nullable=False)
    comment = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
