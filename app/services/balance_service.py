from datetime import datetime, date, timedelta
from typing import Optional, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, update
from dateutil.relativedelta import relativedelta

from app.models.models import (
    Employee, LeaveType, LeaveAccount, LeaveTransaction,
    LeaveApplication, ApprovalRecord
)
from app.schemas import schemas


class BalanceService:
    def __init__(self, db: Session):
        self.db = db

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
        expire_date: Optional[date] = None
    ) -> LeaveTransaction:
        balance_after = account.balance
        transaction = LeaveTransaction(
            account_id=account.id,
            leave_type_id=leave_type_id,
            employee_id=employee_id,
            year=year,
            change_type=change_type,
            change_days=change_days,
            balance_after=balance_after,
            reason=reason,
            source_id=source_id,
            source_type=source_type,
            operator=operator,
            expire_date=expire_date
        )
        self.db.add(transaction)
        self.db.flush()
        return transaction

    def grant_leave(
        self,
        employee_id: int,
        leave_type_id: int,
        days: float,
        reason: str,
        operator: str,
        year: Optional[int] = None,
        expire_date: Optional[date] = None
    ) -> Tuple[LeaveAccount, LeaveTransaction]:
        if year is None:
            year = date.today().year

        account = self._get_or_create_account(employee_id, leave_type_id, year)
        original_version = account.version

        if expire_date is None:
            leave_type = self.db.query(LeaveType).filter(LeaveType.id == leave_type_id).first()
            if leave_type and leave_type.expire_months > 0:
                expire_date = date.today() + relativedelta(months=leave_type.expire_months)

        account.balance += days

        if not self._check_and_update_version(account, original_version):
            self.db.rollback()
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

        self.db.commit()
        self.db.refresh(account)
        self.db.refresh(transaction)
        return account, transaction

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

        account = self._get_or_create_account(employee_id, leave_type_id, year)
        original_version = account.version

        available = account.balance - account.frozen_balance
        if available < days:
            raise ValueError(
                f"可用余额不足：当前可用 {available} 天，申请扣减 {days} 天"
            )

        account.balance -= days

        if not self._check_and_update_version(account, original_version):
            self.db.rollback()
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

        self.db.commit()
        self.db.refresh(account)
        self.db.refresh(transaction)
        return account, transaction

    def adjust_balance(
        self,
        employee_id: int,
        leave_type_id: int,
        days: float,
        reason: str,
        operator: str,
        year: Optional[int] = None,
        expire_date: Optional[date] = None
    ) -> Tuple[LeaveAccount, LeaveTransaction]:
        if year is None:
            year = date.today().year

        account = self._get_or_create_account(employee_id, leave_type_id, year)
        original_version = account.version

        new_balance = account.balance + days
        if new_balance < 0:
            raise ValueError("调整后余额不能为负数")

        account.balance = new_balance

        if not self._check_and_update_version(account, original_version):
            self.db.rollback()
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

        self.db.commit()
        self.db.refresh(account)
        self.db.refresh(transaction)
        return account, transaction

    def freeze_balance(
        self,
        employee_id: int,
        leave_type_id: int,
        days: float,
        year: Optional[int] = None
    ) -> LeaveAccount:
        if year is None:
            year = date.today().year

        account = self._get_or_create_account(employee_id, leave_type_id, year)
        original_version = account.version

        available = account.balance - account.frozen_balance
        if available < days:
            raise ValueError(f"可用余额不足，无法冻结：当前可用 {available} 天")

        account.frozen_balance += days

        if not self._check_and_update_version(account, original_version):
            self.db.rollback()
            raise ValueError("并发冲突：账户已被其他操作修改，请重试")

        self.db.commit()
        self.db.refresh(account)
        return account

    def unfreeze_balance(
        self,
        employee_id: int,
        leave_type_id: int,
        days: float,
        year: Optional[int] = None
    ) -> LeaveAccount:
        if year is None:
            year = date.today().year

        account = self._get_or_create_account(employee_id, leave_type_id, year)
        original_version = account.version

        if account.frozen_balance < days:
            raise ValueError(
                f"冻结余额不足：当前冻结 {account.frozen_balance} 天"
            )

        account.frozen_balance -= days

        if not self._check_and_update_version(account, original_version):
            self.db.rollback()
            raise ValueError("并发冲突：账户已被其他操作修改，请重试")

        self.db.commit()
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
                self.db.rollback()
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
            return base_days

        days_in_year = (year_end - hire_date).days + 1
        total_days_year = (year_end - year_start).days + 1
        return round(base_days * days_in_year / total_days_year, 2)

    def year_end_carry_over(
        self, from_year: int, to_year: int, operator: str = "system"
    ) -> List[Tuple[LeaveAccount, LeaveTransaction]]:
        results = []
        accounts = self.db.query(LeaveAccount).filter(
            LeaveAccount.year == from_year,
            LeaveAccount.balance > 0
        ).all()

        for account in accounts:
            try:
                leave_type = self.db.query(LeaveType).filter(
                    LeaveType.id == account.leave_type_id
                ).first()
                if not leave_type:
                    continue

                carry_over_days = min(account.balance, leave_type.carry_over_days)
                if carry_over_days <= 0:
                    if account.balance > 0:
                        self._expire_remaining(account, from_year, operator)
                    continue

                new_account = self._get_or_create_account(
                    account.employee_id, account.leave_type_id, to_year
                )
                original_version = new_account.version

                expire_date = None
                if leave_type.expire_months > 0:
                    expire_date = date(to_year, 1, 1) + relativedelta(
                        months=leave_type.expire_months
                    )

                new_account.balance += carry_over_days

                if not self._check_and_update_version(new_account, original_version):
                    self.db.rollback()
                    continue

                self._create_transaction(
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
                    self.db.rollback()
                    continue

                self._create_transaction(
                    account=account,
                    leave_type_id=account.leave_type_id,
                    employee_id=account.employee_id,
                    year=from_year,
                    change_type="carry_out",
                    change_days=-carry_over_days,
                    reason=f"结转至{to_year}年",
                    operator=operator
                )

                if account.balance > 0:
                    self._expire_remaining(account, from_year, operator)

                self.db.commit()
                self.db.refresh(new_account)
                results.append((new_account, None))
            except Exception:
                self.db.rollback()
                continue

        return results

    def _expire_remaining(self, account: LeaveAccount, year: int, operator: str):
        expire_days = account.balance
        original_version = account.version
        account.balance = 0.0
        if not self._check_and_update_version(account, original_version):
            self.db.rollback()
            return
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

    def process_expired_balance(self, operator: str = "system") -> List[LeaveTransaction]:
        today = date.today()
        transactions = []

        expired_transactions = self.db.query(LeaveTransaction).filter(
            LeaveTransaction.change_type.in_(["grant", "carry_over", "adjust_add"]),
            LeaveTransaction.expire_date.isnot(None),
            LeaveTransaction.expire_date < today,
            LeaveTransaction.is_reversed == False
        ).all()

        for txn in expired_transactions:
            try:
                account = self.db.query(LeaveAccount).filter(
                    LeaveAccount.id == txn.account_id
                ).first()
                if not account or account.balance <= 0:
                    continue

                expire_amount = min(txn.change_days, account.balance)
                if expire_amount <= 0:
                    continue

                original_version = account.version
                account.balance -= expire_amount
                if not self._check_and_update_version(account, original_version):
                    self.db.rollback()
                    continue

                expire_txn = self._create_transaction(
                    account=account,
                    leave_type_id=txn.leave_type_id,
                    employee_id=txn.employee_id,
                    year=txn.year,
                    change_type="expire",
                    change_days=-expire_amount,
                    reason=f"过期自动清零(原交易ID:{txn.id})",
                    operator=operator,
                    expire_date=today
                )
                txn.is_reversed = True
                txn.reversed_by_id = expire_txn.id

                self.db.commit()
                self.db.refresh(expire_txn)
                transactions.append(expire_txn)
            except Exception:
                self.db.rollback()
                continue

        return transactions

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
                available_balance=acc.balance - acc.frozen_balance
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

        total = query.count()
        transactions = query.order_by(LeaveTransaction.created_at.desc()).offset(skip).limit(limit).all()

        return total, transactions
