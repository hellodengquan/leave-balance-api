from typing import Optional, Set
from sqlalchemy.orm import Session

from app.models.models import SysUser, Employee, LeaveAccount, LeaveTransaction, LeaveApplication
from app.schemas import schemas


class PermissionService:
    ROLE_EMPLOYEE = "employee"
    ROLE_MANAGER = "manager"
    ROLE_HR = "hr"
    ROLE_FINANCE = "finance"
    ROLE_ADMIN = "admin"

    ROLE_HIERARCHY = {
        ROLE_EMPLOYEE: 10,
        ROLE_MANAGER: 20,
        ROLE_FINANCE: 30,
        ROLE_HR: 40,
        ROLE_ADMIN: 999,
    }

    def __init__(self, db: Session):
        self.db = db

    def get_user(self, user_id: int) -> Optional[SysUser]:
        return self.db.query(SysUser).filter(
            SysUser.id == user_id,
            SysUser.is_active == True
        ).first()

    def get_user_by_username(self, username: str) -> Optional[SysUser]:
        return self.db.query(SysUser).filter(
            SysUser.username == username,
            SysUser.is_active == True
        ).first()

    def create_user(self, data: schemas.SysUserCreate) -> SysUser:
        existing = self.db.query(SysUser).filter(SysUser.username == data.username).first()
        if existing:
            raise ValueError(f"用户名 {data.username} 已存在")
        user = SysUser(**data.model_dump())
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def get_auth_context(self, username: str) -> schemas.AuthContext:
        user = self.get_user_by_username(username)
        if not user:
            raise ValueError(f"用户不存在: {username}")
        return schemas.AuthContext(
            user_id=user.id,
            username=user.username,
            role=user.role,
            employee_id=user.employee_id,
            department=user.department
        )

    def has_role(self, auth: schemas.AuthContext, role: str) -> bool:
        return self.ROLE_HIERARCHY.get(auth.role, 0) >= self.ROLE_HIERARCHY.get(role, 9999)

    def require_role(self, auth: schemas.AuthContext, role: str):
        if not self.has_role(auth, role):
            raise PermissionError(
                f"权限不足: 需要 {role} 角色, 当前 {auth.role}"
            )

    def get_accessible_employee_ids(self, auth: schemas.AuthContext) -> Optional[Set[int]]:
        if self.has_role(auth, self.ROLE_HR):
            return None

        ids = set()
        if auth.employee_id:
            ids.add(auth.employee_id)

        if self.has_role(auth, self.ROLE_MANAGER) and auth.department:
            dept_employees = self.db.query(Employee).filter(
                Employee.department == auth.department
            ).all()
            for e in dept_employees:
                ids.add(e.id)

        return ids

    def filter_employees_query(self, query, auth: schemas.AuthContext):
        if self.has_role(auth, self.ROLE_HR):
            return query
        if self.has_role(auth, self.ROLE_MANAGER) and auth.department:
            allowed_ids = self.get_accessible_employee_ids(auth)
            if allowed_ids:
                return query.filter(Employee.id.in_(allowed_ids))
            return query.filter(False)
        if auth.employee_id:
            return query.filter(Employee.id == auth.employee_id)
        return query.filter(False)

    def filter_accounts_query(self, query, auth: schemas.AuthContext):
        if self.has_role(auth, self.ROLE_HR):
            return query
        allowed_ids = self.get_accessible_employee_ids(auth)
        if allowed_ids:
            return query.filter(LeaveAccount.employee_id.in_(allowed_ids))
        return query.filter(False)

    def filter_transactions_query(self, query, auth: schemas.AuthContext):
        if self.has_role(auth, self.ROLE_HR):
            return query
        allowed_ids = self.get_accessible_employee_ids(auth)
        if allowed_ids:
            return query.filter(LeaveTransaction.employee_id.in_(allowed_ids))
        return query.filter(False)

    def filter_applications_query(self, query, auth: schemas.AuthContext):
        if self.has_role(auth, self.ROLE_HR):
            return query
        allowed_ids = self.get_accessible_employee_ids(auth)
        if allowed_ids:
            return query.filter(LeaveApplication.employee_id.in_(allowed_ids))
        return query.filter(False)

    def can_view_employee(self, auth: schemas.AuthContext, employee_id: int) -> bool:
        if self.has_role(auth, self.ROLE_HR):
            return True
        if auth.employee_id == employee_id:
            return True
        if self.has_role(auth, self.ROLE_MANAGER) and auth.department:
            emp = self.db.query(Employee).filter(Employee.id == employee_id).first()
            return emp and emp.department == auth.department
        return False

    def can_approve_application(self, auth: schemas.AuthContext, application: LeaveApplication) -> bool:
        if self.has_role(auth, self.ROLE_HR):
            return True
        if self.has_role(auth, self.ROLE_MANAGER) and auth.department:
            emp = self.db.query(Employee).filter(
                Employee.id == application.employee_id
            ).first()
            return emp and emp.department == auth.department
        return False
