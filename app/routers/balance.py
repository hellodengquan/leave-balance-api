from typing import Optional, List
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, Header
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import schemas
from app.services.balance_service import BalanceService
from app.services.permission_service import PermissionService

router = APIRouter(prefix="/balance", tags=["余额管理"])


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


@router.get("", response_model=List[schemas.BalanceSummary], summary="查询假期余额")
def get_balance(
    employee_id: Optional[int] = None,
    employee_no: Optional[str] = None,
    leave_type_id: Optional[int] = None,
    leave_type_code: Optional[str] = None,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = BalanceService(db, auth)
    try:
        return service.get_balance(
            employee_id=employee_id,
            employee_no=employee_no,
            leave_type_id=leave_type_id,
            leave_type_code=leave_type_code,
            year=year
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/adjust", response_model=schemas.LeaveAccount, summary="余额调整/补发(支持追溯链路)")
def adjust_balance(
    data: schemas.BalanceAdjustment,
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = BalanceService(db, auth)
    retro_link_data = None
    if (data.retro_source_transaction_id or data.retro_source_application_id
            or data.retro_approval_no or data.retro_document_no):
        retro_link_data = {
            "source_transaction_id": data.retro_source_transaction_id,
            "source_application_id": data.retro_source_application_id,
            "approval_no": data.retro_approval_no,
            "document_no": data.retro_document_no,
            "retro_reason": data.reason
        }
    try:
        account, _ = service.adjust_balance(
            employee_id=data.employee_id,
            leave_type_id=data.leave_type_id,
            days=data.days,
            reason=data.reason,
            operator=data.operator,
            year=data.year,
            expire_date=data.expire_date,
            retro_link_data=retro_link_data
        )
        return account
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/grant", response_model=schemas.LeaveAccount, summary="发放假期")
def grant_leave(
    employee_id: int,
    leave_type_id: int,
    days: float,
    reason: str,
    operator: str,
    year: Optional[int] = None,
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = BalanceService(db, auth)
    try:
        account, _ = service.grant_leave(
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            days=days,
            reason=reason,
            operator=operator,
            year=year
        )
        return account
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/annual-grant", summary="年度假期批量发放(支持工作日模式)")
def annual_grant(
    data: schemas.AnnualGrant,
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = BalanceService(db, auth)
    try:
        results = service.annual_grant(
            leave_type_code=data.leave_type_code,
            operator=data.operator,
            year=data.year
        )
        return {"processed": len(results), "message": "年度假期发放完成"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/year-end-carry-over", summary="年度结转(支持节假日处理)")
def year_end_carry_over(
    data: schemas.YearEndCarryOver,
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = BalanceService(db, auth)
    results = service.year_end_carry_over(
        from_year=data.from_year,
        to_year=data.to_year,
        operator=data.operator
    )
    return {"processed": len(results), "message": "年度结转完成"}


@router.post("/process-expired", summary="处理过期余额(自动清零或生成hold审批)")
def process_expired(
    operator: str = "system",
    use_hold_mode: bool = Query(False, description="True=生成hold待审批, False=直接自动清零"),
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = BalanceService(db, auth)
    if use_hold_mode:
        holds = service.scan_expired_and_create_holds(operator=operator)
        return {"processed": len(holds), "mode": "hold", "message": "过期清零待审批单已生成"}
    else:
        transactions = service.process_expired_balance(operator=operator)
        return {"processed": len(transactions), "mode": "auto", "message": "过期余额处理完成"}


@router.get("/transactions", response_model=schemas.PaginatedResponse, summary="交易明细回溯(Offset分页)")
def get_transactions(
    employee_id: Optional[int] = None,
    leave_type_id: Optional[int] = None,
    year: Optional[int] = None,
    change_type: Optional[str] = Query(
        None, description="grant/deduct/adjust_add/adjust_subtract/carry_over/carry_out/expire"
    ),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    source_id: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = BalanceService(db, auth)
    try:
        total, transactions = service.get_transactions(
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            year=year,
            change_type=change_type,
            start_date=start_date,
            end_date=end_date,
            source_id=source_id,
            skip=skip,
            limit=limit
        )
        return schemas.PaginatedResponse(total=total, items=transactions)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.get("/transactions-cursor", response_model=schemas.KeysetPaginatedResponse, summary="交易明细回溯(游标分页-大数据量)")
def get_transactions_cursor(
    employee_id: Optional[int] = None,
    leave_type_id: Optional[int] = None,
    year: Optional[int] = None,
    change_type: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    source_id: Optional[str] = None,
    cursor: Optional[str] = Query(None, description="上次返回的next_cursor"),
    limit: int = Query(100, ge=1, le=1000),
    include_total: bool = Query(False, description="是否统计总数(大数据量建议关闭)"),
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = BalanceService(db, auth)
    try:
        return service.get_transactions_cursor(
            employee_id=employee_id,
            leave_type_id=leave_type_id,
            year=year,
            change_type=change_type,
            start_date=start_date,
            end_date=end_date,
            source_id=source_id,
            cursor=cursor,
            limit=limit,
            include_total=include_total
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.get("/frozen-logs", response_model=schemas.PaginatedResponse, summary="冻结/解冻操作日志")
def get_frozen_logs(
    account_id: Optional[int] = None,
    employee_id: Optional[int] = None,
    application_id: Optional[int] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = BalanceService(db, auth)
    total, logs = service.get_frozen_logs(
        account_id=account_id,
        employee_id=employee_id,
        application_id=application_id,
        skip=skip,
        limit=limit
    )
    return schemas.PaginatedResponse(total=total, items=logs)


@router.get("/retro-link/{grant_transaction_id}", summary="查询补发追溯链路")
def get_retro_link(
    grant_transaction_id: int,
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = BalanceService(db, auth)
    link = service.get_retro_link(grant_transaction_id)
    if not link:
        raise HTTPException(status_code=404, detail="未找到追溯链路")
    return link


@router.get("/expire-holds", response_model=schemas.PaginatedResponse, summary="过期清零待审批列表")
def list_expire_holds(
    status: Optional[str] = Query(None, description="pending/approved/rejected"),
    employee_id: Optional[int] = None,
    leave_type_id: Optional[int] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = BalanceService(db, auth)
    total, holds = service.list_expire_holds(
        status=status,
        employee_id=employee_id,
        leave_type_id=leave_type_id,
        skip=skip,
        limit=limit
    )
    return schemas.PaginatedResponse(total=total, items=holds)


@router.post("/expire-holds/{hold_id}/approve", summary="审批通过过期清零")
def approve_expire_hold(
    hold_id: int,
    data: schemas.ExpireHoldApprove,
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    if auth:
        perm = PermissionService(db)
        if not perm.has_role(auth, perm.ROLE_HR):
            raise HTTPException(status_code=403, detail="仅HR角色可审批过期清零")
    service = BalanceService(db, auth)
    try:
        return service.approve_expire_hold(hold_id, data.approver, data.comment)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/expire-holds/{hold_id}/reject", summary="审批驳回过期清零(余额保留)")
def reject_expire_hold(
    hold_id: int,
    data: schemas.ExpireHoldReject,
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    if auth:
        perm = PermissionService(db)
        if not perm.has_role(auth, perm.ROLE_HR):
            raise HTTPException(status_code=403, detail="仅HR角色可审批过期清零")
    service = BalanceService(db, auth)
    try:
        return service.reject_expire_hold(hold_id, data.approver, data.reject_reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/holidays", response_model=schemas.HolidayConfig, summary="配置节假日/调休工作日")
def add_holiday(
    data: schemas.HolidayConfigCreate,
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    if auth:
        perm = PermissionService(db)
        if not perm.has_role(auth, perm.ROLE_HR):
            raise HTTPException(status_code=403, detail="仅HR角色可配置节假日")
    service = BalanceService(db, auth)
    return service.add_holiday(data.date, data.name, data.type)


@router.get("/holidays", response_model=List[schemas.HolidayConfig], summary="查询节假日配置")
def list_holidays(
    year: Optional[int] = None,
    holiday_type: Optional[str] = Query(None, description="holiday/workday"),
    db: Session = Depends(get_db),
    auth: Optional[schemas.AuthContext] = Depends(_get_auth)
):
    service = BalanceService(db, auth)
    return service.list_holidays(year=year, holiday_type=holiday_type)
