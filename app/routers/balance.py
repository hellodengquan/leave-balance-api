from typing import Optional, List
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import schemas
from app.services.balance_service import BalanceService

router = APIRouter(prefix="/balance", tags=["余额管理"])


@router.get("", response_model=List[schemas.BalanceSummary], summary="查询假期余额")
def get_balance(
    employee_id: Optional[int] = None,
    employee_no: Optional[str] = None,
    leave_type_id: Optional[int] = None,
    leave_type_code: Optional[str] = None,
    year: Optional[int] = None,
    db: Session = Depends(get_db)
):
    service = BalanceService(db)
    return service.get_balance(
        employee_id=employee_id,
        employee_no=employee_no,
        leave_type_id=leave_type_id,
        leave_type_code=leave_type_code,
        year=year
    )


@router.post("/adjust", response_model=schemas.LeaveAccount, summary="余额调整/补发")
def adjust_balance(data: schemas.BalanceAdjustment, db: Session = Depends(get_db)):
    service = BalanceService(db)
    try:
        account, _ = service.adjust_balance(
            employee_id=data.employee_id,
            leave_type_id=data.leave_type_id,
            days=data.days,
            reason=data.reason,
            operator=data.operator,
            year=data.year,
            expire_date=data.expire_date
        )
        return account
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/grant", response_model=schemas.LeaveAccount, summary="发放假期")
def grant_leave(
    employee_id: int,
    leave_type_id: int,
    days: float,
    reason: str,
    operator: str,
    year: Optional[int] = None,
    db: Session = Depends(get_db)
):
    service = BalanceService(db)
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


@router.post("/annual-grant", summary="年度假期批量发放")
def annual_grant(data: schemas.AnnualGrant, db: Session = Depends(get_db)):
    service = BalanceService(db)
    try:
        results = service.annual_grant(
            leave_type_code=data.leave_type_code,
            operator=data.operator,
            year=data.year
        )
        return {"processed": len(results), "message": "年度假期发放完成"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/year-end-carry-over", summary="年度结转")
def year_end_carry_over(data: schemas.YearEndCarryOver, db: Session = Depends(get_db)):
    service = BalanceService(db)
    results = service.year_end_carry_over(
        from_year=data.from_year,
        to_year=data.to_year,
        operator=data.operator
    )
    return {"processed": len(results), "message": "年度结转完成"}


@router.post("/process-expired", summary="处理过期余额")
def process_expired(operator: str = "system", db: Session = Depends(get_db)):
    service = BalanceService(db)
    transactions = service.process_expired_balance(operator=operator)
    return {"processed": len(transactions), "message": "过期余额处理完成"}


@router.get("/transactions", response_model=schemas.PaginatedResponse, summary="交易明细回溯")
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
    db: Session = Depends(get_db)
):
    service = BalanceService(db)
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
