from typing import Optional, List, Tuple
from sqlalchemy.orm import Session

from app.models.models import Employee, LeaveType
from app.schemas import schemas


class MasterDataService:
    def __init__(self, db: Session):
        self.db = db

    def create_employee(self, data: schemas.EmployeeCreate) -> Employee:
        existing = self.db.query(Employee).filter(
            Employee.employee_no == data.employee_no
        ).first()
        if existing:
            raise ValueError(f"员工编号 {data.employee_no} 已存在")

        employee = Employee(**data.model_dump())
        self.db.add(employee)
        self.db.commit()
        self.db.refresh(employee)
        return employee

    def get_employee(self, employee_id: int) -> Optional[Employee]:
        return self.db.query(Employee).filter(Employee.id == employee_id).first()

    def get_employee_by_no(self, employee_no: str) -> Optional[Employee]:
        return self.db.query(Employee).filter(
            Employee.employee_no == employee_no
        ).first()

    def list_employees(
        self, department: Optional[str] = None, is_active: Optional[bool] = None,
        skip: int = 0, limit: int = 100
    ) -> Tuple[int, List[Employee]]:
        query = self.db.query(Employee)
        if department:
            query = query.filter(Employee.department == department)
        if is_active is not None:
            query = query.filter(Employee.is_active == is_active)

        total = query.count()
        employees = query.order_by(Employee.id.asc()).offset(skip).limit(limit).all()
        return total, employees

    def update_employee(
        self, employee_id: int, data: schemas.EmployeeCreate
    ) -> Optional[Employee]:
        employee = self.db.query(Employee).filter(
            Employee.id == employee_id
        ).first()
        if not employee:
            return None

        for key, value in data.model_dump().items():
            setattr(employee, key, value)

        self.db.commit()
        self.db.refresh(employee)
        return employee

    def create_leave_type(self, data: schemas.LeaveTypeCreate) -> LeaveType:
        existing = self.db.query(LeaveType).filter(
            LeaveType.code == data.code
        ).first()
        if existing:
            raise ValueError(f"假期类型编码 {data.code} 已存在")

        leave_type = LeaveType(**data.model_dump())
        self.db.add(leave_type)
        self.db.commit()
        self.db.refresh(leave_type)
        return leave_type

    def get_leave_type(self, leave_type_id: int) -> Optional[LeaveType]:
        return self.db.query(LeaveType).filter(LeaveType.id == leave_type_id).first()

    def get_leave_type_by_code(self, code: str) -> Optional[LeaveType]:
        return self.db.query(LeaveType).filter(LeaveType.code == code).first()

    def list_leave_types(
        self, is_active: Optional[bool] = None, skip: int = 0, limit: int = 100
    ) -> Tuple[int, List[LeaveType]]:
        query = self.db.query(LeaveType)
        if is_active is not None:
            query = query.filter(LeaveType.is_active == is_active)

        total = query.count()
        leave_types = query.order_by(LeaveType.id.asc()).offset(skip).limit(limit).all()
        return total, leave_types
