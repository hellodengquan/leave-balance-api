import sys, os
sys.path.insert(0, '.')
from datetime import date, datetime, timedelta
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.models.models import Base, FrozenBalanceLog
from app.services.balance_service import BalanceService
from tests.test_new_features import setup_test_db, seed_base_data

db = setup_test_db()
seed = seed_base_data(db)
emp_id = seed['emp_ids'][0]
annual_id = seed['lt_ids'][0]
service = BalanceService(db)

acc = service._get_or_create_account(emp_id, annual_id, 2024)
logs = []
prev_id = None
for i in range(30):
    log = FrozenBalanceLog(
        account_id=acc.id, employee_id=emp_id, leave_type_id=annual_id,
        operation='freeze' if i % 2 == 0 else 'unfreeze',
        days=0.5, balance_before=acc.balance, balance_after=acc.balance,
        operator='tester', reason='step-%d' % i, rollback_of_id=prev_id
    )
    db.add(log); db.flush()
    prev_id = log.id; logs.append(log.id)
db.commit()
print('logs[0:5]=', logs[:5], '... logs[-3:]=', logs[-3:])

for lid in logs[:3] + logs[-2:]:
    l = db.query(FrozenBalanceLog).filter(FrozenBalanceLog.id == lid).first()
    print('  log#%d: rollback_of_id=%s' % (l.id, l.rollback_of_id))

last = db.query(FrozenBalanceLog).filter(FrozenBalanceLog.id == logs[-1]).first()
last.rollback_of_id = logs[0]
db.commit()
print('cycle: log#%d.rollback_of_id=%d (logs[0]=%d)' % (logs[-1], last.rollback_of_id, logs[0]))

print('--- walking from logs[-1]=%d ---' % logs[-1])
cur_id = logs[-1]
seen = []
for step in range(35):
    l = db.query(FrozenBalanceLog).filter(FrozenBalanceLog.id == cur_id).first()
    print('  step%d: log#%d.rollback_of_id=%s' % (step, cur_id, l.rollback_of_id if l else 'None'))
    seen.append(cur_id)
    if l.rollback_of_id is None:
        break
    if l.rollback_of_id in seen:
        print('  CYCLE DETECTED: back to log#%d' % l.rollback_of_id)
        break
    cur_id = l.rollback_of_id

r = service.check_freeze_rollback_cycle_dfs(start_log_id=logs[-1], max_depth=50)
print('result: has_cycle=%s, start=%s, path=%s, depth=%s' % (
    r['has_cycle'], r['cycle_start_id'], r['cycle_path'], r['total_depth']))

print('--- sensitive position debug ---')
from app.models.models import Employee, SensitivePositionRule
import json
cfo = Employee(employee_no='E-CFO-001', name='王国富',
                department='财务', position='集团CFO', hire_date=date(2018,3,1))
db.add(cfo); db.commit(); db.refresh(cfo)
print('CFO: position=%s' % cfo.position)

fm = {
    "employee_name": {"access": "masked", "pattern": "name"},
    "frozen_balance": {"access": "hidden"},
    "pending_expire_days": {"access": "hidden"}
}
rule_cfo = SensitivePositionRule(
    rule_name='CFO级', position_pattern='CFO', match_mode='contains',
    field_mappings=json.dumps(fm, ensure_ascii=False), is_active=True,
    priority=50, created_by='admin'
)
db.add(rule_cfo); db.commit(); db.refresh(rule_cfo)
print('rule pattern=', rule_cfo.position_pattern, 'mode=', rule_cfo.match_mode)

rules = db.query(SensitivePositionRule).filter(SensitivePositionRule.is_active==True).all()
matched = None
for r in rules:
    if r.match_mode == 'exact' and cfo.position == r.position_pattern:
        matched = r; break
    elif r.match_mode == 'contains' and r.position_pattern in cfo.position:
        matched = r; break
    elif r.match_mode == 'startswith' and cfo.position.startswith(r.position_pattern):
        matched = r; break
print('matched rule:', matched.id if matched else None)
