from datetime import datetime, date, timedelta
from typing import Optional, List, Tuple, Set, Dict, Any
import uuid
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, update, func
from dateutil.relativedelta import relativedelta

from app.models.models import (
    Employee, LeaveType, LeaveAccount, LeaveTransaction,
    LeaveApplication, HolidayConfig, FrozenBalanceLog,
    ExpireHold, ExpireHoldApproval, GrantRetroLink, SysUser,
    FieldPermission
)
from app.schemas import schemas
from app.utils import (
    TransactionBoundary, transactional, with_retry,
    is_workday, count_workdays, encode_cursor, decode_cursor,
    apply_field_permissions
)


class BalanceService:
    def __init__(self, db: Session, auth: Optional[schemas.AuthContext] = None):
        self.db = db
        self.auth = auth

    def _apply_permission_filter(self, query, model_cls):
        if not self.auth:
            return query
        from app.services.permission_service import PermissionService
        perm = PermissionService(self.db)
        if model_cls == LeaveAccount:
            return perm.filter_accounts_query(query, self.auth)
        if model_cls == LeaveTransaction:
            return perm.filter_transactions_query(query, self.auth)
        return query

    def _get_holiday_sets(self, year: Optional[int] = None) -> Tuple[Set[date], Set[date]]:
        q = self.db.query(HolidayConfig)
        if year:
            q = q.filter(HolidayConfig.year == year)
        configs = q.all()
        holidays = set()
        workdays = set()
        for c in configs:
            if c.type == "workday":
                workdays.add(c.date)
            else:
                holidays.add(c.date)
        return holidays, workdays

    def _get_or_create_account(
        self, employee_id: int, leave_type_id: int, year: int
    ) -> LeaveAccount:
        account = self.db.query(LeaveAccount).filter(
            LeaveAccount.employee_id == employee_id,
            LeaveAccount.leave_type_id == leave_type_id,
            LeaveAccount.year == year
        ).first()

        if not account:
            account = LeaveAccount(
                employee_id=employee_id,
                leave_type_id=leave_type_id,
                year=year,
                balance=0.0,
                frozen_balance=0.0,
                pending_expire_days=0.0,
                version=0
            )
            self.db.add(account)
            self.db.flush()
            self.db.refresh(account)
        return account

    def _check_and_update_version(self, account: LeaveAccount, expected_version: int) -> bool:
        stmt = (
            update(LeaveAccount)
            .where(
                LeaveAccount.id == account.id,
                LeaveAccount.version == expected_version
            )
            .values(version=expected_version + 1)
        )
        result = self.db.execute(stmt)
        self.db.flush()
        if result.rowcount == 1:
            account.version = expected_version + 1
            return True
        return False

    def _create_transaction(
        self,
        account: LeaveAccount,
        leave_type_id: int,
        employee_id: int,
        year: int,
        change_type: str,
        change_days: float,
        reason: Optional[str] = None,
        source_id: Optional[str] = None,
        source_type: Optional[str] = None,
        operator: Optional[str] = None,
        expire_date: Optional[date] = None,
        related_transaction_id: Optional[int] = None
    ) -> LeaveTransaction:
        transaction = LeaveTransaction(
            account_id=account.id,
            leave_type_id=leave_type_id,
            employee_id=employee_id,
            year=year,
            change_type=change_type,
            change_days=change_days,
            balance_after=account.balance,
            frozen_after=account.frozen_balance,
            reason=reason,
            source_id=source_id,
            source_type=source_type,
            operator=operator,
            expire_date=expire_date,
            related_transaction_id=related_transaction_id
        )
        self.db.add(transaction)
        self.db.flush()
        return transaction

    def _create_frozen_log(
        self,
        account: LeaveAccount,
        application_id: Optional[int],
        operation: str,
        days: float,
        frozen_before: float,
        frozen_after: float,
        operator: Optional[str] = None,
        reason: Optional[str] = None,
        rollback_of_id: Optional[int] = None
    ) -> FrozenBalanceLog:
        log = FrozenBalanceLog(
            account_id=account.id,
            employee_id=account.employee_id,
            leave_type_id=account.leave_type_id,
            application_id=application_id,
            operation=operation,
            days=days,
            balance_before=frozen_before,
            balance_after=frozen_after,
            operator=operator,
            reason=reason,
            rollback_of_id=rollback_of_id
        )
        self.db.add(log)
        self.db.flush()
        return log

    @with_retry(max_retries=3, base_delay=0.1, max_delay=1.0)
    def grant_leave(
        self,
        employee_id: int,
        leave_type_id: int,
        days: float,
        reason: str,
        operator: str,
        year: Optional[int] = None,
        expire_date: Optional[date] = None,
        retro_link_data: Optional[dict] = None
    ) -> Tuple[LeaveAccount, LeaveTransaction]:
        if year is None:
            year = date.today().year

        with TransactionBoundary(self.db):
            account = self._get_or_create_account(employee_id, leave_type_id, year)
            original_version = account.version

            if expire_date is None:
                leave_type = self.db.query(LeaveType).filter(LeaveType.id == leave_type_id).first()
                if leave_type and leave_type.expire_months > 0:
                    expire_date = date.today() + relativedelta(months=leave_type.expire_months)

            account.balance += days

            if not self._check_and_update_version(account, original_version):
                raise ValueError("并发冲突：账户已被其他操作修改，请重试")

            transaction = self._create_transaction(
                account=account,
                leave_type_id=leave_type_id,
                employee_id=employee_id,
                year=year,
                change_type="grant",
                change_days=days,
                reason=reason,
                operator=operator,
                expire_date=expire_date
            )

            if retro_link_data:
                retro_link = GrantRetroLink(
                    grant_transaction_id=transaction.id,
                    source_transaction_id=retro_link_data.get("source_transaction_id"),
                    source_application_id=retro_link_data.get("source_application_id"),
                    approval_no=retro_link_data.get("approval_no"),
                    document_no=retro_link_data.get("document_no"),
                    retro_reason=retro_link_data.get("retro_reason", reason),
                    created_by=operator
                )
                self.db.add(retro_link)

        self.db.refresh(account)
        self.db.refresh(transaction)
        return account, transaction

    @with_retry(max_retries=3, base_delay=0.1, max_delay=1.0)
    def deduct_leave(
        self,
        employee_id: int,
        leave_type_id: int,
        days: float,
        reason: str,
        operator: Optional[str] = None,
        year: Optional[int] = None,
        source_id: Optional[str] = None,
        source_type: Optional[str] = None
    ) -> Tuple[LeaveAccount, LeaveTransaction]:
        if year is None:
            year = date.today().year

        with TransactionBoundary(self.db):
            account = self._get_or_create_account(employee_id, leave_type_id, year)
            original_version = account.version

            available = account.balance - account.frozen_balance - account.pending_expire_days
            if available < days:
                raise ValueError(
                    f"可用余额不足：当前可用 {available} 天, 申请扣减 {days} 天"
                )

            account.balance -= days

            if not self._check_and_update_version(account, original_version):
                raise ValueError("并发冲突：账户已被其他操作修改，请重试")

            transaction = self._create_transaction(
                account=account,
                leave_type_id=leave_type_id,
                employee_id=employee_id,
                year=year,
                change_type="deduct",
                change_days=-days,
                reason=reason,
                source_id=source_id,
                source_type=source_type,
                operator=operator
            )

        self.db.refresh(account)
        self.db.refresh(transaction)
        return account, transaction

    @with_retry(max_retries=3, base_delay=0.1, max_delay=1.0)
    def adjust_balance(
        self,
        employee_id: int,
        leave_type_id: int,
        days: float,
        reason: str,
        operator: str,
        year: Optional[int] = None,
        expire_date: Optional[date] = None,
        retro_link_data: Optional[dict] = None
    ) -> Tuple[LeaveAccount, LeaveTransaction]:
        if year is None:
            year = date.today().year

        with TransactionBoundary(self.db):
            account = self._get_or_create_account(employee_id, leave_type_id, year)
            original_version = account.version

            new_balance = account.balance + days
            if new_balance < 0:
                raise ValueError("调整后余额不能为负数")

            account.balance = new_balance

            if not self._check_and_update_version(account, original_version):
                raise ValueError("并发冲突：账户已被其他操作修改，请重试")

            change_type = "adjust_add" if days > 0 else "adjust_subtract"
            transaction = self._create_transaction(
                account=account,
                leave_type_id=leave_type_id,
                employee_id=employee_id,
                year=year,
                change_type=change_type,
                change_days=days,
                reason=reason,
                operator=operator,
                expire_date=expire_date
            )

            if retro_link_data and days > 0:
                retro_link = GrantRetroLink(
                    grant_transaction_id=transaction.id,
                    source_transaction_id=retro_link_data.get("source_transaction_id"),
                    source_application_id=retro_link_data.get("source_application_id"),
                    approval_no=retro_link_data.get("approval_no"),
                    document_no=retro_link_data.get("document_no"),
                    retro_reason=retro_link_data.get("retro_reason", reason),
                    created_by=operator
                )
                self.db.add(retro_link)

        self.db.refresh(account)
        self.db.refresh(transaction)
        return account, transaction

    @with_retry(max_retries=3, base_delay=0.1, max_delay=1.0)
    def freeze_balance(
        self,
        employee_id: int,
        leave_type_id: int,
        days: float,
        year: Optional[int] = None,
        application_id: Optional[int] = None,
        operator: Optional[str] = None,
        reason: Optional[str] = None,
        rollback_of_id: Optional[int] = None
    ) -> LeaveAccount:
        if year is None:
            year = date.today().year

        with TransactionBoundary(self.db):
            account = self._get_or_create_account(employee_id, leave_type_id, year)
            original_version = account.version

            available = account.balance - account.frozen_balance - account.pending_expire_days
            if available < days:
                raise ValueError(f"可用余额不足，无法冻结：当前可用 {available} 天")

            frozen_before = account.frozen_balance
            account.frozen_balance += days
            frozen_after = account.frozen_balance

            if not self._check_and_update_version(account, original_version):
                raise ValueError("并发冲突：账户已被其他操作修改，请重试")

            self._create_frozen_log(
                account=account,
                application_id=application_id,
                operation="freeze",
                days=days,
                frozen_before=frozen_before,
                frozen_after=frozen_after,
                operator=operator,
                reason=reason or "申请请假冻结",
                rollback_of_id=rollback_of_id
            )

        self.db.refresh(account)
        return account

    @with_retry(max_retries=3, base_delay=0.1, max_delay=1.0)
    def unfreeze_balance(
        self,
        employee_id: int,
        leave_type_id: int,
        days: float,
        year: Optional[int] = None,
        application_id: Optional[int] = None,
        operator: Optional[str] = None,
        reason: Optional[str] = None,
        rollback_of_id: Optional[int] = None
    ) -> LeaveAccount:
        if year is None:
            year = date.today().year

        with TransactionBoundary(self.db):
            account = self._get_or_create_account(employee_id, leave_type_id, year)
            original_version = account.version

            if account.frozen_balance < days:
                raise ValueError(
                    f"冻结余额不足：当前冻结 {account.frozen_balance} 天"
                )

            frozen_before = account.frozen_balance
            account.frozen_balance -= days
            frozen_after = account.frozen_balance

            if not self._check_and_update_version(account, original_version):
                raise ValueError("并发冲突：账户已被其他操作修改，请重试")

            self._create_frozen_log(
                account=account,
                application_id=application_id,
                operation="unfreeze",
                days=days,
                frozen_before=frozen_before,
                frozen_after=frozen_after,
                operator=operator,
                reason=reason or "解冻",
                rollback_of_id=rollback_of_id
            )

        self.db.refresh(account)
        return account

    def annual_grant(
        self,
        leave_type_code: str = "annual",
        operator: str = "system",
        year: Optional[int] = None
    ) -> List[Tuple[LeaveAccount, LeaveTransaction]]:
        if year is None:
            year = date.today().year

        leave_type = self.db.query(LeaveType).filter(
            LeaveType.code == leave_type_code
        ).first()
        if not leave_type:
            raise ValueError(f"未找到假期类型: {leave_type_code}")

        employees = self.db.query(Employee).filter(Employee.is_active == True).all()
        results = []

        for emp in employees:
            try:
                grant_days = self._calculate_annual_grant(emp, leave_type, year)
                if grant_days > 0:
                    account, transaction = self.grant_leave(
                        employee_id=emp.id,
                        leave_type_id=leave_type.id,
                        days=grant_days,
                        reason=f"{year}年度{leave_type.name}发放",
                        operator=operator,
                        year=year
                    )
                    results.append((account, transaction))
            except Exception:
                continue

        return results

    def _calculate_annual_grant(
        self, employee: Employee, leave_type: LeaveType, year: int
    ) -> float:
        base_days = leave_type.annual_grant_days
        if base_days <= 0:
            return 0.0

        hire_date = employee.hire_date
        year_start = date(year, 1, 1)
        year_end = date(year, 12, 31)

        if hire_date > year_end:
            return 0.0

        if hire_date <= year_start:
            period_start = year_start
        else:
            period_start = hire_date
        period_end = year_end

        if leave_type.use_workdays:
            holidays, workdays = self._get_holiday_sets(year)
            total_days_year = count_workdays(year_start, year_end, holidays, workdays)
            days_in_period = count_workdays(period_start, period_end, holidays, workdays)
        else:
            total_days_year = (year_end - year_start).days + 1
            days_in_period = (period_end - period_start).days + 1

        return round(base_days * days_in_period / total_days_year, 2)

    def year_end_carry_over(
        self, from_year: int, to_year: int, operator: str = "system"
    ) -> List[Tuple[LeaveAccount, LeaveTransaction]]:
        results = []
        accounts = self.db.query(LeaveAccount).filter(
            LeaveAccount.year == from_year,
            LeaveAccount.balance > 0
        ).all()

        holidays, workdays = self._get_holiday_sets(to_year)

        for account in accounts:
            try:
                with TransactionBoundary(self.db):
                    leave_type = self.db.query(LeaveType).filter(
                        LeaveType.id == account.leave_type_id
                    ).first()
                    if not leave_type:
                        continue

                    carry_over_days = min(account.balance, leave_type.carry_over_days)
                    if carry_over_days <= 0:
                        if account.balance > 0:
                            self._expire_remaining(account, from_year, operator, leave_type)
                        continue

                    new_account = self._get_or_create_account(
                        account.employee_id, account.leave_type_id, to_year
                    )
                    original_new_version = new_account.version

                    expire_date = None
                    if leave_type.expire_months > 0:
                        expire_base = date(to_year, 1, 1)
                        raw_expire = expire_base + relativedelta(months=leave_type.expire_months)
                        if leave_type.use_workdays:
                            expire_date = raw_expire
                            while not is_workday(expire_date, holidays, workdays):
                                expire_date += timedelta(days=1)
                        else:
                            expire_date = raw_expire

                    new_account.balance += carry_over_days

                    if not self._check_and_update_version(new_account, original_new_version):
                        raise ValueError("并发冲突")

                    carry_in_txn = self._create_transaction(
                        account=new_account,
                        leave_type_id=account.leave_type_id,
                        employee_id=account.employee_id,
                        year=to_year,
                        change_type="carry_over",
                        change_days=carry_over_days,
                        reason=f"从{from_year}年结转",
                        operator=operator,
                        expire_date=expire_date
                    )

                    original_account_version = account.version
                    account.balance -= carry_over_days
                    if not self._check_and_update_version(account, original_account_version):
                        raise ValueError("并发冲突")

                    self._create_transaction(
                        account=account,
                        leave_type_id=account.leave_type_id,
                        employee_id=account.employee_id,
                        year=from_year,
                        change_type="carry_out",
                        change_days=-carry_over_days,
                        reason=f"结转至{to_year}年",
                        operator=operator,
                        related_transaction_id=carry_in_txn.id
                    )

                    if account.balance > 0:
                        self._expire_remaining(account, from_year, operator, leave_type)

                    self.db.commit()
                    self.db.refresh(new_account)
                    results.append((new_account, carry_in_txn))
            except Exception:
                self.db.rollback()
                continue

        return results

    def _expire_remaining(
        self, account: LeaveAccount, year: int, operator: str,
        leave_type: Optional[LeaveType] = None
    ):
        if leave_type and leave_type.require_expire_approval:
            txns = self.db.query(LeaveTransaction).filter(
                LeaveTransaction.account_id == account.id,
                LeaveTransaction.change_type.in_(["grant", "carry_over", "adjust_add"]),
                LeaveTransaction.is_reversed == False,
                LeaveTransaction.change_days > 0
            ).all()
            remaining = account.balance
            for txn in txns:
                if remaining <= 0:
                    break
                hold_amount = min(txn.change_days, remaining)
                hold = ExpireHold(
                    hold_no=f"EH{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}",
                    employee_id=account.employee_id,
                    leave_type_id=account.leave_type_id,
                    account_id=account.id,
                    transaction_id=txn.id,
                    expire_date=txn.expire_date or date.today(),
                    hold_days=hold_amount,
                    status="pending",
                    operator=operator
                )
                self.db.add(hold)
                remaining -= hold_amount

            original_version = account.version
            account.pending_expire_days += account.balance
            if not self._check_and_update_version(account, original_version):
                raise ValueError("并发冲突")
            return

        expire_days = account.balance
        original_version = account.version
        account.balance = 0.0
        if not self._check_and_update_version(account, original_version):
            raise ValueError("并发冲突")
        self._create_transaction(
            account=account,
            leave_type_id=account.leave_type_id,
            employee_id=account.employee_id,
            year=year,
            change_type="expire",
            change_days=-expire_days,
            reason=f"{year}年度过期清零",
            operator=operator
        )

    def scan_expired_and_create_holds(self, operator: str = "system") -> List[ExpireHold]:
        today = date.today()
        created_holds = []

        need_approval_types = self.db.query(LeaveType).filter(
            LeaveType.require_expire_approval == True
        ).all()
        need_approval_ids = {lt.id for lt in need_approval_types}

        expired_txns = self.db.query(LeaveTransaction).filter(
            LeaveTransaction.change_type.in_(["grant", "carry_over", "adjust_add"]),
            LeaveTransaction.expire_date.isnot(None),
            LeaveTransaction.expire_date < today,
            LeaveTransaction.is_reversed == False,
            LeaveTransaction.change_days > 0
        ).all()

        for txn in expired_txns:
            try:
                with TransactionBoundary(self.db):
                    account = self.db.query(LeaveAccount).filter(
                        LeaveAccount.id == txn.account_id
                    ).first()
                    if not account or account.balance <= 0:
                        continue

                    if account.leave_type_id in need_approval_ids:
                        existing_hold = self.db.query(ExpireHold).filter(
                            ExpireHold.transaction_id == txn.id,
                            ExpireHold.status == "pending"
                        ).first()
                        if existing_hold:
                            continue

                        hold_amount = min(txn.change_days, account.balance - account.pending_expire_days)
                        if hold_amount <= 0:
                            continue

                        hold = ExpireHold(
                            hold_no=f"EH{datetime.now().strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:6].upper()}",
                            employee_id=txn.employee_id,
                            leave_type_id=txn.leave_type_id,
                            account_id=txn.account_id,
                            transaction_id=txn.id,
                            expire_date=txn.expire_date,
                            hold_days=hold_amount,
                            status="pending",
                            operator=operator
                        )
                        self.db.add(hold)

                        original_version = account.version
                        account.pending_expire_days += hold_amount
                        if not self._check_and_update_version(account, original_version):
                            raise ValueError("并发冲突")
                        created_holds.append(hold)
                    else:
                        expire_amount = min(txn.change_days, account.balance)
                        if expire_amount <= 0:
                            continue

                        original_version = account.version
                        account.balance -= expire_amount
                        if not self._check_and_update_version(account, original_version):
                            raise ValueError("并发冲突")

                        expire_txn = self._create_transaction(
                            account=account,
                            leave_type_id=txn.leave_type_id,
                            employee_id=txn.employee_id,
                            year=txn.year,
                            change_type="expire",
                            change_days=-expire_amount,
                            reason=f"过期自动清零(原交易ID:{txn.id})",
                            operator=operator,
                            expire_date=today,
                            related_transaction_id=txn.id
                        )
                        txn.is_reversed = True
                        txn.reversed_by_id = expire_txn.id
            except Exception:
                continue

        return created_holds

    def process_expired_balance(self, operator: str = "system") -> List[LeaveTransaction]:
        today = date.today()
        transactions = []

        auto_txns = self.db.query(LeaveTransaction).filter(
            LeaveTransaction.change_type.in_(["grant", "carry_over", "adjust_add"]),
            LeaveTransaction.expire_date.isnot(None),
            LeaveTransaction.expire_date < today,
            LeaveTransaction.is_reversed == False,
            LeaveTransaction.change_days > 0
        ).all()

        for txn in auto_txns:
            try:
                with TransactionBoundary(self.db):
                    account = self.db.query(LeaveAccount).filter(
                        LeaveAccount.id == txn.account_id
                    ).first()
                    if not account or account.balance <= 0:
                        continue

                    expire_amount = min(txn.change_days, account.balance - account.pending_expire_days)
                    if expire_amount <= 0:
                        continue

                    original_version = account.version
                    account.balance -= expire_amount
                    if not self._check_and_update_version(account, original_version):
                        raise ValueError("并发冲突")

                    expire_txn = self._create_transaction(
                        account=account,
                        leave_type_id=txn.leave_type_id,
                        employee_id=txn.employee_id,
                        year=txn.year,
                        change_type="expire",
                        change_days=-expire_amount,
                        reason=f"过期自动清零(原交易ID:{txn.id})",
                        operator=operator,
                        expire_date=today,
                        related_transaction_id=txn.id
                    )
                    txn.is_reversed = True
                    txn.reversed_by_id = expire_txn.id
                    transactions.append(expire_txn)
            except Exception:
                continue

        return transactions

    def approve_expire_hold(
        self, hold_id: int, approver: str, comment: Optional[str] = None
    ) -> ExpireHold:
        with TransactionBoundary(self.db):
            hold = self.db.query(ExpireHold).filter(ExpireHold.id == hold_id).first()
            if not hold:
                raise ValueError("过期清零审批单不存在")
            if hold.status != "pending":
                raise ValueError(f"当前状态 {hold.status} 无法审批")

            account = self.db.query(LeaveAccount).filter(
                LeaveAccount.id == hold.account_id
            ).first()
            if not account:
                raise ValueError("账户不存在")

            deduct_amount = min(hold.hold_days, account.balance)
            original_version = account.version
            account.balance -= deduct_amount
            if account.pending_expire_days >= hold.hold_days:
                account.pending_expire_days -= hold.hold_days

            if not self._check_and_update_version(account, original_version):
                raise ValueError("并发冲突：账户已被其他操作修改")

            expire_txn = self._create_transaction(
                account=account,
                leave_type_id=hold.leave_type_id,
                employee_id=hold.employee_id,
                year=account.year,
                change_type="expire",
                change_days=-deduct_amount,
                reason=f"过期清零审批通过(审批单:{hold.hold_no})",
                operator=approver,
                expire_date=date.today(),
                related_transaction_id=hold.transaction_id
            )

            hold.status = "approved"
            hold.approver = approver
            hold.approved_at = datetime.now()

            approval = ExpireHoldApproval(
                hold_id=hold.id,
                approver=approver,
                action="approve",
                comment=comment
            )
            self.db.add(approval)

        self.db.refresh(hold)
        return hold

    def reject_expire_hold(
        self, hold_id: int, approver: str, reject_reason: str
    ) -> ExpireHold:
        with TransactionBoundary(self.db):
            hold = self.db.query(ExpireHold).filter(ExpireHold.id == hold_id).first()
            if not hold:
                raise ValueError("过期清零审批单不存在")
            if hold.status != "pending":
                raise ValueError(f"当前状态 {hold.status} 无法驳回")

            account = self.db.query(LeaveAccount).filter(
                LeaveAccount.id == hold.account_id
            ).first()
            if account:
                original_version = account.version
                if account.pending_expire_days >= hold.hold_days:
                    account.pending_expire_days -= hold.hold_days
                self._check_and_update_version(account, original_version)

            hold.status = "rejected"
            hold.approver = approver
            hold.reject_reason = reject_reason

            approval = ExpireHoldApproval(
                hold_id=hold.id,
                approver=approver,
                action="reject",
                comment=reject_reason
            )
            self.db.add(approval)

        self.db.refresh(hold)
        return hold

    def timeout_expire_holds(
        self, default_action: str = "approve", escalate_to: Optional[str] = None
    ) -> List[ExpireHold]:
        now = datetime.now()
        pending_holds = self.db.query(ExpireHold).filter(
            ExpireHold.status == "pending"
        ).all()
        processed = []
        for hold in pending_holds:
            elapsed_hours = (now - hold.created_at).total_seconds() / 3600
            if elapsed_hours > hold.timeout_hours:
                try:
                    if default_action == "approve":
                        result = self.approve_expire_hold(
                            hold.id, approver=escalate_to or "system_timeout",
                            comment=f"审批超时({elapsed_hours:.0f}h)，自动清零"
                        )
                    else:
                        result = self.reject_expire_hold(
                            hold.id, approver=escalate_to or "system_timeout",
                            reject_reason=f"审批超时({elapsed_hours:.0f}h)，自动保留"
                        )
                    processed.append(result)
                except Exception:
                    if escalate_to:
                        hold.escalated_to = escalate_to
                        self.db.commit()
        return processed

    def recover_stuck_holds(self) -> List[ExpireHold]:
        stuck = self.db.query(ExpireHold).filter(
            ExpireHold.status == "pending"
        ).all()
        recovered = []
        for hold in stuck:
            account = self.db.query(LeaveAccount).filter(
                LeaveAccount.id == hold.account_id
            ).first()
            if not account:
                hold.status = "rejected"
                hold.reject_reason = "账户不存在，自动关闭"
                recovered.append(hold)
            elif account.balance <= 0 and account.pending_expire_days <= 0:
                hold.status = "rejected"
                hold.reject_reason = "余额已为零，自动关闭"
                recovered.append(hold)
        if recovered:
            self.db.commit()
        return recovered

    def list_expire_holds(
        self,
        status: Optional[str] = None,
        employee_id: Optional[int] = None,
        leave_type_id: Optional[int] = None,
        skip: int = 0,
        limit: int = 100
    ) -> Tuple[int, List[ExpireHold]]:
        query = self.db.query(ExpireHold)
        if status:
            query = query.filter(ExpireHold.status == status)
        if employee_id:
            query = query.filter(ExpireHold.employee_id == employee_id)
        if leave_type_id:
            query = query.filter(ExpireHold.leave_type_id == leave_type_id)

        total = query.count()
        items = query.order_by(ExpireHold.created_at.desc()).offset(skip).limit(limit).all()
        return total, items

    def get_balance(
        self,
        employee_id: Optional[int] = None,
        employee_no: Optional[str] = None,
        leave_type_id: Optional[int] = None,
        leave_type_code: Optional[str] = None,
        year: Optional[int] = None
    ) -> List[schemas.BalanceSummary]:
        query = self.db.query(LeaveAccount).join(Employee).join(LeaveType)

        if employee_id:
            query = query.filter(LeaveAccount.employee_id == employee_id)
        if employee_no:
            query = query.filter(Employee.employee_no == employee_no)
        if leave_type_id:
            query = query.filter(LeaveAccount.leave_type_id == leave_type_id)
        if leave_type_code:
            query = query.filter(LeaveType.code == leave_type_code)
        if year:
            query = query.filter(LeaveAccount.year == year)

        query = self._apply_permission_filter(query, LeaveAccount)

        accounts = query.all()
        summaries = []

        for acc in accounts:
            summaries.append(schemas.BalanceSummary(
                employee_id=acc.employee_id,
                employee_name=acc.employee.name,
                leave_type_code=acc.leave_type.code,
                leave_type_name=acc.leave_type.name,
                year=acc.year,
                balance=acc.balance,
                frozen_balance=acc.frozen_balance,
                pending_expire_days=acc.pending_expire_days,
                available_balance=acc.balance - acc.frozen_balance - acc.pending_expire_days
            ))

        return summaries

    def get_transactions(
        self,
        employee_id: Optional[int] = None,
        leave_type_id: Optional[int] = None,
        year: Optional[int] = None,
        change_type: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        source_id: Optional[str] = None,
        skip: int = 0,
        limit: int = 100
    ) -> Tuple[int, List[LeaveTransaction]]:
        query = self.db.query(LeaveTransaction)

        if employee_id:
            query = query.filter(LeaveTransaction.employee_id == employee_id)
        if leave_type_id:
            query = query.filter(LeaveTransaction.leave_type_id == leave_type_id)
        if year:
            query = query.filter(LeaveTransaction.year == year)
        if change_type:
            query = query.filter(LeaveTransaction.change_type == change_type)
        if start_date:
            query = query.filter(LeaveTransaction.created_at >= datetime.combine(start_date, datetime.min.time()))
        if end_date:
            query = query.filter(LeaveTransaction.created_at <= datetime.combine(end_date, datetime.max.time()))
        if source_id:
            query = query.filter(LeaveTransaction.source_id == source_id)

        query = self._apply_permission_filter(query, LeaveTransaction)

        total = query.count()
        transactions = query.order_by(
            LeaveTransaction.id.desc()
        ).offset(skip).limit(limit).all()

        return total, transactions

    def get_transactions_cursor(
        self,
        employee_id: Optional[int] = None,
        leave_type_id: Optional[int] = None,
        year: Optional[int] = None,
        change_type: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        source_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 100,
        include_total: bool = True
    ) -> schemas.KeysetPaginatedResponse:
        query = self.db.query(LeaveTransaction)

        if employee_id:
            query = query.filter(LeaveTransaction.employee_id == employee_id)
        if leave_type_id:
            query = query.filter(LeaveTransaction.leave_type_id == leave_type_id)
        if year:
            query = query.filter(LeaveTransaction.year == year)
        if change_type:
            query = query.filter(LeaveTransaction.change_type == change_type)
        if start_date:
            query = query.filter(LeaveTransaction.created_at >= datetime.combine(start_date, datetime.min.time()))
        if end_date:
            query = query.filter(LeaveTransaction.created_at <= datetime.combine(end_date, datetime.max.time()))
        if source_id:
            query = query.filter(LeaveTransaction.source_id == source_id)

        query = self._apply_permission_filter(query, LeaveTransaction)

        sort_key = "id_desc"
        if cursor:
            cursor_id, cursor_sk = decode_cursor(cursor)
            if cursor_id is None:
                raise ValueError("无效或已篡改的游标")
            sort_key = cursor_sk or "id_desc"
            query = query.filter(LeaveTransaction.id < cursor_id)

        total_count = None
        if include_total:
            total_count = query.count()

        page_query = query.order_by(
            LeaveTransaction.id.desc()
        ).limit(limit + 1)

        items = page_query.all()
        has_more = len(items) > limit
        if has_more:
            items = items[:limit]

        next_cursor = None
        if items and has_more:
            last = items[-1]
            next_cursor = encode_cursor(last.id, sort_key)

        return schemas.KeysetPaginatedResponse(
            items=items,
            next_cursor=next_cursor,
            has_more=has_more,
            total_count=total_count
        )

    def get_frozen_logs(
        self,
        account_id: Optional[int] = None,
        employee_id: Optional[int] = None,
        application_id: Optional[int] = None,
        skip: int = 0,
        limit: int = 100
    ) -> Tuple[int, List[FrozenBalanceLog]]:
        query = self.db.query(FrozenBalanceLog)
        if account_id:
            query = query.filter(FrozenBalanceLog.account_id == account_id)
        if employee_id:
            query = query.filter(FrozenBalanceLog.employee_id == employee_id)
        if application_id:
            query = query.filter(FrozenBalanceLog.application_id == application_id)

        total = query.count()
        logs = query.order_by(FrozenBalanceLog.created_at.desc()).offset(skip).limit(limit).all()
        return total, logs

    def get_retro_link(
        self, grant_transaction_id: int
    ) -> Optional[GrantRetroLink]:
        return self.db.query(GrantRetroLink).filter(
            GrantRetroLink.grant_transaction_id == grant_transaction_id
        ).first()

    def get_retro_chain(
        self, grant_transaction_id: int, max_depth: int = 10
    ) -> schemas.RetroLinkChain:
        chain = []
        visited = set()
        current_txn_id = grant_transaction_id

        for _ in range(max_depth):
            if current_txn_id in visited:
                break
            visited.add(current_txn_id)

            txn = self.db.query(LeaveTransaction).filter(
                LeaveTransaction.id == current_txn_id
            ).first()
            if not txn:
                break

            retro = self.db.query(GrantRetroLink).filter(
                GrantRetroLink.grant_transaction_id == current_txn_id
            ).first()

            app_no = None
            app_id = None
            if retro and retro.source_application_id:
                app = self.db.query(LeaveApplication).filter(
                    LeaveApplication.id == retro.source_application_id
                ).first()
                if app:
                    app_id = app.id
                    app_no = app.application_no

            node = schemas.RetroLinkChainNode(
                transaction_id=txn.id,
                change_type=txn.change_type,
                change_days=txn.change_days,
                reason=txn.reason,
                operator=txn.operator,
                created_at=txn.created_at,
                retro_link=retro,
                related_application_id=app_id,
                related_application_no=app_no
            )
            chain.append(node)

            if txn.related_transaction_id:
                current_txn_id = txn.related_transaction_id
            elif retro and retro.source_transaction_id:
                current_txn_id = retro.source_transaction_id
            else:
                break

        return schemas.RetroLinkChain(
            grant_transaction_id=grant_transaction_id,
            chain=chain,
            total_depth=len(chain)
        )

    def add_holiday(
        self, d: date, name: Optional[str], holiday_type: str = "holiday",
        substitute_for: Optional[date] = None
    ) -> HolidayConfig:
        existing = self.db.query(HolidayConfig).filter(HolidayConfig.date == d).first()
        if existing:
            existing.name = name
            existing.type = holiday_type
            existing.year = d.year
            existing.substitute_for = substitute_for
            self.db.commit()
            self.db.refresh(existing)
            return existing

        cfg = HolidayConfig(
            date=d, name=name, type=holiday_type,
            year=d.year, substitute_for=substitute_for
        )
        self.db.add(cfg)
        self.db.commit()
        self.db.refresh(cfg)
        return cfg

    def list_holidays(
        self, year: Optional[int] = None, holiday_type: Optional[str] = None
    ) -> List[HolidayConfig]:
        query = self.db.query(HolidayConfig)
        if year:
            query = query.filter(HolidayConfig.year == year)
        if holiday_type:
            query = query.filter(HolidayConfig.type == holiday_type)
        return query.order_by(HolidayConfig.date.asc()).all()

    def get_field_permissions(
        self, role: Optional[str] = None, resource: Optional[str] = None
    ) -> List[FieldPermission]:
        query = self.db.query(FieldPermission)
        if role:
            query = query.filter(FieldPermission.role == role)
        if resource:
            query = query.filter(FieldPermission.resource == resource)
        return query.all()

    def set_field_permission(
        self, role: str, resource: str, field_name: str,
        access: str = "visible", mask_pattern: Optional[str] = None
    ) -> FieldPermission:
        existing = self.db.query(FieldPermission).filter(
            FieldPermission.role == role,
            FieldPermission.resource == resource,
            FieldPermission.field_name == field_name
        ).first()
        if existing:
            existing.access = access
            existing.mask_pattern = mask_pattern
            self.db.commit()
            self.db.refresh(existing)
            return existing

        fp = FieldPermission(
            role=role, resource=resource, field_name=field_name,
            access=access, mask_pattern=mask_pattern
        )
        self.db.add(fp)
        self.db.commit()
        self.db.refresh(fp)
        return fp

    def apply_field_filter(
        self, data: dict, resource: str
    ) -> schemas.FieldFilteredResponse:
        if not self.auth:
            return schemas.FieldFilteredResponse(data=data)
        fps = self.get_field_permissions(role=self.auth.role, resource=resource)
        if not fps:
            return schemas.FieldFilteredResponse(data=data)
        filtered, masked, hidden = apply_field_permissions(
            data, self.auth.role, resource, fps
        )
        return schemas.FieldFilteredResponse(
            data=filtered, masked_fields=masked, hidden_fields=hidden
        )
