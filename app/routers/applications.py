from typing import Optional, List
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import schemas
from app.services.application_service import ApplicationService
from app.services.permission_service import PermissionService

router = APIRouter(prefix="/applications", tags=["请假申请与审批"])


def _get_auth(
    db: Session = Depends(get_db),
    x_username: Optional[str] = Header(None, description="调用方用户名，用于权限控制")
) -> Optional[schemas.AuthContext]:
    if not x_username:
        return None
    try:
        perm = PermissionService(db)
        return perm.get_auth_context(x_username)
    except ValueError:
        return None


@router.post("", response_model=schemas.LeaveApplication, summary="提交请假申请")
def create_application(
    data: schemas.LeaveApplicationCreate,
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = ApplicationService(db, auth)
    try:
        return service.create_application(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.get("/{application_id}", response_model=schemas.LeaveApplication, summary="获取申请详情")
def get_application(
    application_id: int,
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = ApplicationService(db, auth)
    try:
        application = service.get_application(application_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    if not application:
        raise HTTPException(status_code=404, detail="申请单不存在")
    return application


@router.get("", response_model=schemas.PaginatedResponse, summary="申请列表")
def list_applications(
    employee_id: Optional[int] = None,
    status: Optional[str] = Query(
        None, description="pending/approved/rejected/cancelled"
    ),
    leave_type_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = ApplicationService(db, auth)
    try:
        total, applications = service.get_applications(
            employee_id=employee_id,
            status=status,
            leave_type_id=leave_type_id,
            start_date=start_date,
            end_date=end_date,
            skip=skip,
            limit=limit
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return schemas.PaginatedResponse(total=total, items=applications)


@router.post("/{application_id}/approve", response_model=schemas.LeaveApplication, summary="审批通过")
def approve_application(
    application_id: int,
    data: schemas.LeaveApplicationApprove,
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = ApplicationService(db, auth)
    try:
        return service.approve_application(application_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/{application_id}/reject", response_model=schemas.LeaveApplication, summary="审批驳回")
def reject_application(
    application_id: int,
    data: schemas.LeaveApplicationReject,
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = ApplicationService(db, auth)
    try:
        return service.reject_application(application_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{application_id}/cancel", response_model=schemas.LeaveApplication, summary="撤销申请(冻结解冻)")
def cancel_application(
    application_id: int,
    data: schemas.LeaveApplicationCancel,
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = ApplicationService(db, auth)
    try:
        return service.cancel_application(application_id, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{application_id}/restore", response_model=schemas.LeaveApplication, summary="恢复已撤销申请")
def restore_application(
    application_id: int,
    operator: str,
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = ApplicationService(db, auth)
    try:
        return service.restore_application(application_id, operator)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{application_id}/approval-records", summary="审批记录")
def get_approval_records(
    application_id: int,
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = ApplicationService(db, auth)
    records = service.get_approval_records(application_id)
    return records
