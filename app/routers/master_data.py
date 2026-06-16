from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import schemas
from app.services.master_data_service import MasterDataService

router = APIRouter(prefix="/master", tags=["基础数据管理"])


@router.post("/employees", response_model=schemas.Employee, summary="创建员工")
def create_employee(data: schemas.EmployeeCreate, db: Session = Depends(get_db)):
    service = MasterDataService(db)
    try:
        return service.create_employee(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/employees/{employee_id}", response_model=schemas.Employee, summary="获取员工详情")
def get_employee(employee_id: int, db: Session = Depends(get_db)):
    service = MasterDataService(db)
    employee = service.get_employee(employee_id)
    if not employee:
        raise HTTPException(status_code=404, detail="员工不存在")
    return employee


@router.get("/employees", response_model=schemas.PaginatedResponse, summary="员工列表")
def list_employees(
    department: Optional[str] = None,
    is_active: Optional[bool] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db)
):
    service = MasterDataService(db)
    total, employees = service.list_employees(department, is_active, skip, limit)
    return schemas.PaginatedResponse(total=total, items=employees)


@router.put("/employees/{employee_id}", response_model=schemas.Employee, summary="更新员工")
def update_employee(
    employee_id: int,
    data: schemas.EmployeeCreate,
    db: Session = Depends(get_db)
):
    service = MasterDataService(db)
    employee = service.update_employee(employee_id, data)
    if not employee:
        raise HTTPException(status_code=404, detail="员工不存在")
    return employee


@router.post("/leave-types", response_model=schemas.LeaveType, summary="创建假期类型")
def create_leave_type(data: schemas.LeaveTypeCreate, db: Session = Depends(get_db)):
    service = MasterDataService(db)
    try:
        return service.create_leave_type(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/leave-types/{leave_type_id}", response_model=schemas.LeaveType, summary="获取假期类型")
def get_leave_type(leave_type_id: int, db: Session = Depends(get_db)):
    service = MasterDataService(db)
    leave_type = service.get_leave_type(leave_type_id)
    if not leave_type:
        raise HTTPException(status_code=404, detail="假期类型不存在")
    return leave_type


@router.get("/leave-types", response_model=List[schemas.LeaveType], summary="假期类型列表")
def list_leave_types(
    is_active: Optional[bool] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db)
):
    service = MasterDataService(db)
    _, leave_types = service.list_leave_types(is_active, skip, limit)
    return leave_types
