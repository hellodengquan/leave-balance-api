from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Date,
    ForeignKey, Boolean, Text, UniqueConstraint, Index,
    BigInteger, Numeric
)
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func

Base = declarative_base()


class SysUser(Base):
    __tablename__ = "sys_users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    role = Column(String(50), default="employee", nullable=False)
    department = Column(String(100))
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())


class HolidayConfig(Base):
    __tablename__ = "holiday_configs"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)
    name = Column(String(100))
    type = Column(String(20), default="holiday")
    year = Column(Integer, index=True)
    substitute_for = Column(Date, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("date", name="uix_holiday_date"),
    )


class FrozenBalanceLog(Base):
    __tablename__ = "frozen_balance_logs"

    id = Column(Integer, primary_key=True, index=True)
    account_id = Column(Integer, ForeignKey("leave_accounts.id"), nullable=False, index=True)
    employee_id = Column(Integer, nullable=False, index=True)
    leave_type_id = Column(Integer, nullable=False)
    application_id = Column(Integer, ForeignKey("leave_applications.id"), nullable=True, index=True)
    operation = Column(String(30), nullable=False)
    days = Column(Float, nullable=False)
    balance_before = Column(Float, nullable=False)
    balance_after = Column(Float, nullable=False)
    operator = Column(String(100))
    reason = Column(String(500))
    rollback_of_id = Column(Integer, ForeignKey("frozen_balance_logs.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class ExpireHold(Base):
    __tablename__ = "expire_holds"

    id = Column(Integer, primary_key=True, index=True)
    hold_no = Column(String(50), unique=True, index=True, nullable=False)
    employee_id = Column(Integer, nullable=False, index=True)
    leave_type_id = Column(Integer, nullable=False)
    account_id = Column(Integer, ForeignKey("leave_accounts.id"), nullable=False)
    transaction_id = Column(Integer, ForeignKey("leave_transactions.id"), nullable=False)
    expire_date = Column(Date, nullable=False, index=True)
    hold_days = Column(Float, nullable=False)
    status = Column(String(30), default="pending", nullable=False)
    approver = Column(String(100))
    approved_at = Column(DateTime)
    reject_reason = Column(Text)
    operator = Column(String(100))
    timeout_hours = Column(Integer, default=72)
    escalated_to = Column(String(100))
    reapply_count = Column(Integer, default=0)
    last_reapplied_at = Column(DateTime, nullable=True)
    is_stuck = Column(Boolean, default=False)
    recovered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class GrantRetroLink(Base):
    __tablename__ = "grant_retro_links"

    id = Column(Integer, primary_key=True, index=True)
    grant_transaction_id = Column(Integer, ForeignKey("leave_transactions.id"), nullable=False, index=True)
    source_transaction_id = Column(Integer, ForeignKey("leave_transactions.id"), nullable=True)
    source_application_id = Column(Integer, ForeignKey("leave_applications.id"), nullable=True)
    approval_no = Column(String(100))
    document_no = Column(String(100))
    retro_reason = Column(String(500))
    created_by = Column(String(100))
    created_at = Column(DateTime, server_default=func.now())


class ApprovalRecord(Base):
    __tablename__ = "approval_records"

    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("leave_applications.id"), nullable=False)
    approver = Column(String(100), nullable=False)
    action = Column(String(30), nullable=False)
    comment = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class ExpireHoldApproval(Base):
    __tablename__ = "expire_hold_approvals"

    id = Column(Integer, primary_key=True, index=True)
    hold_id = Column(Integer, ForeignKey("expire_holds.id"), nullable=False)
    approver = Column(String(100), nullable=False)
    action = Column(String(30), nullable=False)
    comment = Column(Text)
    created_at = Column(DateTime, server_default=func.now())


class FieldPermission(Base):
    __tablename__ = "field_permissions"

    id = Column(Integer, primary_key=True, index=True)
    role = Column(String(50), nullable=False, index=True)
    resource = Column(String(50), nullable=False, index=True)
    field_name = Column(String(100), nullable=False)
    access = Column(String(20), default="visible", nullable=False)
    mask_pattern = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    priority = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("role", "resource", "field_name", name="uix_role_resource_field"),
    )


class CursorSecret(Base):
    __tablename__ = "cursor_secrets"

    id = Column(Integer, primary_key=True, index=True)
    secret_key = Column(String(200), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_primary = Column(Boolean, default=False, nullable=False)
    version = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime, nullable=True)


class FieldAuditLog(Base):
    __tablename__ = "field_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    operator = Column(String(100), nullable=False, index=True)
    operator_role = Column(String(50), nullable=False)
    resource = Column(String(50), nullable=False, index=True)
    field_name = Column(String(100), nullable=False, index=True)
    target_employee_id = Column(Integer, nullable=True, index=True)
    original_value = Column(Text, nullable=True)
    masked_value = Column(Text, nullable=True)
    mask_pattern = Column(String(100), nullable=True)
    access_type = Column(String(20), default="masked", nullable=False)
    request_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


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
    use_workdays = Column(Boolean, default=False)
    require_expire_approval = Column(Boolean, default=False)
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
    pending_expire_days = Column(Float, default=0.0)
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

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("leave_accounts.id"), nullable=False)
    leave_type_id = Column(Integer, ForeignKey("leave_types.id"), nullable=False)
    employee_id = Column(Integer, nullable=False)
    year = Column(Integer, nullable=False)
    change_type = Column(String(50), nullable=False)
    change_days = Column(Float, nullable=False)
    balance_after = Column(Float, nullable=False)
    frozen_after = Column(Float, default=0.0)
    reason = Column(String(500))
    source_id = Column(String(100))
    source_type = Column(String(50))
    operator = Column(String(100))
    expire_date = Column(Date, index=True)
    is_reversed = Column(Boolean, default=False)
    reversed_by_id = Column(Integer, ForeignKey("leave_transactions.id"))
    related_transaction_id = Column(Integer, ForeignKey("leave_transactions.id"))
    created_at = Column(DateTime, server_default=func.now(), index=True)

    account = relationship("LeaveAccount", back_populates="transactions", foreign_keys=[account_id])
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
    work_days = Column(Float)
    status = Column(String(30), default="pending", nullable=False)
    reason = Column(Text)
    approver = Column(String(100))
    approved_at = Column(DateTime)
    reject_reason = Column(Text)
    cancelled_by = Column(String(100))
    cancelled_at = Column(DateTime)
    cancel_reason = Column(Text)
    transaction_id = Column(Integer, ForeignKey("leave_transactions.id"))
    previous_status = Column(String(30), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    employee = relationship("Employee", back_populates="applications")
    leave_type = relationship("LeaveType", back_populates="applications")


class TruncateWarningLog(Base):
    __tablename__ = "truncate_warning_logs"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(50), default="retro_truncate", nullable=False, index=True)
    grant_transaction_id = Column(Integer, nullable=True, index=True)
    employee_id = Column(Integer, nullable=True, index=True)
    max_depth = Column(Integer, nullable=False)
    actual_depth = Column(Integer, nullable=False)
    has_cycle = Column(Boolean, default=False, nullable=False)
    cycle_start_id = Column(Integer, nullable=True)
    severity = Column(String(20), default="warn", nullable=False)
    message = Column(Text, nullable=True)
    operator = Column(String(100), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class FieldPermissionAudit(Base):
    __tablename__ = "field_permission_audits"

    id = Column(Integer, primary_key=True, index=True)
    permission_id = Column(Integer, ForeignKey("field_permissions.id"), nullable=False, index=True)
    action = Column(String(20), nullable=False)
    before_snapshot = Column(Text, nullable=True)
    after_snapshot = Column(Text, nullable=True)
    operator = Column(String(100), nullable=False)
    rollback_of_id = Column(Integer, ForeignKey("field_permission_audits.id"), nullable=True)
    rollback_token = Column(String(100), nullable=True, index=True)
    created_at = Column(DateTime, server_default=func.now())


class SensitivePositionRule(Base):
    __tablename__ = "sensitive_position_rules"

    id = Column(Integer, primary_key=True, index=True)
    rule_name = Column(String(100), nullable=False)
    position_pattern = Column(String(200), nullable=False, index=True)
    match_mode = Column(String(20), default="contains", nullable=False)
    field_mappings = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    priority = Column(Integer, default=0, nullable=False)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AuditRetentionConfig(Base):
    __tablename__ = "audit_retention_configs"

    id = Column(Integer, primary_key=True, index=True)
    resource = Column(String(50), unique=True, nullable=False, index=True)
    retention_days = Column(Integer, default=365, nullable=False)
    retention_mode = Column(String(20), default="delete", nullable=False)
    archive_destination = Column(String(200), nullable=True)
    last_cleanup_at = Column(DateTime, nullable=True)
    cleanup_count = Column(Integer, default=0, nullable=False)
    updated_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class MaskingPolicyRelease(Base):
    __tablename__ = "masking_policy_releases"

    id = Column(Integer, primary_key=True, index=True)
    policy_version = Column(String(50), unique=True, nullable=False, index=True)
    policy_payload = Column(Text, nullable=False)
    checksum = Column(String(100), nullable=False)
    gray_percent = Column(Integer, default=100, nullable=False)
    gray_tags = Column(String(500), nullable=True)
    status = Column(String(20), default="draft", nullable=False)
    subscriber_ids = Column(String(500), nullable=True)
    released_by = Column(String(100), nullable=True)
    released_at = Column(DateTime, nullable=True)
    rolled_back_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
