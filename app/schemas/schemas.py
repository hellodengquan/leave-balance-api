from datetime import datetime, date
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, Field, field_validator


class SysUserBase(BaseModel):
    username: str = Field(..., max_length=100)
    name: str = Field(..., max_length=100)
    employee_id: Optional[int] = None
    role: str = "employee"
    department: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = True


class SysUserCreate(SysUserBase):
    pass


class SysUser(SysUserBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class AuthContext(BaseModel):
    user_id: int
    username: str
    role: str
    employee_id: Optional[int] = None
    department: Optional[str] = None


class HolidayConfigBase(BaseModel):
    date: date
    name: Optional[str] = Field(None, max_length=100)
    type: str = "holiday"
    year: int
    substitute_for: Optional[date] = None


class HolidayConfigCreate(HolidayConfigBase):
    pass


class HolidayConfig(HolidayConfigBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class FrozenBalanceLogBase(BaseModel):
    account_id: int
    employee_id: int
    leave_type_id: int
    application_id: Optional[int] = None
    operation: str = Field(..., max_length=30)
    days: float
    balance_before: float
    balance_after: float
    operator: Optional[str] = Field(None, max_length=100)
    reason: Optional[str] = Field(None, max_length=500)
    rollback_of_id: Optional[int] = None


class FrozenBalanceLog(FrozenBalanceLogBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class ExpireHoldBase(BaseModel):
    employee_id: int
    leave_type_id: int
    account_id: int
    transaction_id: int
    expire_date: date
    hold_days: float
    operator: Optional[str] = Field(None, max_length=100)


class ExpireHoldCreate(ExpireHoldBase):
    pass


class ExpireHoldApprove(BaseModel):
    approver: str = Field(..., max_length=100)
    comment: Optional[str] = None


class ExpireHoldReject(BaseModel):
    approver: str = Field(..., max_length=100)
    reject_reason: str = Field(..., max_length=500)


class ExpireHold(ExpireHoldBase):
    id: int
    hold_no: str
    status: str
    approver: Optional[str] = None
    approved_at: Optional[datetime] = None
    reject_reason: Optional[str] = None
    timeout_hours: int = 72
    escalated_to: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class GrantRetroLinkBase(BaseModel):
    grant_transaction_id: int
    source_transaction_id: Optional[int] = None
    source_application_id: Optional[int] = None
    approval_no: Optional[str] = Field(None, max_length=100)
    document_no: Optional[str] = Field(None, max_length=100)
    retro_reason: str = Field(..., max_length=500)
    created_by: str = Field(..., max_length=100)


class GrantRetroLinkCreate(GrantRetroLinkBase):
    pass


class GrantRetroLink(GrantRetroLinkBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class RetroLinkChainNode(BaseModel):
    transaction_id: int
    change_type: str
    change_days: float
    reason: Optional[str] = None
    operator: Optional[str] = None
    created_at: Optional[datetime] = None
    retro_link: Optional[GrantRetroLink] = None
    related_application_id: Optional[int] = None
    related_application_no: Optional[str] = None


class RetroLinkChain(BaseModel):
    grant_transaction_id: int
    chain: List[RetroLinkChainNode]
    total_depth: int


class FieldPermissionBase(BaseModel):
    role: str = Field(..., max_length=50)
    resource: str = Field(..., max_length=50)
    field_name: str = Field(..., max_length=100)
    access: str = "visible"
    mask_pattern: Optional[str] = Field(None, max_length=100)


class FieldPermissionCreate(FieldPermissionBase):
    pass


class FieldPermission(FieldPermissionBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


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
    use_workdays: bool = False
    require_expire_approval: bool = False
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
    pending_expire_days: float = 0.0


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
    frozen_after: float = 0.0
    is_reversed: bool
    reversed_by_id: Optional[int] = None
    related_transaction_id: Optional[int] = None
    created_at: datetime
    retro_link: Optional[GrantRetroLink] = None

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


class LeaveApplicationCancel(BaseModel):
    operator: str = Field(..., max_length=100)
    cancel_reason: Optional[str] = Field(None, max_length=500)


class LeaveApplication(LeaveApplicationBase):
    id: int
    application_no: str
    work_days: Optional[float] = None
    status: str
    approver: Optional[str] = None
    approved_at: Optional[datetime] = None
    reject_reason: Optional[str] = None
    cancelled_by: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    cancel_reason: Optional[str] = None
    transaction_id: Optional[int] = None
    previous_status: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    employee: Optional[Employee] = None
    leave_type: Optional[LeaveType] = None
    frozen_logs: Optional[List[FrozenBalanceLog]] = None

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
    retro_source_transaction_id: Optional[int] = None
    retro_source_application_id: Optional[int] = None
    retro_approval_no: Optional[str] = None
    retro_document_no: Optional[str] = None


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


class KeysetPaginatedResponse(BaseModel):
    items: List
    next_cursor: Optional[str] = None
    has_more: bool = False
    total_count: Optional[int] = None


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
    pending_expire_days: float = 0.0
    available_balance: float


class FieldFilteredResponse(BaseModel):
    data: Dict[str, Any]
    masked_fields: List[str] = []
    hidden_fields: List[str] = []
