from datetime import datetime, date
from typing import Optional, List, Tuple
import uuid
from sqlalchemy.orm import Session

from app.models.models import (
    Employee, LeaveType, LeaveAccount, LeaveTransaction,
    LeaveApplication, ApprovalRecord
)
from app.schemas import schemas
from app.services.balance_service import BalanceService


class ApplicationService:
    def __init__(self, db: Session):
        self.db = db
        self.balance_service = BalanceService(db)

    def _generate_application_no(self) -> str:
        return f"LA{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"

    def create_application(
        self, data: schemas.LeaveApplicationCreate
    ) -> LeaveApplication:
        employee = self.db.query(Employee).filter(
            Employee.id == data.employee_id,
            Employee.is_active == True
        ).first()
        if not employee:
            raise ValueError("员工不存在或已离职")

        leave_type = self.db.query(LeaveType).filter(
            LeaveType.id == data.leave_type_id,
            LeaveType.is_active == True
        ).first()
        if not leave_type:
            raise ValueError("假期类型不存在或已停用")

        if data.end_date < data.start_date:
            raise ValueError("结束日期不能早于开始日期")

        application = LeaveApplication(
            application_no=self._generate_application_no(),
            employee_id=data.employee_id,
            leave_type_id=data.leave_type_id,
            start_date=data.start_date,
            end_date=data.end_date,
            days=data.days,
            status="pending",
            reason=data.reason
        )
        self.db.add(application)
        self.db.flush()
        self.db.commit()
        self.db.refresh(application)

        try:
            self.balance_service.freeze_balance(
                employee_id=data.employee_id,
                leave_type_id=data.leave_type_id,
                days=data.days,
                year=data.start_date.year
            )
        except Exception as e:
            application.status = "cancelled"
            self.db.commit()
            raise ValueError(f"创建申请失败：{str(e)}")

        return application

    def approve_application(
        self, application_id: int, data: schemas.LeaveApplicationApprove
    ) -> LeaveApplication:
        application = self.db.query(LeaveApplication).filter(
            LeaveApplication.id == application_id
        ).first()
        if not application:
            raise ValueError("申请单不存在")

        if application.status != "pending":
            raise ValueError(f"当前状态为 {application.status}，无法审批")

        application.status = "approved"
        application.approver = data.approver
        application.approved_at = datetime.now()

        approval_record = ApprovalRecord(
            application_id=application.id,
            approver=data.approver,
            action="approve",
            comment=data.comment
        )
        self.db.add(approval_record)

        try:
            self.balance_service.unfreeze_balance(
                employee_id=application.employee_id,
                leave_type_id=application.leave_type_id,
                days=application.days,
                year=application.start_date.year
            )

            account, transaction = self.balance_service.deduct_leave(
                employee_id=application.employee_id,
                leave_type_id=application.leave_type_id,
                days=application.days,
                reason=f"请假申请: {application.application_no}",
                operator=data.approver,
                year=application.start_date.year,
                source_id=str(application.id),
                source_type="leave_application"
            )
            application.transaction_id = transaction.id
        except Exception as e:
            self.db.rollback()
            raise ValueError(f"审批失败：{str(e)}")

        self.db.commit()
        self.db.refresh(application)
        return application

    def reject_application(
        self, application_id: int, data: schemas.LeaveApplicationReject
    ) -> LeaveApplication:
        application = self.db.query(LeaveApplication).filter(
            LeaveApplication.id == application_id
        ).first()
        if not application:
            raise ValueError("申请单不存在")

        if application.status != "pending":
            raise ValueError(f"当前状态为 {application.status}，无法驳回")

        application.status = "rejected"
        application.approver = data.approver
        application.reject_reason = data.reject_reason

        approval_record = ApprovalRecord(
            application_id=application.id,
            approver=data.approver,
            action="reject",
            comment=data.reject_reason
        )
        self.db.add(approval_record)

        try:
            self.balance_service.unfreeze_balance(
                employee_id=application.employee_id,
                leave_type_id=application.leave_type_id,
                days=application.days,
                year=application.start_date.year
            )
        except Exception:
            pass

        self.db.commit()
        self.db.refresh(application)
        return application

    def cancel_application(
        self, application_id: int, operator: str
    ) -> LeaveApplication:
        application = self.db.query(LeaveApplication).filter(
            LeaveApplication.id == application_id
        ).first()
        if not application:
            raise ValueError("申请单不存在")

        if application.status not in ["pending"]:
            raise ValueError(f"当前状态为 {application.status}，无法撤销")

        application.status = "cancelled"

        approval_record = ApprovalRecord(
            application_id=application.id,
            approver=operator,
            action="cancel",
            comment="申请人撤销"
        )
        self.db.add(approval_record)

        try:
            self.balance_service.unfreeze_balance(
                employee_id=application.employee_id,
                leave_type_id=application.leave_type_id,
                days=application.days,
                year=application.start_date.year
            )
        except Exception:
            pass

        self.db.commit()
        self.db.refresh(application)
        return application

    def get_application(self, application_id: int) -> Optional[LeaveApplication]:
        return self.db.query(LeaveApplication).filter(
            LeaveApplication.id == application_id
        ).first()

    def get_applications(
        self,
        employee_id: Optional[int] = None,
        status: Optional[str] = None,
        leave_type_id: Optional[int] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        skip: int = 0,
        limit: int = 100
    ) -> Tuple[int, List[LeaveApplication]]:
        query = self.db.query(LeaveApplication)

        if employee_id:
            query = query.filter(LeaveApplication.employee_id == employee_id)
        if status:
            query = query.filter(LeaveApplication.status == status)
        if leave_type_id:
            query = query.filter(LeaveApplication.leave_type_id == leave_type_id)
        if start_date:
            query = query.filter(LeaveApplication.start_date >= start_date)
        if end_date:
            query = query.filter(LeaveApplication.end_date <= end_date)

        total = query.count()
        applications = query.order_by(
            LeaveApplication.created_at.desc()
        ).offset(skip).limit(limit).all()

        return total, applications

    def get_approval_records(
        self, application_id: int
    ) -> List[ApprovalRecord]:
        return self.db.query(ApprovalRecord).filter(
            ApprovalRecord.application_id == application_id
        ).order_by(ApprovalRecord.created_at.asc()).all()
