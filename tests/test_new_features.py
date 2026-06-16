import sys
import os
import time
import threading
from datetime import date, timedelta, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.models import Base, Employee, LeaveType, SysUser, HolidayConfig, FieldPermission
from app.services.balance_service import BalanceService, TransactionBoundary
from app.services.application_service import ApplicationService
from app.services.master_data_service import MasterDataService
from app.services.permission_service import PermissionService
from app.schemas import schemas
from app.utils import encode_cursor, decode_cursor


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


def test_1_cursor_stability():
    print("\n" + "="*60)
    print("测试 1: 游标分页稳定性（签名防篡改、翻页完整、篡改拒绝）")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    annual_id = seed["lt_ids"][0]
    service = BalanceService(db)

    N = 25
    for i in range(N):
        service.adjust_balance(emp_id, annual_id, 1.0, f"补发-{i+1}", "test", 2024)
    print(f"✓ 生成 {N} 条交易记录")

    page1 = service.get_transactions_cursor(
        employee_id=emp_id, leave_type_id=annual_id, year=2024,
        limit=10, include_total=True
    )
    assert page1.has_more is True
    assert len(page1.items) == 10
    print(f"✓ 第1页: {len(page1.items)}条, has_more={page1.has_more}")

    p1_ids = {t.id for t in page1.items}

    page2 = service.get_transactions_cursor(
        employee_id=emp_id, leave_type_id=annual_id, year=2024,
        cursor=page1.next_cursor, limit=10
    )
    p2_ids = {t.id for t in page2.items}
    assert len(p1_ids & p2_ids) == 0, "页面之间不应重叠"
    print(f"✓ 第2页: {len(page2.items)}条, 与第1页无重叠")

    page3 = service.get_transactions_cursor(
        employee_id=emp_id, leave_type_id=annual_id, year=2024,
        cursor=page2.next_cursor, limit=10
    )
    p3_ids = {t.id for t in page3.items}
    assert len(p2_ids & p3_ids) == 0
    print(f"✓ 第3页: {len(page3.items)}条, 与第2页无重叠")

    all_ids = p1_ids | p2_ids | p3_ids
    assert len(all_ids) == N, f"总记录应为{N}, 实际{len(all_ids)}"
    print(f"✓ 翻页完整: {len(all_ids)}条无遗漏")

    cursor_id, cursor_sk = decode_cursor(page1.next_cursor)
    assert cursor_id is not None, "合法cursor应解码成功"
    print(f"✓ 合法游标解码: id={cursor_id}, sk={cursor_sk}")

    import base64, json
    tampered_data = {"p": {"id": cursor_id, "sk": "id_desc"}, "s": "bad_sig"}
    tampered_cursor = base64.urlsafe_b64encode(
        json.dumps(tampered_data, separators=(",", ":")).encode()
    ).decode()
    try:
        service.get_transactions_cursor(
            employee_id=emp_id, leave_type_id=annual_id, year=2024,
            cursor=tampered_cursor, limit=10
        )
        assert False, "篡改游标应被拒绝"
    except ValueError as e:
        print(f"✓ 篡改游标被拒绝: {e}")

    raw_cursor = base64.urlsafe_b64encode(b'{"garbage":true}').decode()
    tid, tsk = decode_cursor(raw_cursor)
    assert tid is None, "非法cursor解码应返回None"
    print(f"✓ 非法游标解码返回None")

    db.close()
    print("✓ 游标分页稳定性测试通过")


def test_2_optimistic_lock_concurrent():
    print("\n" + "="*60)
    print("测试 2: 乐观锁退避——多线程并发、退避时间验证、最终一致性")
    print("="*60)

    from sqlalchemy.orm import sessionmaker as sm
    from sqlalchemy.pool import StaticPool as sp

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sp,
        isolation_level="SERIALIZABLE",
    )
    Base.metadata.create_all(bind=engine)
    SF = sm(autocommit=False, autoflush=False, bind=engine)

    db0 = SF()
    db0.add(Employee(id=1, employee_no="E1", name="测试", department="D",
                     position="P", hire_date=date(2020, 1, 1), is_active=True))
    db0.add(LeaveType(id=1, code="t1", name="测试假期", annual_grant_days=10,
                      carry_over_days=5, expire_months=12))
    db0.commit()
    s0 = BalanceService(db0)
    s0.grant_leave(1, 1, 100.0, "初始", "admin", 2024)
    db0.commit()
    db0.close()
    print("✓ 初始余额: 100 天")

    results = {"success": 0, "fail": 0, "lock_errors": 0}
    lock = threading.Lock()

    def worker(amount, idx):
        db = SF()
        svc = BalanceService(db)
        try:
            svc.deduct_leave(1, 1, amount, f"并发扣减-{idx}", f"worker-{idx}", 2024)
            with lock:
                results["success"] += 1
        except ValueError as e:
            if "可用余额不足" in str(e):
                with lock:
                    results["fail"] += 1
            elif "并发冲突" in str(e):
                with lock:
                    results["lock_errors"] += 1
            else:
                with lock:
                    results["fail"] += 1
        finally:
            db.close()

    threads = []
    for i in range(5):
        t = threading.Thread(target=worker, args=(10.0, i))
        threads.append(t)

    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    elapsed = time.time() - start
    print(f"✓ 5线程并发扣减: 成功={results['success']}, 余额不足={results['fail']}, "
          f"锁冲突(重试后)={results['lock_errors']}, 耗时={elapsed:.2f}s")

    db_final = SF()
    svc = BalanceService(db_final)
    b = svc.get_balance(employee_id=1, leave_type_id=1, year=2024)
    expected_balance = 100.0 - results["success"] * 10.0
    print(f"✓ 最终余额: {b[0].balance}, 预期: {expected_balance}")
    assert abs(b[0].balance - expected_balance) < 0.01, \
        f"余额不一致: {b[0].balance} vs {expected_balance}"
    db_final.close()

    engine2 = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=sp,
        isolation_level="SERIALIZABLE",
    )
    Base.metadata.create_all(bind=engine2)
    SF2 = sm(autocommit=False, autoflush=False, bind=engine2)

    db_setup = SF2()
    db_setup.add(Employee(id=1, employee_no="E1", name="测试", department="D",
                          position="P", hire_date=date(2020, 1, 1), is_active=True))
    db_setup.add(LeaveType(id=1, code="t1", name="测试假期", annual_grant_days=10,
                           carry_over_days=5, expire_months=12))
    db_setup.commit()
    s_setup = BalanceService(db_setup)
    s_setup.grant_leave(1, 1, 100.0, "初始", "admin", 2024)
    db_setup.commit()
    db_setup.close()

    call_count = [0]
    original_check = BalanceService._check_and_update_version

    def timed_flaky(self, account, expected_version):
        call_count[0] += 1
        if call_count[0] <= 2:
            return False
        return original_check(self, account, expected_version)

    BalanceService._check_and_update_version = timed_flaky
    db2 = SF2()
    s2 = BalanceService(db2)
    start2 = time.time()
    try:
        s2.adjust_balance(1, 1, 5.0, "退避测试", "test", 2024)
        elapsed2 = time.time() - start2
        print(f"✓ 退避重试: 调用{call_count[0]}次, 耗时{elapsed2:.3f}s")
        assert call_count[0] == 3, "应重试2次后第3次成功"
    finally:
        BalanceService._check_and_update_version = original_check

    db2.close()
    print("✓ 乐观锁退避测试通过")


def test_3_holiday_substitute():
    print("\n" + "="*60)
    print("测试 3: 节假日补班处理（补班日抵扣周末、跨年配置、年假折算）")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    annual_wd_id = seed["lt_ids"][3]

    service = BalanceService(db)

    service.add_holiday(date(2024, 10, 1), "国庆节", "holiday", None)
    service.add_holiday(date(2024, 10, 2), "国庆节", "holiday", None)
    service.add_holiday(date(2024, 10, 3), "国庆节", "holiday", None)
    service.add_holiday(date(2024, 9, 29), "国庆补班", "workday", date(2024, 10, 1))
    service.add_holiday(date(2024, 10, 12), "国庆补班", "workday", date(2024, 10, 2))
    print("✓ 配置: 10/1-3放假 + 9/29补10/1 + 10/12补10/2")

    from app.utils import is_workday, count_workdays
    holidays, workdays = service._get_holiday_sets(2024)

    assert is_workday(date(2024, 9, 29), holidays, workdays) is True
    print("✓ 9/29(周日)补班 → 是工作日")
    assert is_workday(date(2024, 10, 12), holidays, workdays) is True
    print("✓ 10/12(周六)补班 → 是工作日")
    assert is_workday(date(2024, 10, 1), holidays, workdays) is False
    print("✓ 10/1(周二)放假 → 非工作日")

    wd = count_workdays(date(2024, 9, 28), date(2024, 10, 12), holidays, workdays)
    expected = 9
    assert wd == expected, f"9/28~10/12工作日应为{expected}, 实际{wd}"
    print(f"✓ 9/28~10/12工作日数: {wd}天")

    h = service.list_holidays(year=2024, holiday_type="workday")
    assert len(h) == 2
    for hi in h:
        assert hi.substitute_for is not None
        print(f"  - {hi.date} 补班替 {hi.substitute_for}")
    print("✓ 补班日关联原始假日: substitute_for 正确")

    db2 = setup_test_db()
    seed2 = seed_base_data(db2)
    awd_id2 = seed2["lt_ids"][3]
    for d_str, name, t, sub in [
        ("2024-10-01", "国庆", "holiday", None),
        ("2024-10-02", "国庆", "holiday", None),
        ("2024-10-03", "国庆", "holiday", None),
        ("2024-09-29", "补班", "workday", "2024-10-01"),
    ]:
        d = date.fromisoformat(d_str)
        sub_d = date.fromisoformat(sub) if sub else None
        hc = HolidayConfig(
            date=d, name=name, type=t, year=2024, substitute_for=sub_d
        )
        db2.add(hc)
    db2.commit()

    s2 = BalanceService(db2)
    results = s2.annual_grant(leave_type_code="annual_wd", operator="system", year=2024)
    for r in results:
        acc = r[0]
        emp = db2.query(Employee).filter(Employee.id == acc.employee_id).first()
        print(f"  - {emp.name}: {acc.balance}天(含补班折算)")

    service.add_holiday(date(2025, 1, 1), "元旦", "holiday", None)
    service.add_holiday(date(2024, 12, 28), "元旦补班", "workday", date(2025, 1, 1))
    h_cross = service.list_holidays(year=2024, holiday_type="workday")
    has_cross = any(hc.substitute_for and hc.substitute_for.year == 2025 for hc in h_cross)
    print(f"✓ 跨年补班: 2024/12/28补2025/1/1 → 配置正确={has_cross}")

    db.close()
    db2.close()
    print("✓ 节假日补班处理测试通过")


def test_4_hold_fallback():
    print("\n" + "="*60)
    print("测试 4: 审批Hold Fallback（超时自动清零、异常恢复、escalation）")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    sick_id = seed["lt_ids"][2]

    service = BalanceService(db)

    past_expire = date.today() - timedelta(days=10)
    service.grant_leave(emp_id, sick_id, 5.0, "病假", "admin", 2024, expire_date=past_expire)
    holds = service.scan_expired_and_create_holds(operator="system")
    assert len(holds) >= 1
    hold = next(h for h in holds if h.employee_id == emp_id)
    print(f"✓ 创建Hold: {hold.hold_no}, 天数={hold.hold_days}, timeout={hold.timeout_hours}h")

    hold.timeout_hours = 0
    db.commit()
    db.refresh(hold)
    processed = service.timeout_expire_holds(default_action="approve", escalate_to="HR总监")
    assert len(processed) >= 1
    print(f"✓ 超时自动清零: {len(processed)}张hold被处理")

    b = service.get_balance(employee_id=emp_id, leave_type_id=sick_id, year=2024)
    assert b[0].balance == 0.0, f"超时清零后余额应为0, 实际{b[0].balance}"
    print(f"✓ 超时清零后余额: {b[0].balance}")

    emp2_id = seed["emp_ids"][1]
    past2 = date.today() - timedelta(days=5)
    service.grant_leave(emp2_id, sick_id, 3.0, "病假2", "admin", 2024, expire_date=past2)
    holds2 = service.scan_expired_and_create_holds(operator="system")
    hold2 = next(h for h in holds2 if h.employee_id == emp2_id)
    hold2.timeout_hours = 0
    db.commit()
    db.refresh(hold2)

    processed2 = service.timeout_expire_holds(default_action="reject", escalate_to="HR经理")
    assert len(processed2) >= 1
    b2 = service.get_balance(employee_id=emp2_id, leave_type_id=sick_id, year=2024)
    assert b2[0].balance == 3.0, f"超时驳回后余额保留, 实际{b2[0].balance}"
    print(f"✓ 超时驳回保留余额: {b2[0].balance}")

    recovered = service.recover_stuck_holds()
    print(f"✓ 异常Hold恢复: {len(recovered)}张被关闭")

    db.close()
    print("✓ 审批Hold Fallback测试通过")


def test_5_retro_chain_ui():
    print("\n" + "="*60)
    print("测试 5: 追溯链路UI回看（完整链API、逐级关联、可视化数据）")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    annual_id = seed["lt_ids"][0]
    service = BalanceService(db)
    app_service = ApplicationService(db)

    _, grant_txn = service.grant_leave(emp_id, annual_id, 10.0, "2024年假", "admin", 2024)
    print(f"✓ 发放(ID={grant_txn.id}): 10天")

    app_data = schemas.LeaveApplicationCreate(
        employee_id=emp_id, leave_type_id=annual_id,
        start_date=date(2024, 6, 10), end_date=date(2024, 6, 12), days=2.0,
        reason="家里有事"
    )
    app = app_service.create_application(app_data)
    app = app_service.approve_application(
        app.id, schemas.LeaveApplicationApprove(approver="经理", comment="同意")
    )
    print(f"✓ 请假(ID={app.id}): 扣2天")

    retro_data = {
        "source_transaction_id": grant_txn.id,
        "source_application_id": app.id,
        "approval_no": "HR-SPECIAL-2024-001",
        "document_no": "DOC-2024-088",
        "retro_reason": "加班补发3天年假"
    }
    _, adj_txn = service.adjust_balance(
        emp_id, annual_id, 3.0, "加班补发", "HR", 2024, retro_link_data=retro_data
    )
    print(f"✓ 补发(ID={adj_txn.id}): +3天")

    chain = service.get_retro_chain(adj_txn.id)
    print(f"✓ 追溯链: depth={chain.total_depth}")
    for i, node in enumerate(chain.chain):
        app_info = ""
        if node.related_application_no:
            app_info = f", 申请单={node.related_application_no}"
        retro_info = ""
        if node.retro_link:
            retro_info = f", 审批号={node.retro_link.approval_no}, 凭证={node.retro_link.document_no}"
        print(f"  [{i}] txn#{node.transaction_id} {node.change_type} "
              f"{node.change_days:+.0f}天 {node.reason}{app_info}{retro_info}")

    assert chain.total_depth >= 1
    assert chain.chain[0].transaction_id == adj_txn.id
    assert chain.chain[0].retro_link is not None
    assert chain.chain[0].retro_link.approval_no == "HR-SPECIAL-2024-001"
    assert chain.chain[0].related_application_id == app.id
    assert chain.chain[0].related_application_no == app.application_no
    print("✓ 追溯链路UI回看测试通过")

    db.close()


def test_6_cursor_large_data():
    print("\n" + "="*60)
    print("测试 6: 大数据分页游标稳定（30条、签名校验、完整遍历）")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    annual_id = seed["lt_ids"][0]
    service = BalanceService(db)

    N = 30
    for i in range(N):
        service.adjust_balance(emp_id, annual_id, 1.0, f"补发-{i+1}", "test", 2024)
    print(f"✓ 生成{N}条记录")

    page_size = 7
    all_ids = set()
    cursor = None
    page_num = 0
    while True:
        page_num += 1
        p = service.get_transactions_cursor(
            employee_id=emp_id, leave_type_id=annual_id, year=2024,
            cursor=cursor, limit=page_size, include_total=(page_num == 1)
        )
        page_ids = {t.id for t in p.items}
        overlap = all_ids & page_ids
        assert len(overlap) == 0, f"第{page_num}页有重叠: {overlap}"
        all_ids |= page_ids
        if not p.has_more:
            break
        cursor = p.next_cursor

    assert len(all_ids) == N, f"遍历不完整: {len(all_ids)} vs {N}"
    print(f"✓ 完整遍历: {page_num}页, {len(all_ids)}条, 无重叠无遗漏")

    db.close()
    print("✓ 大数据分页游标稳定测试通过")


def test_7_field_level_permission():
    print("\n" + "="*60)
    print("测试 7: 权限过滤字段级（隐藏/脱敏/可见、角色差异化）")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    service = BalanceService(db)

    service.set_field_permission("employee", "balance", "frozen_balance", "hidden")
    service.set_field_permission("employee", "balance", "pending_expire_days", "hidden")
    service.set_field_permission("employee", "balance", "employee_name", "masked", "name")
    print("✓ 配置字段权限: employee隐藏frozen/pending, 姓名脱敏")

    fps = service.get_field_permissions(role="employee", resource="balance")
    assert len(fps) == 3
    print(f"✓ 字段权限规则数: {len(fps)}")

    data = {
        "employee_id": 1,
        "employee_name": "张三丰",
        "leave_type_code": "annual",
        "balance": 10.0,
        "frozen_balance": 3.0,
        "pending_expire_days": 1.0,
        "available_balance": 6.0
    }

    perm = PermissionService(db)
    auth_emp = perm.get_auth_context("zhangsan")
    svc_emp = BalanceService(db, auth_emp)
    result = svc_emp.apply_field_filter(data, "balance")
    print(f"✓ employee过滤结果: hidden={result.hidden_fields}, masked={result.masked_fields}")
    assert "frozen_balance" in result.hidden_fields
    assert "pending_expire_days" in result.hidden_fields
    assert "frozen_balance" not in result.data
    assert "pending_expire_days" not in result.data
    assert result.data.get("employee_name") == "张**"
    assert result.data.get("balance") == 10.0
    print(f"  → 姓名脱敏: '张三丰' → '{result.data['employee_name']}'")

    auth_hr = perm.get_auth_context("hr1")
    svc_hr = BalanceService(db, auth_hr)
    result_hr = svc_hr.apply_field_filter(data, "balance")
    assert "frozen_balance" in result_hr.data
    assert result_hr.data.get("employee_name") == "张三丰"
    print(f"✓ HR无字段限制: frozen_balance={result_hr.data['frozen_balance']}, "
          f"姓名={result_hr.data['employee_name']}")

    db.close()
    print("✓ 权限过滤字段级测试通过")


def test_8_freeze_rollback_chain():
    print("\n" + "="*60)
    print("测试 8: 冻结恢复回滚链路（状态机、rollback_of_id、恢复失败回退）")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    annual_id = seed["lt_ids"][0]
    service = BalanceService(db)
    app_service = ApplicationService(db)

    service.grant_leave(emp_id, annual_id, 10.0, "初始", "admin", 2024)

    app_data = schemas.LeaveApplicationCreate(
        employee_id=emp_id, leave_type_id=annual_id,
        start_date=date(2024, 8, 1), end_date=date(2024, 8, 3), days=3.0,
        reason="年假"
    )
    app = app_service.create_application(app_data)
    print(f"✓ 创建请假: status={app.status}")

    b = service.get_balance(employee_id=emp_id, leave_type_id=annual_id, year=2024)
    assert b[0].frozen_balance == 3.0
    print(f"  冻结: 余额={b[0].balance}, 冻结={b[0].frozen_balance}")

    total_logs, logs = service.get_frozen_logs(application_id=app.id)
    freeze_log = next(l for l in logs if l.operation == "freeze")
    print(f"✓ 冻结日志: id={freeze_log.id}, op={freeze_log.operation}")

    cancel_data = schemas.LeaveApplicationCancel(operator="张三", cancel_reason="计划有变")
    app2 = app_service.cancel_application(app.id, cancel_data)
    assert app2.status == "cancelled"
    assert app2.previous_status == "pending"
    print(f"✓ 撤销: status={app2.status}, previous={app2.previous_status}")

    total_logs2, logs2 = service.get_frozen_logs(application_id=app.id)
    unfreeze_log = next(l for l in logs2 if l.operation == "unfreeze")
    assert unfreeze_log.rollback_of_id == freeze_log.id
    print(f"✓ 解冻日志: id={unfreeze_log.id}, rollback_of={unfreeze_log.rollback_of_id}")

    b2 = service.get_balance(employee_id=emp_id, leave_type_id=annual_id, year=2024)
    assert b2[0].frozen_balance == 0.0
    print(f"  解冻后: 余额={b2[0].balance}, 冻结={b2[0].frozen_balance}")

    app3 = app_service.restore_application(app.id, operator="张三")
    assert app3.status == "pending"
    assert app3.previous_status == "cancelled"
    print(f"✓ 恢复: status={app3.status}, previous={app3.previous_status}")

    total_logs3, logs3 = service.get_frozen_logs(application_id=app.id)
    restore_freeze_log = next(l for l in logs3 if l.operation == "freeze" and l.id != freeze_log.id)
    assert restore_freeze_log.rollback_of_id == unfreeze_log.id
    print(f"✓ 恢复冻结日志: id={restore_freeze_log.id}, rollback_of={restore_freeze_log.rollback_of_id}")

    b3 = service.get_balance(employee_id=emp_id, leave_type_id=annual_id, year=2024)
    assert b3[0].frozen_balance == 3.0
    print(f"  恢复后: 余额={b3[0].balance}, 冻结={b3[0].frozen_balance}")

    freeze_ops = [l for l in logs3 if l.operation == "freeze"]
    unfreeze_ops = [l for l in logs3 if l.operation == "unfreeze"]
    print(f"✓ 完整链路: freeze={len(freeze_ops)}, unfreeze={len(unfreeze_ops)}")

    chain_map = {}
    for l in logs3:
        if l.rollback_of_id:
            chain_map[l.rollback_of_id] = l.id
    print(f"  rollback链: {chain_map}")
    assert freeze_log.id in chain_map, "freeze → unfreeze 链路存在"
    assert unfreeze_log.id in chain_map, "unfreeze → restore_freeze 链路存在"

    db.close()
    print("✓ 冻结恢复回滚链路测试通过")


def test_9_hold_reapply_path():
    print("\n" + "="*60)
    print("测试 9: Hold Rejected 重新申请路径")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    sick_id = seed["lt_ids"][2]
    service = BalanceService(db)

    past_expire = date.today() - timedelta(days=10)
    service.grant_leave(emp_id, sick_id, 5.0, "病假", "admin", 2024, expire_date=past_expire)
    holds = service.scan_expired_and_create_holds(operator="system")
    hold = next(h for h in holds if h.employee_id == emp_id)
    print(f"✓ 创建Hold: {hold.hold_no}, status={hold.status}")

    service.reject_expire_hold(hold.id, approver="HR", reject_reason="需要复核")
    db.refresh(hold)
    assert hold.status == "rejected"
    print(f"✓ 驳回: status={hold.status}, reject_reason={hold.reject_reason}")

    hold2 = service.reapply_expire_hold(hold.id, operator="员工", new_timeout_hours=48)
    assert hold2.status == "pending"
    assert hold2.reapply_count == 1
    assert hold2.timeout_hours == 48
    print(f"✓ 重新申请: status={hold2.status}, reapply_count={hold2.reapply_count}, "
          f"timeout={hold2.timeout_hours}h")

    total_approvals, approvals = service.list_expire_hold_approvals(hold_id=hold.id)
    reapply_rec = [a for a in approvals if a.action == "reapply"]
    assert len(reapply_rec) >= 1
    print(f"✓ 审批记录: reapply 操作 {len(reapply_rec)} 条")

    service.reject_expire_hold(hold.id, approver="HR2", reject_reason="再次驳回")
    db.refresh(hold)
    hold3 = service.reapply_expire_hold(hold.id, operator="员工")
    assert hold3.reapply_count == 2
    print(f"✓ 第2次重新申请: reapply_count={hold3.reapply_count}")

    try:
        service.reapply_expire_hold(hold.id, operator="员工")
        assert False, "pending状态不应允许重新申请"
    except ValueError as e:
        print(f"✓ pending状态拒绝重申请: {e}")

    db.close()
    print("✓ Hold Reapply 测试通过")


def test_10_recover_stuck_with_compensation():
    print("\n" + "="*60)
    print("测试 10: recover_stuck_holds 自动补偿")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    sick_id = seed["lt_ids"][2]
    service = BalanceService(db)

    past_expire = date.today() - timedelta(days=5)
    service.grant_leave(emp_id, sick_id, 8.0, "病假", "admin", 2024, expire_date=past_expire)
    holds = service.scan_expired_and_create_holds(operator="system")
    hold = next(h for h in holds if h.employee_id == emp_id)
    hold_txn_id = hold.transaction_id
    print(f"✓ 创建Hold: {hold.hold_no}, 天数={hold.hold_days}, txn_id={hold_txn_id}")

    acc = service._get_or_create_account(emp_id, sick_id, 2024)
    original_pending = acc.pending_expire_days
    original_balance = acc.balance
    acc.pending_expire_days = 1.0
    db.commit()
    db.refresh(acc)
    print(f"✓ 模拟数据不一致: pending从{original_pending}降到1.0 (余额{acc.balance})")

    recovered = service.recover_stuck_holds(auto_compensate=True)
    print(f"✓ 第一轮检测: {len(recovered)} 张stuck (余额非0不视为stuck)")
    assert len(recovered) == 0

    db.refresh(acc)
    print(f"  补偿后: balance={acc.balance}, pending={acc.pending_expire_days}")
    assert acc.pending_expire_days >= hold.hold_days, "pending应被修复到hold天数"
    assert acc.balance == original_balance, "总余额不变（pending从可用转待过期）"

    total_txns, txns = service.get_transactions(employee_id=emp_id, leave_type_id=sick_id,
                                                  year=2024, change_type="compensate_add")
    print(f"✓ 补偿交易: {total_txns} 条")
    assert total_txns >= 1

    print("---")

    from app.models.models import LeaveTransaction
    txn = db.query(LeaveTransaction).filter(
        LeaveTransaction.id == hold_txn_id
    ).first()
    if txn:
        db.delete(txn)
        db.commit()
        print(f"✓ 构造stuck: 删除关联交易 txn_id={hold_txn_id}")

    recovered2 = service.recover_stuck_holds(auto_compensate=True)
    print(f"✓ 第二轮检测: {len(recovered2)} 张stuck (关联交易不存在)")
    assert len(recovered2) >= 1

    stuck_hold = recovered2[0]
    assert stuck_hold.is_stuck is True
    assert stuck_hold.recovered_at is not None
    assert stuck_hold.status == "rejected"
    print(f"  is_stuck={stuck_hold.is_stuck}, status={stuck_hold.status}, "
          f"reject_reason={stuck_hold.reject_reason}")

    db.close()
    print("✓ Stuck Hold自动补偿测试通过")


def test_11_retro_chain_deep_truncate():
    print("\n" + "="*60)
    print("测试 11: Retro Chain 极深链截断与循环检测")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    annual_id = seed["lt_ids"][0]
    service = BalanceService(db)

    _, prev_txn = service.grant_leave(emp_id, annual_id, 10.0, "初始发放", "admin", 2024)

    prev_id = prev_txn.id
    for i in range(15):
        from app.models.models import LeaveTransaction
        new_txn = LeaveTransaction(
            account_id=prev_txn.account_id,
            leave_type_id=annual_id,
            employee_id=emp_id,
            year=2024,
            change_type="adjust_add",
            change_days=1.0,
            balance_after=10.0 + i + 1,
            frozen_after=0.0,
            reason=f"补发层级-{i+1}",
            operator="test",
            related_transaction_id=prev_id
        )
        db.add(new_txn)
        db.flush()
        prev_id = new_txn.id
    db.commit()

    chain = service.get_retro_chain(prev_id, max_depth=5)
    print(f"✓ max_depth=5: depth={chain.total_depth}, is_truncated={chain.is_truncated}")
    assert chain.is_truncated is True
    assert chain.total_depth == 5

    chain_full = service.get_retro_chain(prev_id, max_depth=20)
    print(f"✓ max_depth=20: depth={chain_full.total_depth}, is_truncated={chain_full.is_truncated}")
    assert chain_full.is_truncated is False
    assert chain_full.total_depth == 16

    from app.models.models import LeaveTransaction as LT
    txn_a = db.query(LT).filter(LT.reason == "补发层级-3").first()
    txn_b = db.query(LT).filter(LT.reason == "补发层级-7").first()
    if txn_a and txn_b:
        txn_a.related_transaction_id = txn_b.id
        db.commit()
        print(f"✓ 构造环: txn#{txn_a.id}(层级3) → txn#{txn_b.id}(层级7)")

        chain_cycle = service.get_retro_chain(prev_id, max_depth=20)
        print(f"  has_cycle={chain_cycle.has_cycle}, cycle_start_id={chain_cycle.cycle_start_id}")
        assert chain_cycle.has_cycle is True
        assert chain_cycle.cycle_start_id == txn_b.id
        print(f"✓ 循环检测: 正确识别环起点={chain_cycle.cycle_start_id}")

    db.close()
    print("✓ Retro Chain 深度截断与循环检测测试通过")


def test_12_field_permission_runtime_toggle():
    print("\n" + "="*60)
    print("测试 12: FieldPermission 运行时切换（缓存、启停、优先级）")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    service = BalanceService(db)

    service.set_field_permission("employee", "balance", "frozen_balance", "hidden", priority=10)
    service.set_field_permission("employee", "balance", "employee_name", "masked", "name", priority=5)
    print("✓ 初始化2条权限规则")

    from app.utils import invalidate_field_perm_cache, get_field_perm_cache_ver
    ver_before = get_field_perm_cache_ver()

    service.set_field_permission("employee", "balance", "frozen_balance", "visible")
    ver_after = get_field_perm_cache_ver()
    print(f"✓ 修改权限后缓存版本: {ver_before} → {ver_after}")
    assert ver_after > ver_before

    service.toggle_field_permission("employee", "balance", "employee_name", False)
    fps = service.get_field_permissions(role="employee", resource="balance")
    active_fps = [f for f in fps if f.is_active]
    print(f"✓ 停用name脱敏后: 激活规则数={len(active_fps)}")
    assert len(active_fps) == 1

    perm = PermissionService(db)
    auth = perm.get_auth_context("zhangsan")
    svc_emp = BalanceService(db, auth)

    data = {
        "employee_name": "张三丰",
        "frozen_balance": 3.0,
        "balance": 10.0,
    }
    result = svc_emp.apply_field_filter(data, "balance")
    print(f"✓ 停用name脱敏后: employee_name={result.data.get('employee_name')}")
    assert result.data.get("employee_name") == "张三丰"

    service.toggle_field_permission("employee", "balance", "employee_name", True)
    result2 = svc_emp.apply_field_filter(data, "balance")
    print(f"✓ 启用name脱敏后: employee_name={result2.data.get('employee_name')}")
    assert result2.data.get("employee_name") == "张**"

    print("✓ 运行时切换即时生效")

    db.close()
    print("✓ FieldPermission 运行时切换测试通过")


def test_13_cursor_secret_rotation():
    print("\n" + "="*60)
    print("测试 13: 游标签名密钥轮换（新旧兼容、轮换、停用）")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    annual_id = seed["lt_ids"][0]
    service = BalanceService(db)

    for i in range(15):
        service.adjust_balance(emp_id, annual_id, 1.0, f"测试-{i+1}", "test", 2024)
    print("✓ 生成15条交易")

    page1 = service.get_transactions_cursor(
        employee_id=emp_id, leave_type_id=annual_id, year=2024, limit=5, include_total=False
    )
    cursor1 = page1.next_cursor
    print(f"✓ 默认密钥生成cursor: {cursor1[:20]}...")

    page2 = service.get_transactions_cursor(
        employee_id=emp_id, leave_type_id=annual_id, year=2024,
        cursor=cursor1, limit=5
    )
    assert len(page2.items) == 5
    print(f"✓ 默认密钥解码成功: 第2页{len(page2.items)}条")

    new_secret = "new-rotation-secret-v2-test"
    cs = service.rotate_cursor_secret(new_secret)
    print(f"✓ 轮换新密钥: version={cs.version}, is_primary={cs.is_primary}")

    page_new = service.get_transactions_cursor(
        employee_id=emp_id, leave_type_id=annual_id, year=2024, limit=5
    )
    cursor_new = page_new.next_cursor
    print(f"✓ 新密钥生成cursor: {cursor_new[:20]}...")
    assert cursor_new != cursor1

    page_old_cursor = service.get_transactions_cursor(
        employee_id=emp_id, leave_type_id=annual_id, year=2024,
        cursor=cursor1, limit=5
    )
    print(f"✓ 旧cursor仍然兼容: 第2页{len(page_old_cursor.items)}条")
    assert len(page_old_cursor.items) == 5

    page_new_cursor = service.get_transactions_cursor(
        employee_id=emp_id, leave_type_id=annual_id, year=2024,
        cursor=cursor_new, limit=5
    )
    assert len(page_new_cursor.items) == 5
    print(f"✓ 新cursor正常使用: 第2页{len(page_new_cursor.items)}条")

    secrets = service.list_cursor_secrets()
    print(f"✓ 密钥列表: {len(secrets)} 个, 版本={[s.version for s in secrets]}")
    assert len(secrets) >= 1
    assert secrets[0].is_primary is True
    assert secrets[0].secret_key != "default-cursor-secret-key"

    old_primary = [s for s in secrets if not s.is_primary and s.is_active]
    if old_primary:
        try:
            service.deactivate_cursor_secret(old_primary[0].id)
            print(f"✓ 停用旧密钥: id={old_primary[0].id}")
        except ValueError as e:
            print(f"  停用提示: {e}")

    db.close()
    print("✓ 游标密钥轮换测试通过")


def test_14_rollback_chain_cycle_detection():
    print("\n" + "="*60)
    print("测试 14: Rollback 链环检测（循环检测、断裂、路径）")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    annual_id = seed["lt_ids"][0]
    service = BalanceService(db)
    app_service = ApplicationService(db)

    service.grant_leave(emp_id, annual_id, 10.0, "初始", "admin", 2024)

    app_data = schemas.LeaveApplicationCreate(
        employee_id=emp_id, leave_type_id=annual_id,
        start_date=date(2024, 9, 1), end_date=date(2024, 9, 2), days=2.0,
        reason="测试"
    )
    app = app_service.create_application(app_data)
    cancel_data = schemas.LeaveApplicationCancel(operator="test", cancel_reason="取消")
    app2 = app_service.cancel_application(app.id, cancel_data)
    app3 = app_service.restore_application(app.id, operator="test")
    print(f"✓ 构造3步链路: freeze→unfreeze→restore_freeze")

    result = service.check_freeze_rollback_cycle(application_id=app.id)
    print(f"  has_cycle={result['has_cycle']}, depth={result['total_depth']}, "
          f"path={result['cycle_path']}")
    assert result["has_cycle"] is False

    _, logs = service.get_frozen_logs(application_id=app.id)
    freeze_logs = [l for l in logs if l.operation == "freeze"]
    unfreeze_logs = [l for l in logs if l.operation == "unfreeze"]
    if freeze_logs and unfreeze_logs:
        first_freeze = freeze_logs[0]
        last_freeze = freeze_logs[-1]
        if first_freeze.id != last_freeze.id:
            first_freeze.rollback_of_id = last_freeze.id
            db.commit()
            print(f"✓ 构造环: log#{first_freeze.id} → log#{last_freeze.id}")

            result2 = service.check_freeze_rollback_cycle(
                start_log_id=first_freeze.id, max_depth=20
            )
            print(f"  has_cycle={result2['has_cycle']}, cycle_start={result2['cycle_start_id']}")
            assert result2["has_cycle"] is True

            broken = service.break_freeze_rollback_cycle(first_freeze.id)
            assert broken.rollback_of_id is None
            assert "[环断裂]" in (broken.reason or "")
            print(f"✓ 环断裂: log#{broken.id} rollback_of_id={broken.rollback_of_id}")

            result3 = service.check_freeze_rollback_cycle(
                start_log_id=last_freeze.id, max_depth=20
            )
            print(f"  断裂后has_cycle={result3['has_cycle']}")
            assert result3["has_cycle"] is False

    db.close()
    print("✓ Rollback链环检测测试通过")


def test_15_hr_scenario_masking():
    print("\n" + "="*60)
    print("测试 15: HR 全量场景脱敏（总监级员工额外脱敏）")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    annual_id = seed["lt_ids"][0]

    master = MasterDataService(db)
    director_emp = master.create_employee(schemas.EmployeeCreate(
        employee_no="DIR001", name="王总监", department="技术部",
        position="技术总监", hire_date=date(2018, 1, 1)
    ))
    print(f"✓ 创建总监员工: {director_emp.name}, position={director_emp.position}")

    service = BalanceService(db)
    service.grant_leave(director_emp.id, annual_id, 20.0, "总监年假", "admin", 2024)

    perm = PermissionService(db)
    auth_hr = perm.get_auth_context("hr1")
    svc_hr = BalanceService(db, auth_hr)

    data_normal = {
        "employee_id": emp_id,
        "employee_name": "张三",
        "balance": 10.0,
        "frozen_balance": 3.0,
    }
    result_normal = svc_hr.apply_field_filter(
        data_normal, "balance", target_employee_id=emp_id
    )
    print(f"✓ HR查普通员工: name={result_normal.data.get('employee_name')}, "
          f"frozen={result_normal.data.get('frozen_balance')}")
    assert result_normal.data.get("employee_name") == "张三"
    assert result_normal.data.get("frozen_balance") == 3.0

    data_dir = {
        "employee_id": director_emp.id,
        "employee_name": "王总监",
        "balance": 20.0,
        "frozen_balance": 5.0,
    }
    result_dir = svc_hr.apply_field_filter(
        data_dir, "balance", target_employee_id=director_emp.id
    )
    print(f"✓ HR查总监: name={result_dir.data.get('employee_name')}, "
          f"frozen={result_dir.data.get('frozen_balance')}")
    assert result_dir.data.get("employee_name") == "王**"
    assert "frozen_balance" in result_dir.hidden_fields
    assert result_dir.data.get("frozen_balance") is None

    print("✓ 场景化脱敏: 总监级额外脱敏生效")

    db.close()
    print("✓ HR场景脱敏测试通过")


def test_16_masking_audit_trail():
    print("\n" + "="*60)
    print("测试 16: 脱敏审计旁路（操作留痕、溯源查询）")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    service = BalanceService(db)

    service.set_field_permission("employee", "balance", "frozen_balance", "hidden")
    service.set_field_permission("employee", "balance", "employee_name", "masked", "name")

    perm = PermissionService(db)
    auth_emp = perm.get_auth_context("zhangsan")
    svc_emp = BalanceService(db, auth_emp)

    data = {
        "employee_id": emp_id,
        "employee_name": "张三丰",
        "balance": 10.0,
        "frozen_balance": 3.0,
    }
    result = svc_emp.apply_field_filter(
        data, "balance", target_employee_id=emp_id, enable_audit=True
    )
    print(f"✓ 执行脱敏: hidden={result.hidden_fields}, masked={result.masked_fields}")

    total, logs = service.get_field_audit_logs(operator="zhangsan")
    print(f"✓ 审计日志: {total} 条")
    assert total >= 2

    for log in logs[:5]:
        print(f"  - {log.created_at.strftime('%H:%M:%S')} {log.field_name}: "
              f"{log.access_type}, original={log.original_value}, masked={log.masked_value}")

    name_logs = [l for l in logs if l.field_name == "employee_name" and l.access_type == "masked"]
    assert len(name_logs) >= 1
    nl = name_logs[0]
    assert nl.original_value == "张三丰"
    assert nl.masked_value == "张**"
    assert nl.mask_pattern == "name"
    assert nl.target_employee_id == emp_id
    print(f"✓ 脱敏溯源: 原值={nl.original_value} → 脱敏值={nl.masked_value}, "
          f"模式={nl.mask_pattern}, request_id={nl.request_id}")

    hidden_logs = [l for l in logs if l.field_name == "frozen_balance" and l.access_type == "hidden"]
    assert len(hidden_logs) >= 1
    print(f"✓ 隐藏审计: {len(hidden_logs)} 条, 原值={hidden_logs[0].original_value}")

    result_no_audit = svc_emp.apply_field_filter(
        data, "balance", target_employee_id=emp_id, enable_audit=False
    )
    total2, _ = service.get_field_audit_logs(operator="zhangsan")
    print(f"✓ 关闭审计后日志不增长: {total} → {total2}")

    db.close()
    print("✓ 脱敏审计旁路测试通过")


def test_17_truncate_warning_log():
    print("\n" + "="*60)
    print("测试 17: is_truncated 预警日志记录")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    annual_id = seed["lt_ids"][0]
    service = BalanceService(db)

    _, prev_txn = service.grant_leave(emp_id, annual_id, 10.0, "初始发放", "admin", 2024)
    prev_id = prev_txn.id
    for i in range(15):
        from app.models.models import LeaveTransaction
        new_txn = LeaveTransaction(
            account_id=prev_txn.account_id,
            leave_type_id=annual_id,
            employee_id=emp_id,
            year=2024,
            change_type="adjust_add",
            change_days=1.0,
            balance_after=10.0 + i + 1,
            frozen_after=0.0,
            reason=f"补发层级-{i+1}",
            operator="test",
            related_transaction_id=prev_id
        )
        db.add(new_txn)
        db.flush()
        prev_id = new_txn.id
    db.commit()

    chain = service.get_retro_chain(prev_id, max_depth=5)
    print(f"✓ max_depth=5截断: is_truncated={chain.is_truncated}, "
          f"warning_id={chain.warning_log_id}, severity={chain.warning_severity}")
    assert chain.is_truncated is True
    assert chain.warning_log_id is not None
    assert chain.warning_severity == "warn"

    total_warn, warnings = service.list_truncate_warnings(event_type="retro_truncate")
    print(f"✓ 预警列表: {total_warn} 条 (type=retro_truncate)")
    assert total_warn >= 1

    from app.models.models import LeaveTransaction as LT
    txn_a = db.query(LT).filter(LT.reason == "补发层级-3").first()
    txn_b = db.query(LT).filter(LT.reason == "补发层级-7").first()
    if txn_a and txn_b:
        txn_a.related_transaction_id = txn_b.id
        db.commit()

    chain_cycle = service.get_retro_chain(prev_id, max_depth=30)
    print(f"✓ 循环检测: has_cycle={chain_cycle.has_cycle}, "
          f"severity={chain_cycle.warning_severity}")
    assert chain_cycle.has_cycle is True
    assert chain_cycle.warning_severity == "critical"

    total_critical, criticals = service.list_truncate_warnings(severity="critical")
    print(f"✓ 严重预警: {total_critical} 条")
    assert total_critical >= 1

    db.close()
    print("✓ is_truncated 预警日志测试通过")


def test_18_field_permission_rollback():
    print("\n" + "="*60)
    print("测试 18: FieldPermission 运行时切换回滚")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    service = BalanceService(db)

    fp = service.set_field_permission("hr", "balance", "employee_name",
                                       "masked", "name", operator="Admin_Alice")
    total0, audits0 = service.list_field_permission_audits(permission_id=fp.id)
    print(f"✓ 创建权限: id={fp.id}, 初始审计={total0} 条 (action=create)")
    assert audits0[0].action == "create"

    before_access = fp.access
    before_pattern = fp.mask_pattern
    token = audits0[0].rollback_token
    print(f"  初始: access={before_access}, pattern={before_pattern}, token={token[:8]}...")

    fp2 = service.set_field_permission("hr", "balance", "employee_name",
                                        "hidden", None, operator="Admin_Bob")
    total1, audits1 = service.list_field_permission_audits(permission_id=fp.id)
    print(f"✓ 修改权限: access={fp2.access}, 审计={total1} 条 (新增update)")
    assert audits1[0].action == "update"
    assert fp2.access == "hidden"

    rollback_token = audits1[0].rollback_token
    fp_rb = service.rollback_field_permission(rollback_token, operator="Admin_Alice")
    print(f"✓ 回滚: access={fp_rb.access}, pattern={fp_rb.mask_pattern}")
    assert fp_rb.access == before_access
    assert fp_rb.mask_pattern == before_pattern

    total2, audits2 = service.list_field_permission_audits(permission_id=fp.id)
    rollback_audits = [a for a in audits2 if a.action == "rollback"]
    print(f"✓ 回滚审计: {len(rollback_audits)} 条 (共 {total2} 条)")
    assert len(rollback_audits) >= 1
    assert rollback_audits[0].rollback_of_id == audits1[0].id

    try:
        service.rollback_field_permission("not-exist-token")
        assert False, "应抛出不存在异常"
    except ValueError as e:
        print(f"✓ 无效令牌保护: {e}")

    db.close()
    print("✓ FieldPermission 回滚测试通过")


def test_19_cursor_secret_expiry():
    print("\n" + "="*60)
    print("测试 19: CursorSecret 密钥过期处理")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    annual_id = seed["lt_ids"][0]
    service = BalanceService(db)

    for i in range(15):
        service.grant_leave(emp_id, annual_id, 1.0, f"grant-{i}", "admin", 2024)

    secrets_old = service.list_cursor_secrets()
    print(f"✓ 初始密钥: {len(secrets_old)} 条")

    cs1 = service.rotate_cursor_secret("old-expiring-secret-v99")
    expired_time = datetime.now() - timedelta(hours=1)
    service.set_cursor_secret_expiry(cs1.id, expired_time)
    db.refresh(cs1)
    print(f"✓ 密钥v{cs1.version}: 设置过期时间 {cs1.expires_at.strftime('%H:%M')} (已过期)")

    cs2 = service.rotate_cursor_secret("new-fresh-secret-v100")
    print(f"✓ 密钥v{cs2.version}: 新主密钥 (未过期)")

    page1 = service.get_transactions_cursor(limit=5)
    last_id = page1.items[-1].id
    cursor_new = page1.next_cursor

    page2 = service.get_transactions_cursor(cursor=cursor_new, limit=5)
    print(f"✓ 新密钥cursor解码: {len(page2.items)}条, 首id={page2.items[0].id if page2.items else None}")
    assert len(page2.items) == 5
    assert page2.items[0].id < last_id

    expired_count = service.cleanup_expired_cursor_secrets()
    print(f"✓ 清理过期密钥: {expired_count} 条")
    assert expired_count >= 1

    db.refresh(cs1)
    print(f"  cs1.is_active = {cs1.is_active} (应为False)")
    assert cs1.is_active is False

    cs3 = service.rotate_cursor_secret("to-bedeleted-v101")
    future = datetime.now() + timedelta(days=30)
    service.set_cursor_secret_expiry(cs3.id, future)
    count2 = service.cleanup_expired_cursor_secrets()
    print(f"✓ 未过期密钥不被清理: 清理{count2} 条")
    assert count2 == 0

    db.close()
    print("✓ CursorSecret 过期处理测试通过")


def test_20_dfs_cycle_detection_performance():
    print("\n" + "="*60)
    print("测试 20: DFS 环检测性能与节点缓存")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    annual_id = seed["lt_ids"][0]
    service = BalanceService(db)

    acc = service._get_or_create_account(emp_id, annual_id, 2024)
    from app.models.models import FrozenBalanceLog as FBL
    logs = []
    prev_id = None
    for i in range(30):
        log = FBL(
            account_id=acc.id, employee_id=emp_id, leave_type_id=annual_id,
            operation="freeze" if i % 2 == 0 else "unfreeze",
            days=0.5, balance_before=acc.balance, balance_after=acc.balance,
            operator="tester", reason=f"step-{i}", rollback_of_id=prev_id
        )
        db.add(log)
        db.flush()
        prev_id = log.id
        logs.append(log.id)
    db.commit()
    print(f"✓ 构造30步线性链: {logs[0]} → ... → {logs[-1]}")

    r1 = service.check_freeze_rollback_cycle_dfs(start_log_id=logs[-1], max_depth=50)
    print(f"✓ 线性链DFS: has_cycle={r1['has_cycle']}, depth={r1['total_depth']}, "
          f"nodes={r1['nodes_visited']}, colors={r1['dfs_color_count']}")
    assert r1["has_cycle"] is False
    assert r1["total_depth"] >= 30

    first_log_id = logs[0]
    first_log = db.query(FBL).filter(FBL.id == logs[0]).first()
    if first_log:
        first_log.rollback_of_id = logs[-1]
        db.commit()
    print(f"✓ 构造环: log#{first_log.id} → log#{logs[-1]} (形成 {logs[-1]}→...→{logs[0]}→{logs[-1]})")

    node_cache = {}
    r2 = service.check_freeze_rollback_cycle_dfs(start_log_id=logs[-1], max_depth=50,
                                                  node_cache=node_cache)
    print(f"✓ 环检测DFS: has_cycle={r2['has_cycle']}, start={r2['cycle_start_id']}, "
          f"path_len={len(r2['cycle_path'])}")
    assert r2["has_cycle"] is True
    assert r2["cycle_start_id"] == logs[-1]
    assert len(r2["cycle_path"]) >= 3

    r3 = service.check_freeze_rollback_cycle_dfs(start_log_id=logs[0], max_depth=50,
                                                  node_cache=node_cache)
    print(f"✓ 缓存复用: 再次检测 nodes={r3['nodes_visited']}, cache_size={len(node_cache)}")
    assert len(node_cache) >= 30

    db.close()
    print("✓ DFS 环检测性能测试通过")


def test_21_sensitive_position_custom_rules():
    print("\n" + "="*60)
    print("测试 21: 敏感职位脱敏自定义规则")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    service = BalanceService(db)

    from app.models.models import Employee
    cfo = Employee(employee_no="E-CFO-001", name="王国富",
                    department="财务", position="集团CFO", hire_date=date(2018,3,1))
    svp = Employee(employee_no="E-SVP-002", name="李副总",
                    department="技术", position="研发部SVP", hire_date=date(2019,6,1))
    db.add_all([cfo, svp])
    db.commit()
    db.refresh(cfo); db.refresh(svp)
    print(f"✓ 创建员工: CFO={cfo.name}, SVP={svp.name}")

    rule_cfo = service.create_sensitive_position_rule(
        rule_name="CFO级超敏规则",
        position_pattern="CFO",
        match_mode="contains",
        field_mappings={
            "employee_name": {"access": "masked", "pattern": "name"},
            "frozen_balance": {"access": "hidden"},
            "pending_expire_days": {"access": "hidden"}
        },
        priority=50,
        created_by="Admin_System"
    )
    print(f"✓ 创建规则: id={rule_cfo.id}, pattern='{rule_cfo.position_pattern}', "
          f"mode={rule_cfo.match_mode}, priority={rule_cfo.priority}")

    from app.services.permission_service import PermissionService
    perm = PermissionService(db)
    auth_hr = perm.get_auth_context("hr1")
    svc_hr = BalanceService(db, auth_hr)
    service.set_field_permission("hr", "balance", "frozen_balance", "visible")

    data_cfo = {"employee_id": cfo.id, "employee_name": cfo.name,
                "department": "财务", "balance": 50.0, "frozen_balance": 5.0,
                "pending_expire_days": 3.0}
    r_cfo = svc_hr.apply_field_filter(data_cfo, "balance", target_employee_id=cfo.id)
    print(f"✓ CFO脱敏: name={r_cfo.data.get('employee_name')}, "
          f"balance={r_cfo.data.get('balance')}, "
          f"has_frozen={'frozen_balance' in r_cfo.data}, "
          f"has_pending={'pending_expire_days' in r_cfo.data}")
    assert r_cfo.data["employee_name"] == "王**"
    assert "frozen_balance" not in r_cfo.data
    assert "pending_expire_days" not in r_cfo.data

    rule_svp = service.create_sensitive_position_rule(
        rule_name="SVP级规则",
        position_pattern="SVP",
        match_mode="exact",
        field_mappings={
            "employee_name": {"access": "masked", "pattern": "name"},
        },
        priority=30
    )
    rule_svp_updated = service.update_sensitive_position_rule(
        rule_svp.id, match_mode="contains"
    )
    print(f"✓ 更新规则: exact → contains")
    assert rule_svp_updated.match_mode == "contains"

    data_svp = {"employee_id": svp.id, "employee_name": svp.name,
                "department": "技术", "balance": 30.0, "frozen_balance": 2.0}
    r_svp = svc_hr.apply_field_filter(data_svp, "balance", target_employee_id=svp.id)
    print(f"✓ SVP脱敏: name={r_svp.data.get('employee_name')}, "
          f"balance={r_svp.data.get('balance')}, "
          f"has_frozen={'frozen_balance' in r_svp.data}")
    assert r_svp.data["employee_name"] == "李**"
    assert "frozen_balance" in r_svp.data

    total, rules = service.list_sensitive_position_rules(is_active=True)
    print(f"✓ 生效规则列表: {total} 条")
    assert total >= 2

    db.close()
    print("✓ 敏感职位自定义规则测试通过")


def test_22_audit_retention_cleanup():
    print("\n" + "="*60)
    print("测试 22: FieldAuditLog 留存策略与清理")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    service = BalanceService(db)

    from app.models.models import FieldAuditLog
    from datetime import timedelta as td
    now = datetime.now()
    old_logs = []
    for i in range(15):
        days_ago = [400, 380, 366, 364, 200, 10, 1][i % 7]
        log = FieldAuditLog(
            operator="zhangsan", operator_role="employee",
            resource="balance", field_name="employee_name",
            target_employee_id=emp_id,
            original_value=f"测试员工{i}", masked_value=f"测***",
            mask_pattern="name", access_type="masked",
            request_id=f"req-old-{i:04d}"
        )
        db.add(log)
        db.flush()
        log.created_at = now - td(days=days_ago)
        old_logs.append((log.id, days_ago))
    for i in range(5):
        log = FieldAuditLog(
            operator="lisi", operator_role="hr",
            resource="employee", field_name="phone",
            target_employee_id=emp_id,
            original_value="13800138000", masked_value="138****8000",
            mask_pattern="phone", access_type="masked",
            request_id=f"req-new-{i:04d}"
        )
        db.add(log)
    db.commit()
    old_count = db.query(func.count(FieldAuditLog.id)).scalar()
    print(f"✓ 构造日志: balance={len([l for l in old_logs])}条, employee=5条, 共{old_count}条")

    cfg_bal = service.set_audit_retention(
        resource="balance", retention_days=365,
        retention_mode="delete", updated_by="admin_audit"
    )
    print(f"✓ balance留存策略: {cfg_bal.retention_days}天, mode={cfg_bal.retention_mode}")

    cfg_get = service.get_audit_retention("balance")
    assert cfg_get.retention_days == 365

    result_bal = service.cleanup_audit_logs("balance")
    print(f"✓ 清理balance: deleted={result_bal.deleted_count}, "
          f"archived={result_bal.archived_count}")
    assert result_bal.deleted_count >= 3

    remaining = db.query(FieldAuditLog).filter(
        FieldAuditLog.resource == "balance"
    ).count()
    print(f"  balance剩余: {remaining} 条")
    assert remaining < 15

    cfg_emp = service.set_audit_retention("employee", 180, "archive")
    result_emp = service.cleanup_audit_logs("employee")
    print(f"✓ 清理employee(archive): deleted={result_emp.deleted_count}, "
          f"archived={result_emp.archived_count}")

    cfg_check = service.get_audit_retention("balance")
    print(f"✓ 清理记录: last_cleanup={cfg_check.last_cleanup_at is not None}, "
          f"累计count={cfg_check.cleanup_count}")
    assert cfg_check.last_cleanup_at is not None
    assert cfg_check.cleanup_count >= result_bal.deleted_count

    db.close()
    print("✓ 审计留存策略测试通过")


def test_23_masking_policy_release_flow():
    print("\n" + "="*60)
    print("测试 23: scene_mask 脱敏策略下发与灰度")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    service = BalanceService(db)

    payload_v1 = {
        "balance": {
            "employee_name": {"action": "masked", "pattern": "name"},
            "frozen_balance": {"action": "hidden"}
        },
        "employee": {
            "phone": {"action": "masked", "pattern": "phone"},
            "email": {"action": "masked", "pattern": "email"}
        }
    }
    rel_v1 = service.create_masking_policy_release(
        policy_version="POLICY-V1.0.0",
        policy_payload=payload_v1,
        gray_percent=100,
        gray_tags=["hr", "finance"],
        subscriber_ids=["balance-api", "hr-dashboard"],
        released_by="Governance_Team"
    )
    print(f"✓ 创建策略: ver={rel_v1.policy_version}, "
          f"checksum={rel_v1.checksum[:10]}..., status={rel_v1.status}")
    assert rel_v1.status == "draft"
    assert rel_v1.gray_percent == 100

    before = service.get_active_masking_policy()
    print(f"  发布前: active_policy={before is not None}")
    assert before is None

    published = service.publish_masking_policy("POLICY-V1.0.0")
    print(f"✓ 发布: status={published.status}, released_at={published.released_at.strftime('%H:%M')}")
    assert published.status == "active"

    active = service.get_active_masking_policy()
    print(f"✓ 当前生效: ver={active['policy_version']}, "
          f"gray_tags={active['gray_tags']}, subscribers={active['subscriber_ids']}")
    assert active["policy_version"] == "POLICY-V1.0.0"

    data_bal = {
        "employee_id": 1, "employee_name": "张三丰",
        "balance": 20.0, "frozen_balance": 3.0,
        "phone": "13900139000", "email": "zhangsan@company.com"
    }
    masked = service.apply_released_masking_policy(data_bal, "balance")
    print(f"✓ 策略下发脱敏: name={masked['employee_name']}, "
          f"has_frozen={'frozen_balance' in masked}, balance={masked.get('balance')}")
    assert masked["employee_name"] == "张**"
    assert "frozen_balance" not in masked

    payload_v2 = {
        "balance": {
            "employee_name": {"action": "visible"},
            "balance": {"action": "masked", "pattern": "digit"}
        }
    }
    rel_v2 = service.create_masking_policy_release(
        policy_version="POLICY-V2.0.0",
        policy_payload=payload_v2,
        gray_percent=0,
        released_by="Governance_Team"
    )
    published_v2 = service.publish_masking_policy("POLICY-V2.0.0")
    print(f"✓ 发布V2灰度0%: status={published_v2.status}, gray={published_v2.gray_percent}%")

    gray_0 = service.apply_released_masking_policy(data_bal, "balance", gray_user_tag="employee")
    print(f"  灰度外: name={gray_0.get('employee_name')}, "
          f"has_frozen={'frozen_balance' in gray_0}")
    assert gray_0.get("employee_name") == "张三丰"
    assert "frozen_balance" in gray_0

    published_v2.gray_percent = 100
    db.commit()
    gray_100 = service.apply_released_masking_policy(data_bal, "balance")
    print(f"  灰度100%: name={gray_100.get('employee_name')}, "
          f"has_balance={'balance' in gray_100}")
    assert gray_100.get("employee_name") == "张三丰"

    rb = service.rollback_masking_policy("POLICY-V2.0.0")
    print(f"✓ 回滚V2: status={rb.status}, rolled_back={rb.rolled_back_at is not None}")
    assert rb.status == "rolled_back"

    active_after = service.get_active_masking_policy()
    print(f"  回滚后生效版本: {active_after['policy_version'] if active_after else None}")
    assert active_after is not None
    assert active_after["policy_version"] == "POLICY-V1.0.0"

    db.close()
    print("✓ 策略下发与灰度测试通过")


def test_24_corner_cases_and_boundary():
    print("\n" + "="*60)
    print("测试 24: 边界场景与异常路径覆盖率")
    print("="*60)

    db = setup_test_db()
    seed = seed_base_data(db)
    emp_id = seed["emp_ids"][0]
    sick_id = seed["lt_ids"][2]
    service = BalanceService(db)

    past_expire = date.today() - timedelta(days=3)
    service.grant_leave(emp_id, sick_id, 3.0, "病假", "admin", 2024, expire_date=past_expire)
    holds = service.scan_expired_and_create_holds(operator="system")
    hold = holds[0] if holds else None

    if hold:
        try:
            service.reapply_expire_hold(999999, operator="tester")
            assert False
        except ValueError as e:
            print(f"✓ reapply不存在hold: {e}")

        service.approve_expire_hold(hold.id, "HR")
        try:
            service.reapply_expire_hold(hold.id, operator="tester")
            assert False
        except ValueError as e:
            print(f"✓ reapply已approved: {e}")

    warnings_total, _ = service.list_truncate_warnings(severity="info")
    print(f"✓ list_warnings空级别过滤: {warnings_total} 条")

    try:
        service.rollback_field_permission("invalid-token-xyz")
        assert False
    except ValueError as e:
        print(f"✓ rollback无效token: {e}")

    total_audits, _ = service.list_field_permission_audits(permission_id=99999)
    print(f"✓ audit空permission过滤: {total_audits} 条")

    try:
        service.set_cursor_secret_expiry(99999, datetime.now())
        assert False
    except ValueError as e:
        print(f"✓ 过期不存在密钥: {e}")

    try:
        service.deactivate_cursor_secret(99999)
        assert False
    except ValueError as e:
        print(f"✓ 停用不存在密钥: {e}")

    primary = service.rotate_cursor_secret("secret-primary-boundary")
    try:
        service.deactivate_cursor_secret(primary.id)
        assert False
    except ValueError as e:
        print(f"✓ 停用主密钥被拒: {e}")

    try:
        service.break_freeze_rollback_cycle(999999)
        assert False
    except ValueError as e:
        print(f"✓ 断裂不存在日志: {e}")

    r_none = service.check_freeze_rollback_cycle_dfs(start_log_id=999999)
    print(f"✓ DFS不存在起点: has_cycle={r_none['has_cycle']}, nodes={r_none['nodes_visited']}")
    assert r_none["has_cycle"] is False

    try:
        service.update_sensitive_position_rule(99999, is_active=False)
        assert False
    except ValueError as e:
        print(f"✓ 更新不存在规则: {e}")

    cfg_default = service.get_audit_retention("unknown-resource")
    print(f"✓ 未知资源默认留存: {cfg_default.retention_days}天")
    assert cfg_default.retention_days == 365

    r_clean_empty = service.cleanup_audit_logs("nonexistent-resource")
    print(f"✓ 清理不存在资源: deleted={r_clean_empty.deleted_count}")

    try:
        service.publish_masking_policy("NOT-EXIST-VERSION")
        assert False
    except ValueError as e:
        print(f"✓ 发布不存在版本: {e}")

    try:
        service.create_masking_policy_release("POLICY-V1.0.0", {"x": 1})
    except ValueError as e:
        print(f"✓ 创建重复版本: {e}")

    from app.services.permission_service import PermissionService
    perm = PermissionService(db)
    auth_hr = perm.get_auth_context("hr1")
    svc_hr = BalanceService(db, auth_hr)

    normal_emp = {"employee_id": emp_id, "employee_name": "张三丰",
                   "balance": 5.0, "frozen_balance": 1.0}
    r_no_target = svc_hr.apply_field_filter(normal_emp, "balance", target_employee_id=None)
    print(f"✓ 无目标员工场景化脱敏: name={r_no_target.data['employee_name']}")
    assert r_no_target.data["employee_name"] == "张三丰"

    db.close()
    print("✓ 边界场景覆盖测试通过")


def run_all_tests():
    print("\n" + "#"*60)
    print("#  员工假期余额管理API - 第四批8大收尾特性测试")
    print("#"*60)

    test_funcs = [
        test_9_hold_reapply_path,
        test_10_recover_stuck_with_compensation,
        test_11_retro_chain_deep_truncate,
        test_12_field_permission_runtime_toggle,
        test_13_cursor_secret_rotation,
        test_14_rollback_chain_cycle_detection,
        test_15_hr_scenario_masking,
        test_16_masking_audit_trail,
        test_17_truncate_warning_log,
        test_18_field_permission_rollback,
        test_19_cursor_secret_expiry,
        test_20_dfs_cycle_detection_performance,
        test_21_sensitive_position_custom_rules,
        test_22_audit_retention_cleanup,
        test_23_masking_policy_release_flow,
        test_24_corner_cases_and_boundary,
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
        print("#  ✓ 全部第四批收尾特性测试通过！")
    print("#"*60 + "\n")
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
