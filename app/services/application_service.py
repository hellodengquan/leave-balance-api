from datetime import datetime, date
from typing import Optional, List, Tuple
import uuid
from sqlalchemy.orm import Session

from app.models.models import (
    Employee, LeaveType, LeaveAccount, LeaveTransaction,
    LeaveApplication, ApprovalRecord, FrozenBalanceLog
)
from app.schemas import schemas
from app.services.balance_service import BalanceService
from app.utils import TransactionBoundary, with_retry, count_workdays


class ApplicationService:
    def __init__(self, db: Session, auth: Optional[schemas.AuthContext] = None):
        self.db = db
        self.auth = auth
        self.balance_service = BalanceService(db, auth)

    def _generate_application_no(self) -> str:
        return f"LA{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}"

    def _apply_permission_filter(self, query):
        if not self.auth:
            return query
        from app.services.permission_service import PermissionService
        perm = PermissionService(self.db)
        return perm.filter_applications_query(query, self.auth)

    def _calculate_work_days(
        self, leave_type: LeaveType, start: date, end: date
    ) -> Optional[float]:
        if not leave_type.use_workdays:
            return None
        holidays, workdays = self.balance_service._get_holiday_sets(start.year)
        return float(count_workdays(start, end, holidays, workdays))

    @with_retry(max_retries=3, base_delay=0.15, max_delay=1.5)
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

        operator = self.auth.username if self.auth else "system"
        work_days = self._calculate_work_days(leave_type, data.start_date, data.end_date)

        with TransactionBoundary(self.db):
            application = LeaveApplication(
                application_no=self._generate_application_no(),
                employee_id=data.employee_id,
                leave_type_id=data.leave_type_id,
                start_date=data.start_date,
                end_date=data.end_date,
                days=data.days,
                work_days=work_days,
                status="pending",
                reason=data.reason
            )
            self.db.add(application)
            self.db.flush()
            self.db.refresh(application)

        freeze_reason = f"申请单冻结: {application.application_no}"
        try:
            self.balance_service.freeze_balance(
                employee_id=data.employee_id,
                leave_type_id=data.leave_type_id,
                days=data.days,
                year=data.start_date.year,
                application_id=application.id,
                operator=operator,
                reason=freeze_reason
            )
        except Exception as e:
            with TransactionBoundary(self.db):
                app = self.db.query(LeaveApplication).filter(
                    LeaveApplication.id == application.id
                ).first()
                if app:
                    app.status = "cancelled"
                    app.cancel_reason = f"冻结失败: {str(e)}"
            raise ValueError(f"创建申请失败：{str(e)}")

        self.db.refresh(application)
        return application

    @with_retry(max_retries=3, base_delay=0.15, max_delay=1.5)
    def approve_application(
        self, application_id: int, data: schemas.LeaveApplicationApprove
    ) -> LeaveApplication:
        if self.auth:
            from app.services.permission_service import PermissionService
            perm = PermissionService(self.db)
            if not perm.has_role(self.auth, perm.ROLE_MANAGER):
                app = self.db.query(LeaveApplication).filter(
                    LeaveApplication.id == application_id
                ).first()
                if app and not perm.can_approve_application(self.auth, app):
                    raise PermissionError("无权限审批此申请")

        with TransactionBoundary(self.db):
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
                year=application.start_date.year,
                application_id=application.id,
                operator=data.approver,
                reason=f"审批通过解冻: {application.application_no}"
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
            with TransactionBoundary(self.db):
                app = self.db.query(LeaveApplication).filter(
                    LeaveApplication.id == application.id
                ).first()
                if app:
                    app.transaction_id = transaction.id
        except Exception as e:
            with TransactionBoundary(self.db):
                app = self.db.query(LeaveApplication).filter(
                    LeaveApplication.id == application.id
                ).first()
                if app and app.status == "approved":
                    app.status = "pending"
                    app.approver = None
                    app.approved_at = None
            raise ValueError(f"审批失败：{str(e)}")

        self.db.refresh(application)
        return application

    def reject_application(
        self, application_id: int, data: schemas.LeaveApplicationReject
    ) -> LeaveApplication:
        with TransactionBoundary(self.db):
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
                year=application.start_date.year,
                application_id=application.id,
                operator=data.approver,
                reason=f"审批驳回解冻: {application.application_no}"
            )
        except Exception:
            pass

        self.db.refresh(application)
        return application

    def cancel_application(
        self, application_id: int, data: schemas.LeaveApplicationCancel
    ) -> LeaveApplication:
        with TransactionBoundary(self.db):
            application = self.db.query(LeaveApplication).filter(
                LeaveApplication.id == application_id
            ).first()
            if not application:
                raise ValueError("申请单不存在")

            if application.status not in ["pending"]:
                raise ValueError(f"当前状态为 {application.status}，无法撤销")

            application.previous_status = application.status
            application.status = "cancelled"
            application.cancelled_by = data.operator
            application.cancelled_at = datetime.now()
            application.cancel_reason = data.cancel_reason

            approval_record = ApprovalRecord(
                application_id=application.id,
                approver=data.operator,
                action="cancel",
                comment=data.cancel_reason or "申请人撤销"
            )
            self.db.add(approval_record)

        freeze_logs = self.db.query(FrozenBalanceLog).filter(
            FrozenBalanceLog.application_id == application.id,
            FrozenBalanceLog.operation == "freeze"
        ).all()
        freeze_log_ids = [log.id for log in freeze_logs]

        unfreeze_ok = False
        try:
            self.balance_service.unfreeze_balance(
                employee_id=application.employee_id,
                leave_type_id=application.leave_type_id,
                days=application.days,
                year=application.start_date.year,
                application_id=application.id,
                operator=data.operator,
                reason=f"撤销申请解冻: {application.application_no}",
                rollback_of_id=freeze_log_ids[0] if freeze_log_ids else None
            )
            unfreeze_ok = True
        except Exception as e:
            pass

        if not unfreeze_ok:
            try:
                self.balance_service.unfreeze_balance(
                    employee_id=application.employee_id,
                    leave_type_id=application.leave_type_id,
                    days=application.days,
                    year=application.start_date.year,
                    application_id=application.id,
                    operator=data.operator,
                    reason=f"撤销申请解冻(重试): {application.application_no}",
                    rollback_of_id=freeze_log_ids[0] if freeze_log_ids else None
                )
            except Exception:
                pass

        self.db.refresh(application)
        return application

    def restore_application(
        self, application_id: int, operator: str
    ) -> LeaveApplication:
        with TransactionBoundary(self.db):
            application = self.db.query(LeaveApplication).filter(
                LeaveApplication.id == application_id
            ).first()
            if not application:
                raise ValueError("申请单不存在")

            if application.status != "cancelled":
                raise ValueError(f"当前状态为 {application.status}，仅已撤销申请可恢复")

            leave_type = self.db.query(LeaveType).filter(
                LeaveType.id == application.leave_type_id
            ).first()
            if not leave_type or not leave_type.is_active:
                raise ValueError("假期类型已停用")

        unfreeze_logs = self.db.query(FrozenBalanceLog).filter(
            FrozenBalanceLog.application_id == application.id,
            FrozenBalanceLog.operation == "unfreeze"
        ).all()
        unfreeze_log_ids = [log.id for log in unfreeze_logs]

        try:
            self.balance_service.freeze_balance(
                employee_id=application.employee_id,
                leave_type_id=application.leave_type_id,
                days=application.days,
                year=application.start_date.year,
                application_id=application.id,
                operator=operator,
                reason=f"恢复申请冻结: {application.application_no}",
                rollback_of_id=unfreeze_log_ids[0] if unfreeze_log_ids else None
            )
        except Exception as e:
            raise ValueError(f"恢复申请失败：{str(e)}")

        with TransactionBoundary(self.db):
            app = self.db.query(LeaveApplication).filter(
                LeaveApplication.id == application_id
            ).first()
            if app:
                restore_to_status = app.previous_status or "pending"
                app.previous_status = app.status
                app.status = restore_to_status
                app.cancelled_by = None
                app.cancelled_at = None
                app.cancel_reason = None

                approval_record = ApprovalRecord(
                    application_id=app.id,
                    approver=operator,
                    action="restore",
                    comment=f"恢复已撤销申请"
                )
                self.db.add(approval_record)

        self.db.refresh(application)
        return application

    def get_application(self, application_id: int) -> Optional[LeaveApplication]:
        application = self.db.query(LeaveApplication).filter(
            LeaveApplication.id == application_id
        ).first()
        if application and self.auth:
            from app.services.permission_service import PermissionService
            perm = PermissionService(self.db)
            if not perm.can_view_employee(self.auth, application.employee_id):
                raise PermissionError("无权限查看此申请")
        return application

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

        query = self._apply_permission_filter(query)

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
