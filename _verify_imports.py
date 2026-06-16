import sys
sys.path.insert(0, '.')
print('=== 检查 app/models/models.py 导入 ===')
from app.models import models
tbls = [c for c in dir(models) if not c.startswith('_') and hasattr(getattr(models, c), '__tablename__')]
print('  models 模块 OK, 表数:', len(tbls))

print('=== 检查 app/services/balance_service.py 导入 ===')
from app.services.balance_service import BalanceService
methods = [m for m in dir(BalanceService) if not m.startswith('_')]
print('  BalanceService OK, 方法数:', len(methods))
print('    关键方法:', ', '.join(sorted([m for m in methods if any(k in m.lower() for k in ['truncate','audit','retention','rollback','secret','policy','cycle','dfs','rule','mask'])])))

print('=== 检查 app/schemas/schemas.py 导入 ===')
from app.schemas import schemas
print('  schemas 模块 OK')

print('=== 检查 app/utils/__init__.py 导入 ===')
from app.utils import CURSOR_SECRET, apply_field_permissions, encode_cursor, decode_cursor, with_retry
print('  utils OK, CURSOR_SECRET[:4] =', CURSOR_SECRET[:4])

print('=== 检查 app/main.py 导入 ===')
import app.main as main_mod
print('  main OK')
print()
print('全部导入验证通过 ✓')
