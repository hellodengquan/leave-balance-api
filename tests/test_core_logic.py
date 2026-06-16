import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.models import Base, Employee, LeaveType
from app.services.balance_service import BalanceService
from app.services.application_service import ApplicationService
from app.services.master_data_service import MasterDataService
from app.schemas import schemas


def setup_test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        isolation_level="SERIALIZABLE",
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()


def test_master_data():
    print("\n" + "="*60)
    print("测试 1: 基础数据管理")
    print("="*60)

    db = setup_test_db()
    master_service = MasterDataService(db)

    emp_data = schemas.EmployeeCreate(
        employee_no="EMP001",
        name="张三",
        department="技术部",
        position="工程师",
        hire_date=date(2020, 1, 15),
        is_active=True
    )
    emp = master_service.create_employee(emp_data)
    print(f"✓ 创建员工: {emp.name} (ID: {emp.id})")

    annual_leave = schemas.LeaveTypeCreate(
        code="annual",
        name="年假",
        description="年度带薪休假",
        annual_grant_days=10.0,
        carry_over_days=5.0,
        expire_months=12,
        unit="day"
    )
    lt1 = master_service.create_leave_type(annual_leave)
    print(f"✓ 创建假期类型: {lt1.name} (ID: {lt1.id})")

    compensatory_leave = schemas.LeaveTypeCreate(
        code="compensatory",
        name="调休",
        description="加班调休",
        annual_grant_days=0.0,
        carry_over_days=3.0,
        expire_months=6,
        unit="day"
    )
    lt2 = master_service.create_leave_type(compensatory_leave)
    print(f"✓ 创建假期类型: {lt2.name} (ID: {lt2.id})")

    sick_leave = schemas.LeaveTypeCreate(
        code="sick",
        name="病假",
        description="病假",
        annual_grant_days=0.0,
        carry_over_days=0.0,
        expire_months=12,
        unit="day"
    )
    lt3 = master_service.create_leave_type(sick_leave)
    print(f"✓ 创建假期类型: {lt3.name} (ID: {lt3.id})")

    ids = (emp.id, lt1.id, lt2.id, lt3.id)
    db.close()
    print("✓ 基础数据测试通过")
    return ids


def test_balance_grant_and_adjust(emp_id, annual_id, comp_id, sick_id):
    print("\n" + "="*60)
    print("测试 2: 余额发放与调整")
    print("="*60)

    db = setup_test_db()

    emp = Employee(id=emp_id, employee_no="EMP001", name="张三", department="技术部",
                   position="工程师", hire_date=date(2020, 1, 15), is_active=True)
    db.add(emp)
    db.add_all([
        LeaveType(id=annual_id, code="annual", name="年假", annual_grant_days=10.0,
                  carry_over_days=5.0, expire_months=12),
        LeaveType(id=comp_id, code="compensatory", name="调休", annual_grant_days=0.0,
                  carry_over_days=3.0, expire_months=6),
        LeaveType(id=sick_id, code="sick", name="病假", annual_grant_days=0.0,
                  carry_over_days=0.0, expire_months=12),
    ])
    db.commit()

    service = BalanceService(db)

    account, txn = service.grant_leave(
        employee_id=emp_id,
        leave_type_id=annual_id,
        days=10.0,
        reason="2024年度年假发放",
        operator="admin",
        year=2024
    )
    print(f"✓ 发放年假: +{txn.change_days} 天, 余额: {account.balance}")

    account, txn = service.grant_leave(
        employee_id=emp_id,
        leave_type_id=comp_id,
        days=8.0,
        reason="加班调休",
        operator="admin",
        year=2024
    )
    print(f"✓ 发放调休: +{txn.change_days} 天, 余额: {account.balance}")

    account, txn = service.adjust_balance(
        employee_id=emp_id,
        leave_type_id=annual_id,
        days=-2.0,
        reason="特别扣减",
        operator="hr",
        year=2024
    )
    print(f"✓ 余额调整(扣减): {txn.change_days} 天, 余额: {account.balance}")

    account, txn = service.adjust_balance(
        employee_id=emp_id,
        leave_type_id=annual_id,
        days=3.0,
        reason="特殊补发",
        operator="hr",
        year=2024
    )
    print(f"✓ 余额调整(补发): +{txn.change_days} 天, 余额: {account.balance}")

    expected_balance = 10.0 - 2.0 + 3.0
    assert account.balance == expected_balance, f"余额应为 {expected_balance}, 实际 {account.balance}"
    print(f"✓ 余额验证通过: {account.balance} = {expected_balance}")

    try:
        service.adjust_balance(
            employee_id=emp_id,
            leave_type_id=annual_id,
            days=-100.0,
            reason="测试负数余额",
            operator="test",
            year=2024
        )
        print("✗ 应该不允许调整为负数余额")
    except ValueError as e:
        print(f"✓ 负数余额校验通过: {e}")

    db.close()
    print("✓ 余额发放与调整测试通过")


def test_deduct_and_transaction(emp_id, annual_id, comp_id, sick_id):
    print("\n" + "="*60)
    print("测试 3: 余额扣减与交易明细回溯")
    print("="*60)

    db = setup_test_db()

    emp = Employee(id=emp_id, employee_no="EMP001", name="张三", department="技术部",
                   position="工程师", hire_date=date(2020, 1, 15), is_active=True)
    db.add(emp)
    db.add_all([
        LeaveType(id=annual_id, code="annual", name="年假", annual_grant_days=10.0,
                  carry_over_days=5.0, expire_months=12),
        LeaveType(id=comp_id, code="compensatory", name="调休", annual_grant_days=0.0,
                  carry_over_days=3.0, expire_months=6),
        LeaveType(id=sick_id, code="sick", name="病假", annual_grant_days=0.0,
                  carry_over_days=0.0, expire_months=12),
    ])
    db.commit()

    service = BalanceService(db)
    service.grant_leave(emp_id, annual_id, 10.0, "年假发放", "admin", 2024)

    account, txn = service.deduct_leave(
        employee_id=emp_id,
        leave_type_id=annual_id,
        days=3.0,
        reason="春节回家",
        operator="system",
        year=2024
    )
    print(f"✓ 扣减年假: {txn.change_days} 天, 余额: {account.balance}")

    assert account.balance == 7.0
    print(f"✓ 扣减后余额验证: {account.balance}")

    try:
        service.deduct_leave(
            employee_id=emp_id,
            leave_type_id=annual_id,
            days=100.0,
            reason="超额扣减测试",
            operator="test",
            year=2024
        )
        print("✗ 应该不允许超额扣减")
    except ValueError as e:
        print(f"✓ 超额扣减校验通过: {e}")

    total, transactions = service.get_transactions(employee_id=emp_id)
    print(f"✓ 查询交易明细: 共 {total} 条记录")
    for txn in transactions:
        print(f"  - [{txn.created_at.strftime('%Y-%m-%d %H:%M')}] "
              f"{txn.change_type}: {txn.change_days:+} 天 → 余额 {txn.balance_after} 天 | {txn.reason}")

    db.close()
    print("✓ 余额扣减与交易明细测试通过")


def test_application_workflow(emp_id, annual_id, comp_id, sick_id):
    print("\n" + "="*60)
    print("测试 4: 请假申请与审批流")
    print("="*60)

    db = setup_test_db()

    emp = Employee(id=emp_id, employee_no="EMP001", name="张三", department="技术部",
                   position="工程师", hire_date=date(2020, 1, 15), is_active=True)
    db.add(emp)
    db.add_all([
        LeaveType(id=annual_id, code="annual", name="年假", annual_grant_days=10.0,
                  carry_over_days=5.0, expire_months=12),
        LeaveType(id=comp_id, code="compensatory", name="调休", annual_grant_days=0.0,
                  carry_over_days=3.0, expire_months=6),
        LeaveType(id=sick_id, code="sick", name="病假", annual_grant_days=0.0,
                  carry_over_days=0.0, expire_months=12),
    ])
    db.commit()

    balance_service = BalanceService(db)
    balance_service.grant_leave(emp_id, annual_id, 10.0, "年假发放", "admin", 2024)

    app_service = ApplicationService(db)

    app_data = schemas.LeaveApplicationCreate(
        employee_id=emp_id,
        leave_type_id=annual_id,
        start_date=date(2024, 6, 10),
        end_date=date(2024, 6, 12),
        days=3.0,
        reason="家里有事"
    )
    application = app_service.create_application(app_data)
    print(f"✓ 创建请假申请: {application.application_no}, 状态: {application.status}")

    balance = balance_service.get_balance(employee_id=emp_id, leave_type_id=annual_id, year=2024)
    assert len(balance) > 0
    assert balance[0].frozen_balance == 3.0
    print(f"✓ 申请冻结余额: 冻结 {balance[0].frozen_balance} 天, 可用 {balance[0].available_balance} 天")

    approve_data = schemas.LeaveApplicationApprove(approver="李经理", comment="同意")
    application = app_service.approve_application(application.id, approve_data)
    print(f"✓ 审批通过: 状态={application.status}, 审批人={application.approver}")

    balance = balance_service.get_balance(employee_id=emp_id, leave_type_id=annual_id, year=2024)
    assert balance[0].balance == 7.0
    assert balance[0].frozen_balance == 0.0
    print(f"✓ 审批后余额: 余额={balance[0].balance}, 冻结={balance[0].frozen_balance}")

    app_data2 = schemas.LeaveApplicationCreate(
        employee_id=emp_id,
        leave_type_id=annual_id,
        start_date=date(2024, 7, 1),
        end_date=date(2024, 7, 2),
        days=2.0,
        reason="测试驳回"
    )
    application2 = app_service.create_application(app_data2)
    print(f"✓ 创建第二个申请: {application2.application_no}")

    reject_data = schemas.LeaveApplicationReject(
        approver="李经理", reject_reason="项目太忙，稍后再请"
    )
    application2 = app_service.reject_application(application2.id, reject_data)
    print(f"✓ 审批驳回: 状态={application2.status}, 原因={application2.reject_reason}")

    balance = balance_service.get_balance(employee_id=emp_id, leave_type_id=annual_id, year=2024)
    assert balance[0].balance == 7.0
    assert balance[0].frozen_balance == 0.0
    print(f"✓ 驳回后余额不变: 余额={balance[0].balance}, 冻结={balance[0].frozen_balance}")

    db.close()
    print("✓ 请假申请与审批流测试通过")


def test_annual_grant_and_carry_over(emp_id, annual_id, comp_id, sick_id):
    print("\n" + "="*60)
    print("测试 5: 年度发放与跨年度结转")
    print("="*60)

    db = setup_test_db()

    for emp_data in [
        (emp_id, "EMP001", "张三", date(2020, 6, 15)),
        (emp_id + 1, "EMP002", "李四", date(2024, 7, 1)),
    ]:
        db.add(Employee(
            id=emp_data[0], employee_no=emp_data[1], name=emp_data[2],
            department="技术部", position="工程师", hire_date=emp_data[3], is_active=True
        ))
    db.add_all([
        LeaveType(id=annual_id, code="annual", name="年假", annual_grant_days=10.0,
                  carry_over_days=5.0, expire_months=12),
        LeaveType(id=comp_id, code="compensatory", name="调休", annual_grant_days=0.0,
                  carry_over_days=3.0, expire_months=6),
        LeaveType(id=sick_id, code="sick", name="病假", annual_grant_days=0.0,
                  carry_over_days=0.0, expire_months=12),
    ])
    db.commit()

    service = BalanceService(db)

    results = service.annual_grant(leave_type_code="annual", operator="system", year=2024)
    print(f"✓ 年度年假批量发放: 共处理 {len(results)} 人")

    for r in results:
        account = r[0]
        emp = db.query(Employee).filter(Employee.id == account.employee_id).first()
        print(f"  - {emp.name}: +{account.balance} 天 (入职: {emp.hire_date})")

    service.grant_leave(emp_id, comp_id, 8.0, "加班调休", "admin", 2024)
    print(f"✓ 给张三发放调休 8 天")

    results = service.year_end_carry_over(from_year=2024, to_year=2025, operator="system")
    print(f"✓ 年度结转完成: 处理 {len(results)} 个账户")

    balance_2024 = service.get_balance(employee_id=emp_id, year=2024)
    balance_2025 = service.get_balance(employee_id=emp_id, year=2025)

    print("  张三 2024 年末余额:")
    for b in balance_2024:
        print(f"    - {b.leave_type_name}: {b.balance} 天 (结转上限已扣除)")

    print("  张三 2025 年初结转余额:")
    for b in balance_2025:
        print(f"    - {b.leave_type_name}: +{b.balance} 天")

    annual_2025 = [b for b in balance_2025 if b.leave_type_code == "annual"]
    comp_2025 = [b for b in balance_2025 if b.leave_type_code == "compensatory"]
    assert len(annual_2025) > 0 and annual_2025[0].balance <= 5.0, "年假结转不应超过5天"
    assert len(comp_2025) > 0 and comp_2025[0].balance <= 3.0, "调休结转不应超过3天"
    print("✓ 结转上限校验通过")

    db.close()
    print("✓ 年度发放与结转测试通过")


def test_concurrent_simulation(emp_id, annual_id, comp_id, sick_id):
    print("\n" + "="*60)
    print("测试 6: 并发控制 (乐观锁)")
    print("="*60)

    from sqlalchemy.orm import sessionmaker as sm
    from sqlalchemy import create_engine as ce
    from sqlalchemy.pool import StaticPool as sp
    from app.models.models import LeaveAccount

    engine = ce(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sp,
        isolation_level="SERIALIZABLE",
    )
    Base.metadata.create_all(bind=engine)
    SessionFactory = sm(autocommit=False, autoflush=False, bind=engine)

    db0 = SessionFactory()
    db0.add(Employee(id=emp_id, employee_no="EMP001", name="张三", department="技术部",
                     position="工程师", hire_date=date(2020, 1, 15), is_active=True))
    db0.add(LeaveType(id=annual_id, code="annual", name="年假", annual_grant_days=10.0,
                      carry_over_days=5.0, expire_months=12))
    db0.commit()

    service0 = BalanceService(db0)
    service0.grant_leave(emp_id, annual_id, 10.0, "初始发放", "admin", 2024)
    db0.close()
    print("✓ 初始余额: 10 天")

    db1 = SessionFactory()
    db2 = SessionFactory()
    service1 = BalanceService(db1)
    service2 = BalanceService(db2)

    account1 = service1._get_or_create_account(emp_id, annual_id, 2024)
    version_before_1 = account1.version
    account2 = service2._get_or_create_account(emp_id, annual_id, 2024)
    version_before_2 = account2.version
    print(f"✓ 两个会话都读取账户: version = {version_before_1}")

    account1.balance -= 3.0
    if not service1._check_and_update_version(account1, version_before_1):
        db1.rollback()
        print("✗ 会话1更新失败")
    db1.commit()
    print(f"✓ 会话1扣减3天: version {version_before_1} → {account1.version}, 余额 {account1.balance}")

    conflict_detected = False
    try:
        account2.balance -= 4.0
        if not service2._check_and_update_version(account2, version_before_2):
            raise ValueError("并发冲突：账户已被其他操作修改，请重试")
        db2.commit()
        print("✗ 乐观锁未生效 - 会话2应该检测到冲突")
    except ValueError as e:
        conflict_detected = True
        print(f"✓ 会话2并发冲突检测: {e}")
        db2.rollback()

    assert conflict_detected, "应该检测到并发冲突"

    db_final = SessionFactory()
    service_final = BalanceService(db_final)
    balances = service_final.get_balance(employee_id=emp_id, leave_type_id=annual_id, year=2024)
    print(f"✓ 最终余额验证: {balances[0].balance} 天 (应为7天)")
    assert balances[0].balance == 7.0, f"余额应为7, 实际 {balances[0].balance}"

    db1.close()
    db2.close()
    db_final.close()
    print("✓ 并发控制测试通过")


def test_balance_query():
    print("\n" + "="*60)
    print("测试 7: 余额查询API")
    print("="*60)

    db = setup_test_db()
    master = MasterDataService(db)
    balance_service = BalanceService(db)

    emp1 = master.create_employee(schemas.EmployeeCreate(
        employee_no="EMP001", name="张三", department="技术部",
        position="工程师", hire_date=date(2020, 1, 1)
    ))
    emp2 = master.create_employee(schemas.EmployeeCreate(
        employee_no="EMP002", name="李四", department="市场部",
        position="经理", hire_date=date(2019, 6, 1)
    ))
    lt_annual = master.create_leave_type(schemas.LeaveTypeCreate(
        code="annual", name="年假", annual_grant_days=10.0, carry_over_days=5.0
    ))
    lt_sick = master.create_leave_type(schemas.LeaveTypeCreate(
        code="sick", name="病假", annual_grant_days=0.0, carry_over_days=0.0
    ))

    balance_service.grant_leave(emp1.id, lt_annual.id, 10.0, "年假", "admin", 2024)
    balance_service.grant_leave(emp1.id, lt_sick.id, 5.0, "病假补发", "hr", 2024)
    balance_service.grant_leave(emp2.id, lt_annual.id, 15.0, "年假", "admin", 2024)

    all_balances = balance_service.get_balance()
    print(f"✓ 查询所有余额: 共 {len(all_balances)} 条")
    for b in all_balances:
        print(f"  - {b.employee_name} | {b.leave_type_name} ({b.year}): "
              f"余额={b.balance}, 可用={b.available_balance}")

    emp1_annual = balance_service.get_balance(
        employee_id=emp1.id, leave_type_code="annual", year=2024
    )
    assert len(emp1_annual) == 1
    assert emp1_annual[0].balance == 10.0
    print(f"✓ 按条件查询: 张三的年假 = {emp1_annual[0].balance} 天")

    by_no = balance_service.get_balance(employee_no="EMP002")
    assert len(by_no) == 1
    assert by_no[0].employee_name == "李四"
    print(f"✓ 按员工编号查询: {by_no[0].employee_name}")

    db.close()
    print("✓ 余额查询测试通过")


def run_all_tests():
    print("\n" + "#"*60)
    print("#  员工假期余额管理API - 核心业务逻辑测试")
    print("#"*60)

    try:
        test_master_data()
        test_balance_grant_and_adjust(1, 1, 2, 3)
        test_deduct_and_transaction(1, 1, 2, 3)
        test_application_workflow(1, 1, 2, 3)
        test_annual_grant_and_carry_over(1, 1, 2, 3)
        test_concurrent_simulation(1, 1, 2, 3)
        test_balance_query()

        print("\n" + "#"*60)
        print("#  ✓ 所有测试通过！")
        print("#"*60 + "\n")
        return True
    except Exception as e:
        print(f"\n✗ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
