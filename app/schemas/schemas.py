from datetime import datetime, date
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator


class EmployeeBase(BaseModel):
    employee_no: str = Field(..., max_length=50)
    name: str = Field(..., max_length=100)
    department: Optional[str] = Field(None, max_length=100)
    position: Optional[str] = Field(None, max_length=100)
    hire_date: date
    is_active: Optional[bool] = True


class EmployeeCreate(EmployeeBase):
    pass


class Employee(EmployeeBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LeaveTypeBase(BaseModel):
    code: str = Field(..., max_length=50)
    name: str = Field(..., max_length=100)
    description: Optional[str] = None
    annual_grant_days: float = 0.0
    carry_over_days: float = 0.0
    expire_months: int = 12
    unit: str = "day"
    is_active: Optional[bool] = True


class LeaveTypeCreate(LeaveTypeBase):
    pass


class LeaveType(LeaveTypeBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class LeaveAccountBase(BaseModel):
    employee_id: int
    leave_type_id: int
    year: int
    balance: float = 0.0
    frozen_balance: float = 0.0


class LeaveAccount(LeaveAccountBase):
    id: int
    version: int
    created_at: datetime
    updated_at: datetime
    employee: Optional[Employee] = None
    leave_type: Optional[LeaveType] = None

    class Config:
        from_attributes = True


class LeaveTransactionBase(BaseModel):
    change_type: str = Field(..., max_length=50)
    change_days: float
    reason: Optional[str] = Field(None, max_length=500)
    source_id: Optional[str] = Field(None, max_length=100)
    source_type: Optional[str] = Field(None, max_length=50)
    operator: Optional[str] = Field(None, max_length=100)
    expire_date: Optional[date] = None


class LeaveTransactionCreate(LeaveTransactionBase):
    account_id: int
    leave_type_id: int
    employee_id: int
    year: int


class LeaveTransaction(LeaveTransactionBase):
    id: int
    account_id: int
    leave_type_id: int
    employee_id: int
    year: int
    balance_after: float
    is_reversed: bool
    reversed_by_id: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


class LeaveApplicationBase(BaseModel):
    employee_id: int
    leave_type_id: int
    start_date: date
    end_date: date
    days: float
    reason: Optional[str] = None

    @field_validator("days")
    @classmethod
    def days_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("请假天数必须大于0")
        return v


class LeaveApplicationCreate(LeaveApplicationBase):
    pass


class LeaveApplicationApprove(BaseModel):
    approver: str = Field(..., max_length=100)
    comment: Optional[str] = None


class LeaveApplicationReject(BaseModel):
    approver: str = Field(..., max_length=100)
    reject_reason: str = Field(..., max_length=500)


class LeaveApplication(LeaveApplicationBase):
    id: int
    application_no: str
    status: str
    approver: Optional[str] = None
    approved_at: Optional[datetime] = None
    reject_reason: Optional[str] = None
    transaction_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    employee: Optional[Employee] = None
    leave_type: Optional[LeaveType] = None

    class Config:
        from_attributes = True


class BalanceAdjustment(BaseModel):
    employee_id: int
    leave_type_id: int
    year: Optional[int] = None
    days: float
    reason: str = Field(..., max_length=500)
    operator: str = Field(..., max_length=100)
    expire_date: Optional[date] = None


class AnnualGrant(BaseModel):
    leave_type_code: str = "annual"
    operator: str = Field(..., max_length=100)
    year: Optional[int] = None


class YearEndCarryOver(BaseModel):
    from_year: int
    to_year: int
    operator: str = Field(..., max_length=100)


class BalanceQuery(BaseModel):
    employee_id: Optional[int] = None
    employee_no: Optional[str] = None
    leave_type_id: Optional[int] = None
    leave_type_code: Optional[str] = None
    year: Optional[int] = None


class TransactionQuery(BaseModel):
    employee_id: Optional[int] = None
    leave_type_id: Optional[int] = None
    year: Optional[int] = None
    change_type: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    source_id: Optional[str] = None


class PaginatedResponse(BaseModel):
    total: int
    items: List


class BalanceSummary(BaseModel):
    employee_id: int
    employee_name: str
    leave_type_code: str
    leave_type_name: str
    year: int
    balance: float
    frozen_balance: float
    available_balance: float
