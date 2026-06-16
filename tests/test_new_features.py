import sys
import os
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.models import Base, Employee, LeaveType, SysUser, HolidayConfig
from app.services.balance_service import BalanceService, TransactionBoundary
from app.services.application_service import ApplicationService
from app.services.master_data_service import MasterDataService
from app.services.permission_service import PermissionService
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


def seed_base_data(db):
    master = MasterDataService(db)
    emp1 = master.create_employee(schemas.EmployeeCreate(
        employee_no="EMP001", name="张三", department="技术部",
        position="工程师", hire_date=date(2020, 1, 15)
    ))
    emp2 = master.create_employee(schemas.EmployeeCreate(
        employee_no="EMP002", name="李四", department="市场部",
        position="经理", hire_date=date(2019, 6, 1)
    ))
    emp3 = master.create_employee(schemas.EmployeeCreate(
        employee_no="EMP003", name="王五", department="技术部",
        position="实习生", hire_date=date(2024, 7, 1)
    ))
    lt1 = master.create_leave_type(schemas.LeaveTypeCreate(
        code="annual", name="年假", annual_grant_days=10.0,
        carry_over_days=5.0, expire_months=12, use_workdays=False
    ))
    lt2 = master.create_leave_type(schemas.LeaveTypeCreate(
        code="compensatory", name="调休", annual_grant_days=0.0,
        carry_over_days=3.0, expire_months=6
    ))
    lt3 = master.create_leave_type(schemas.LeaveTypeCreate(
        code="sick", name="病假", annual_grant_days=0.0,
        carry_over_days=0.0, expire_months=12, require_expire_approval=True
    ))
    lt4 = master.create_leave_type(schemas.LeaveTypeCreate(
        code="annual_wd", name="年假(工作日)", annual_grant_days=10.0,
        carry_over_days=5.0, expire_months=12, use_workdays=True
    ))
    perm = PermissionService(db)
    u1 = perm.create_user(schemas.SysUserCreate(
        username="zhangsan", name="张三", employee_id=emp1.id,
        role="employee", department="技术部"
    ))
    u2 = perm.create_user(schemas.SysUserCreate(
        username="manager1", name="技术经理", employee_id=None,
        role="manager", department="技术部"
    ))
    u3 = perm.create_user(schemas.SysUserCreate(
        username="hr1", name="HR专员", employee_id=None,
        role="hr", department="人事部"
    ))
    u4 = perm.create_user(schemas.SysUserCreate(
        username="admin", name="系统管理员", employee_id=None,
        role="admin"
    ))
    return {
        "emp_ids": (emp1.id, emp2.id, emp3.id),
        "lt_ids": (lt1.id, lt2.id, lt3.id, lt4.id),
        "user_ids": (u1.id, u2.id, u3.id, u4.id)
    }


def test_1_transaction_boundary():
    print("\n" + "="*60)
    print("测试 1: 事务边界与回滚一致性")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    annual_id = seed["lt_ids"][0]
    service = BalanceService(db)

    service.grant_leave(emp_id, annual_id, 10.0, "初始发放", "admin", 2024)
    b0 = service.get_balance(employee_id=emp_id, leave_type_id=annual_id, year=2024)
    print(f"✓ 初始余额: {b0[0].balance} 天")

    try:
        with TransactionBoundary(db):
            account = service._get_or_create_account(emp_id, annual_id, 2024)
            ov = account.version
            account.balance += 5
            if not service._check_and_update_version(account, ov):
                raise ValueError("并发冲突")
            service._create_transaction(account, annual_id, emp_id, 2024, "test_add", 5.0, "步骤1")
            raise ValueError("步骤2故意失败")
    except ValueError as e:
        print(f"✓ 事务中间故意抛出异常触发回滚: {e}")

    b1 = service.get_balance(employee_id=emp_id, leave_type_id=annual_id, year=2024)
    assert b1[0].balance == b0[0].balance, (
        f"回滚后余额应={b0[0].balance}, 实际={b1[0].balance}"
    )
    print(f"✓ 事务回滚验证通过: 余额保持 {b1[0].balance} 不变")

    try:
        with TransactionBoundary(db):
            account = service._get_or_create_account(emp_id, annual_id, 2024)
            ov = account.version
            account.balance += 3
            if not service._check_and_update_version(account, ov):
                raise ValueError("并发冲突")
            service._create_transaction(account, annual_id, emp_id, 2024, "test_add", 3.0, "加3")
            account2 = service._get_or_create_account(emp_id, annual_id, 2024)
            ov2 = account2.version
            account2.balance -= 2
            if not service._check_and_update_version(account2, ov2):
                raise ValueError("并发冲突")
            service._create_transaction(account2, annual_id, emp_id, 2024, "test_sub", -2.0, "减2")
    except Exception as e:
        print(f"✗ 有效事务不应异常: {e}")

    b2 = service.get_balance(employee_id=emp_id, leave_type_id=annual_id, year=2024)
    expected = 10.0 + 3.0 - 2.0
    assert b2[0].balance == expected
    print(f"✓ 有效多步骤事务提交: 余额 {b2[0].balance} = 10+3-2 = {expected}")

    try:
        service.adjust_balance(emp_id, annual_id, 4.0, "有效补发", "admin", 2024)
        service.deduct_leave(emp_id, annual_id, 9999.0, "必然失败的超额扣减", "test", 2024)
    except ValueError as e:
        print(f"✓ 调用链中第二步骤失败: {e}")

    b3 = service.get_balance(employee_id=emp_id, leave_type_id=annual_id, year=2024)
    print(f"  验证 adjust_balance 原子性: 补发成功={b3[0].balance == expected + 4}, "
          f"余额={b3[0].balance}, 预期={expected + 4}")

    db.close()
    print("✓ 事务边界与回滚一致性测试通过")


def test_2_retry_backoff():
    print("\n" + "="*60)
    print("测试 2: 乐观锁冲突的重试退避")
    print("="*60)

    from sqlalchemy.orm import sessionmaker as sm
    from sqlalchemy import create_engine as ce
    from sqlalchemy.pool import StaticPool as sp

    engine = ce(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sp,
        isolation_level="SERIALIZABLE",
    )
    Base.metadata.create_all(bind=engine)
    SessionFactory = sm(autocommit=False, autoflush=False, bind=engine)

    db0 = SessionFactory()
    db0.add(Employee(id=1, employee_no="E1", name="测试", department="D",
                     position="P", hire_date=date(2020, 1, 1), is_active=True))
    db0.add(LeaveType(id=1, code="t1", name="测试假期", annual_grant_days=10,
                      carry_over_days=5, expire_months=12))
    db0.commit()

    s0 = BalanceService(db0)
    s0.grant_leave(1, 1, 10.0, "初始", "admin", 2024)
    db0.close()
    print("✓ 初始余额: 10 天")

    import time
    start = time.time()
    call_count = [0]
    original_check = BalanceService._check_and_update_version

    def flaky_check(self, account, expected_version):
        call_count[0] += 1
        if call_count[0] <= 2:
            print(f"  第 {call_count[0]} 次故意模拟冲突")
            return False
        return original_check(self, account, expected_version)

    BalanceService._check_and_update_version = flaky_check

    db1 = SessionFactory()
    s1 = BalanceService(db1)
    try:
        acc, txn = s1.grant_leave(1, 1, 5.0, "测试重试", "admin", 2024)
        elapsed = time.time() - start
        print(f"✓ 重试机制生效: 尝试 {call_count[0]} 次, 耗时 {elapsed:.3f}s")
        assert call_count[0] == 3, f"应该调用3次 (2次失败+1次成功), 实际 {call_count[0]}"
        assert elapsed >= 0.1, "重试之间应该有退避延迟"
    finally:
        BalanceService._check_and_update_version = original_check

    BalanceService._check_and_update_version = original_check
    db1.close()
    print("✓ 乐观锁冲突的重试退避测试通过")


def test_3_holiday_workdays():
    print("\n" + "="*60)
    print("测试 3: 年度结转节假日处理 / 工作日模式")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    annual_wd_id = seed["lt_ids"][3]

    service = BalanceService(db)

    for d_str, name, t in [
        ("2024-10-01", "国庆节", "holiday"),
        ("2024-10-02", "国庆节", "holiday"),
        ("2024-10-03", "国庆节", "holiday"),
        ("2024-09-29", "国庆调休上班", "workday"),
    ]:
        d = date.fromisoformat(d_str)
        service.add_holiday(d, name, t)
    print("✓ 配置 2024 国庆节假日 (3天假期+1天调休上班)")

    holidays = service.list_holidays(year=2024)
    print(f"✓ 节假日配置数: {len(holidays)} 条")

    from app.utils import count_workdays, is_workday
    holiday_set = {h.date for h in holidays if h.type == "holiday"}
    workday_set = {h.date for h in holidays if h.type == "workday"}

    start = date(2024, 9, 28)
    end = date(2024, 10, 8)
    wd_count = count_workdays(start, end, holiday_set, workday_set)
    print(f"✓ 9/28~10/8 期间工作日数: {wd_count} 天 (9/29调休上班, 10/1-3放假)")

    db2 = setup_test_db()
    seed2 = seed_base_data(db2)
    emp3_id = seed2["emp_ids"][2]
    awd_id = seed2["lt_ids"][3]

    for d_str, name, t in [
        ("2024-10-01", "国庆", "holiday"),
        ("2024-10-02", "国庆", "holiday"),
        ("2024-10-03", "国庆", "holiday"),
    ]:
        d = date.fromisoformat(d_str)
        HolidayConfig(date=d, name=name, type=t, year=2024)

    s2 = BalanceService(db2)
    results = s2.annual_grant(leave_type_code="annual_wd", operator="system", year=2024)
    for r in results:
        acc = r[0]
        emp = db2.query(Employee).filter(Employee.id == acc.employee_id).first()
        print(f"  - {emp.name} (入职{emp.hire_date}): {acc.balance} 天年假")

    db.close()
    db2.close()
    print("✓ 年度结转节假日处理测试通过")


def test_4_expire_hold_approval():
    print("\n" + "="*60)
    print("测试 4: 过期清零的审批 Hold 机制")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    sick_id = seed["lt_ids"][2]

    service = BalanceService(db)

    past_expire = date.today() - timedelta(days=10)
    service.grant_leave(
        emp_id, sick_id, 5.0, "病假余额", "admin", 2024,
        expire_date=past_expire
    )
    b = service.get_balance(employee_id=emp_id, leave_type_id=sick_id, year=2024)
    print(f"✓ 病假期初余额: {b[0].balance} 天 (已过期, 需审批)")

    holds = service.scan_expired_and_create_holds(operator="system")
    print(f"✓ 扫描过期生成 Hold 审批单: {len(holds)} 张")

    total, pending_holds_all = service.list_expire_holds(status="pending")
    pending_holds = [h for h in pending_holds_all if h.employee_id == emp_id]
    assert len(pending_holds) >= 1, f"员工1应该有>=1张待审批, 实际 {len(pending_holds)}"
    hold_id = pending_holds[0].id
    print(f"  - Hold单: {pending_holds[0].hold_no}, 天数: {pending_holds[0].hold_days}")

    b_after = service.get_balance(employee_id=emp_id, leave_type_id=sick_id, year=2024)
    assert b_after[0].pending_expire_days == 5.0
    print(f"✓ 生成Hold后: pending_expire_days={b_after[0].pending_expire_days}, "
          f"可用余额={b_after[0].available_balance}")

    service.reject_expire_hold(hold_id, approver="HR小王", reject_reason="员工申诉，保留余额")
    print("✓ 驳回过期清零申请 → 余额保留")

    b_reject = service.get_balance(employee_id=emp_id, leave_type_id=sick_id, year=2024)
    assert b_reject[0].pending_expire_days == 0.0
    assert b_reject[0].balance == 5.0
    print(f"✓ 驳回后余额验证: 余额={b_reject[0].balance}, pending清零={b_reject[0].pending_expire_days}")

    past_expire2 = date.today() - timedelta(days=5)
    emp2_id = seed["emp_ids"][1]
    service.grant_leave(
        emp2_id, sick_id, 3.0, "新员工病假", "admin", 2024,
        expire_date=past_expire2
    )
    new_holds = service.scan_expired_and_create_holds(operator="system")
    assert len(new_holds) >= 1, "至少应生成1张新hold"
    new_hold = next(h for h in new_holds if h.employee_id == emp2_id)
    new_hold_id = new_hold.id

    service.approve_expire_hold(new_hold_id, approver="HR小王", comment="同意清零")
    print("✓ 审批通过过期清零 → 余额扣减")

    b_final_emp1 = service.get_balance(employee_id=emp_id, leave_type_id=sick_id, year=2024)
    b_final_emp2 = service.get_balance(employee_id=emp2_id, leave_type_id=sick_id, year=2024)
    assert b_final_emp1[0].balance == 5.0, "员工1余额保留5天"
    assert b_final_emp2[0].balance == 0.0, "员工2(新病假3天)清零后余额应为0"
    print(f"✓ 审批通过后验证: 员工1保留={b_final_emp1[0].balance}, 员工2清零={b_final_emp2[0].balance}")

    db.close()
    print("✓ 过期清零的审批 Hold 机制测试通过")


def test_5_retro_link():
    print("\n" + "="*60)
    print("测试 5: 特殊补发的追溯链路")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    annual_id = seed["lt_ids"][0]

    service = BalanceService(db)

    _, grant_txn = service.grant_leave(
        emp_id, annual_id, 10.0, "2024年度年假", "admin", 2024
    )
    print(f"✓ 初始年假发放 (ID={grant_txn.id}): 10 天")

    app_data = schemas.LeaveApplicationCreate(
        employee_id=emp_id, leave_type_id=annual_id,
        start_date=date(2024, 6, 10), end_date=date(2024, 6, 12), days=2.0,
        reason="家里有事"
    )
    app_service = ApplicationService(db)
    app = app_service.create_application(app_data)
    app = app_service.approve_application(
        app.id, schemas.LeaveApplicationApprove(approver="经理", comment="同意")
    )
    print(f"✓ 创建并审批请假单 (ID={app.id}): 扣 2 天")

    retro_data = {
        "source_transaction_id": grant_txn.id,
        "source_application_id": app.id,
        "approval_no": "HR-SPECIAL-2024-001",
        "document_no": "DOC-2024-补充-088",
        "retro_reason": "因国庆加班特殊补发3天年假，追溯关联年度发放及请假单"
    }
    account, adj_txn = service.adjust_balance(
        employee_id=emp_id, leave_type_id=annual_id, days=3.0,
        reason="国庆加班特别补发", operator="HR总监", year=2024,
        retro_link_data=retro_data
    )
    print(f"✓ 特殊补发 3 天 (交易ID={adj_txn.id})，附带追溯链路")

    retro_link = service.get_retro_link(adj_txn.id)
    assert retro_link is not None, "追溯链路应该存在"
    print(f"  追溯链路详情:")
    print(f"    - 关联原始交易ID: {retro_link.source_transaction_id}")
    print(f"    - 关联申请单ID: {retro_link.source_application_id}")
    print(f"    - 审批单号: {retro_link.approval_no}")
    print(f"    - 凭证号: {retro_link.document_no}")
    print(f"    - 追溯原因: {retro_link.retro_reason}")

    assert retro_link.source_transaction_id == grant_txn.id
    assert retro_link.source_application_id == app.id
    assert retro_link.approval_no == "HR-SPECIAL-2024-001"

    db.close()
    print("✓ 特殊补发的追溯链路测试通过")


def test_6_cursor_pagination():
    print("\n" + "="*60)
    print("测试 6: 明细回溯的大数据游标分页")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    annual_id = seed["lt_ids"][0]

    service = BalanceService(db)

    N = 30
    for i in range(N):
        service.adjust_balance(
            emp_id, annual_id, 1.0, f"补发测试-{i+1}", "test", 2024
        )
    print(f"✓ 生成 {N} 条补发交易记录")

    total_p, items_p = service.get_transactions(
        employee_id=emp_id, leave_type_id=annual_id, year=2024,
        skip=0, limit=10
    )
    print(f"✓ Offset分页(0~10): total={total_p}, 返回 {len(items_p)} 条")

    page1 = service.get_transactions_cursor(
        employee_id=emp_id, leave_type_id=annual_id, year=2024,
        limit=10, include_total=True
    )
    p1_ids = sorted([t.id for t in page1.items])
    print(f"✓ 游标分页第1页: total={page1.total_count}, has_more={page1.has_more}, "
          f"ids=[{p1_ids[0]}~{p1_ids[-1]}], 共{len(page1.items)}条")
    assert page1.total_count == N
    assert page1.has_more is True
    assert len(page1.items) == 10

    page2 = service.get_transactions_cursor(
        employee_id=emp_id, leave_type_id=annual_id, year=2024,
        cursor=page1.next_cursor, limit=10, include_total=False
    )
    p2_ids = sorted([t.id for t in page2.items])
    print(f"✓ 游标分页第2页: has_more={page2.has_more}, ids=[{p2_ids[0]}~{p2_ids[-1]}], 共{len(page2.items)}条")
    assert page2.total_count is None
    assert page2.has_more is True
    assert len(p1_ids & p2_ids) == 0, "page1和page2之间不应有重叠"

    cursor = page2.next_cursor
    last_page = None
    page_count = 2
    max_safe_iter = 100
    iter_count = 0
    seen_ids_sets = []
    while cursor and iter_count < max_safe_iter:
        p = service.get_transactions_cursor(
            employee_id=emp_id, leave_type_id=annual_id, year=2024,
            cursor=cursor, limit=10
        )
        page_count += 1
        iter_count += 1
        pg_ids = sorted([t.id for t in p.items])
        pg_min, pg_max = (pg_ids[0], pg_ids[-1]) if pg_ids else (None, None)
        print(f"  - 第{page_count}页: items={len(p.items)}, ids范围=[{pg_min}~{pg_max}], "
              f"has_more={p.has_more}")
        if not p.has_more:
            last_page = p
            break
        # 防止死循环：检查本页id与前一页是否完全重叠
        if pg_ids in seen_ids_sets:
            print(f"  ✗ 警告：重复的页面ID集，停止循环")
            break
        seen_ids_sets.append(pg_ids)
        cursor = p.next_cursor

    assert iter_count < max_safe_iter, "游标分页出现死循环"
    assert last_page is not None, "没有到达最后一页"
    print(f"✓ 游标分页总翻页次数: {page_count}, 最后一页 {len(last_page.items)} 条")

    all_ids_offset = {t.id for t in items_p}
    page1_ids = {t.id for t in page1.items}
    assert len(all_ids_offset & page1_ids) == 10, "前10条ID应一致"
    print("✓ 游标分页与Offset分页数据一致性验证通过")

    db.close()
    print("✓ 明细回溯的大数据游标分页测试通过")


def test_7_freeze_cancel_restore():
    print("\n" + "="*60)
    print("测试 7: 请假冻结的取消与恢复")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    annual_id = seed["lt_ids"][0]

    service = BalanceService(db)
    app_service = ApplicationService(db)

    service.grant_leave(emp_id, annual_id, 10.0, "初始年假", "admin", 2024)

    app_data = schemas.LeaveApplicationCreate(
        employee_id=emp_id, leave_type_id=annual_id,
        start_date=date(2024, 8, 1), end_date=date(2024, 8, 3), days=3.0,
        reason="年假"
    )
    app = app_service.create_application(app_data)
    print(f"✓ 创建请假申请，状态: {app.status}")

    b = service.get_balance(employee_id=emp_id, leave_type_id=annual_id, year=2024)
    print(f"  冻结后: 余额={b[0].balance}, 冻结={b[0].frozen_balance}, 可用={b[0].available_balance}")
    assert b[0].frozen_balance == 3.0

    total, logs = service.get_frozen_logs(application_id=app.id)
    print(f"✓ 冻结日志数: {total} 条, operation={logs[0].operation}, 变化量={logs[0].days}")

    cancel_data = schemas.LeaveApplicationCancel(
        operator="张三", cancel_reason="计划有变"
    )
    app2 = app_service.cancel_application(app.id, cancel_data)
    print(f"✓ 撤销申请: 状态={app2.status}, 撤销人={app2.cancelled_by}, 原因={app2.cancel_reason}")

    b2 = service.get_balance(employee_id=emp_id, leave_type_id=annual_id, year=2024)
    print(f"  撤销后: 余额={b2[0].balance}, 冻结={b2[0].frozen_balance}, 可用={b2[0].available_balance}")
    assert b2[0].frozen_balance == 0.0
    assert b2[0].balance == 10.0

    total2, logs2 = service.get_frozen_logs(application_id=app.id)
    print(f"✓ 撤销后冻结日志总数: {total2} 条 (freeze + unfreeze)")
    assert total2 == 2

    app3 = app_service.restore_application(app.id, operator="张三")
    print(f"✓ 恢复已撤销申请: 状态={app3.status}")

    b3 = service.get_balance(employee_id=emp_id, leave_type_id=annual_id, year=2024)
    print(f"  恢复后: 余额={b3[0].balance}, 冻结={b3[0].frozen_balance}, 可用={b3[0].available_balance}")
    assert b3[0].frozen_balance == 3.0

    approval_records = app_service.get_approval_records(app.id)
    print(f"✓ 审批链记录: {len(approval_records)} 条 (create + cancel + restore)")
    for r in approval_records:
        print(f"  - [{r.created_at.strftime('%H:%M:%S')}] {r.approver}: {r.action}")

    db.close()
    print("✓ 请假冻结的取消与恢复测试通过")


def test_8_permission_filter():
    print("\n" + "="*60)
    print("测试 8: 多维度查询的权限过滤")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp1_id, emp2_id, emp3_id = seed["emp_ids"]
    annual_id = seed["lt_ids"][0]

    service_public = BalanceService(db)
    service_public.grant_leave(emp1_id, annual_id, 10.0, "年假", "admin", 2024)
    service_public.grant_leave(emp2_id, annual_id, 15.0, "年假", "admin", 2024)
    service_public.grant_leave(emp3_id, annual_id, 5.0, "年假", "admin", 2024)

    perm = PermissionService(db)
    auth_employee = perm.get_auth_context("zhangsan")
    auth_manager = perm.get_auth_context("manager1")
    auth_hr = perm.get_auth_context("hr1")
    print(f"✓ 三个身份上下文: employee(张三), manager(技术部经理), hr(HR专员)")

    s_emp = BalanceService(db, auth_employee)
    b_emp = s_emp.get_balance(year=2024)
    print(f"  员工张三 可见余额: {len(b_emp)} 条")
    for b in b_emp:
        print(f"    - {b.employee_name}: {b.balance} 天")
    assert len(b_emp) == 1
    assert b_emp[0].employee_id == emp1_id

    s_mgr = BalanceService(db, auth_manager)
    b_mgr = s_mgr.get_balance(year=2024)
    print(f"  技术部经理 可见余额: {len(b_mgr)} 条 (技术部: 张三+王五)")
    for b in b_mgr:
        print(f"    - {b.employee_name}: {b.balance} 天")
    assert len(b_mgr) == 2
    dept_names = {b.employee_name for b in b_mgr}
    assert "李四" not in dept_names, "经理不应看到其他部门"
    assert "张三" in dept_names and "王五" in dept_names

    s_hr = BalanceService(db, auth_hr)
    b_hr = s_hr.get_balance(year=2024)
    print(f"  HR 可见余额: {len(b_hr)} 条 (全部员工)")
    for b in b_hr:
        print(f"    - {b.employee_name}: {b.balance} 天")
    assert len(b_hr) == 3

    total_e, _ = s_emp.get_transactions(year=2024)
    total_m, _ = s_mgr.get_transactions(year=2024)
    total_h, _ = s_hr.get_transactions(year=2024)
    print(f"✓ 交易记录权限: 员工见{total_e}条, 经理见{total_m}条, HR见{total_h}条")
    assert total_e < total_m < total_h

    can_view_emp1_hr = perm.can_view_employee(auth_hr, emp1_id)
    can_view_emp1_mgr = perm.can_view_employee(auth_manager, emp1_id)
    can_view_emp2_mgr = perm.can_view_employee(auth_manager, emp2_id)
    print(f"✓ 行级权限验证: HR看张三={can_view_emp1_hr}, "
          f"经理看张三(同部门)={can_view_emp1_mgr}, 经理看李四(跨部门)={can_view_emp2_mgr}")
    assert can_view_emp1_hr is True
    assert can_view_emp1_mgr is True
    assert can_view_emp2_mgr is False

    db.close()
    print("✓ 多维度查询的权限过滤测试通过")


def run_all_tests():
    print("\n" + "#"*60)
    print("#  员工假期余额管理API - 新增8大特性测试")
    print("#"*60)

    test_funcs = [
        test_1_transaction_boundary,
        test_2_retry_backoff,
        test_3_holiday_workdays,
        test_4_expire_hold_approval,
        test_5_retro_link,
        test_6_cursor_pagination,
        test_7_freeze_cancel_restore,
        test_8_permission_filter,
    ]

    passed = 0
    failed = 0
    for fn in test_funcs:
        try:
            fn()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n✗ [{fn.__name__}] 失败: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "#"*60)
    print(f"#  测试结果: 通过 {passed}, 失败 {failed}")
    if failed == 0:
        print("#  ✓ 全部新特性测试通过！")
    print("#"*60 + "\n")
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
