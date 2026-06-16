import time
import random
import hashlib
import logging
from functools import wraps
from typing import Callable, Any, Type, Tuple, Set, Optional
from datetime import date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

CONCURRENCY_ERRORS: Tuple[Type[Exception], ...] = (ValueError,)

CURSOR_SECRET = "leave-balance-cursor-v1"


class TransactionBoundary:
    def __init__(self, db: Session, savepoint: bool = False):
        self.db = db
        self.savepoint = savepoint
        self._nested = savepoint

    def __enter__(self):
        if self._nested:
            self.db.begin_nested()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            logger.warning(
                f"事务回滚: {exc_type.__name__}: {exc_val}"
            )
            self.db.rollback()
            return False
        try:
            self.db.commit()
        except SQLAlchemyError as e:
            logger.error(f"事务提交失败，执行回滚: {e}")
            self.db.rollback()
            raise
        return True


def transactional(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        db = kwargs.get("db")
        if db is None:
            for arg in args:
                if isinstance(arg, Session):
                    db = arg
                    break

        if db is None:
            raise ValueError("未找到数据库会话 Session 参数")

        try:
            result = func(*args, **kwargs)
            db.commit()
            return result
        except Exception as e:
            db.rollback()
            logger.error(f"[{func.__name__}] 事务回滚: {type(e).__name__}: {e}")
            raise

    return wrapper


def with_retry(
    max_retries: int = 3,
    base_delay: float = 0.1,
    max_delay: float = 2.0,
    jitter: bool = True,
    retry_exceptions: Tuple[Type[Exception], ...] = CONCURRENCY_ERRORS
) -> Callable:
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except retry_exceptions as e:
                    last_exception = e
                    if attempt == max_retries - 1:
                        logger.warning(
                            f"[{func.__name__}] 已达最大重试次数 {max_retries}，"
                            f"最后错误: {e}"
                        )
                        break

                    delay = min(base_delay * (2 ** attempt), max_delay)
                    if jitter:
                        delay = delay * (0.5 + random.random() * 0.5)

                    logger.info(
                        f"[{func.__name__}] 第 {attempt + 1}/{max_retries} 次重试，"
                        f"等待 {delay:.3f}s，原因: {e}"
                    )
                    time.sleep(delay)

                    db = kwargs.get("db")
                    if db is None:
                        for arg in args:
                            if isinstance(arg, Session):
                                db = arg
                                break
                    if db is not None:
                        db.rollback()
                        db.expire_all()

            raise last_exception
        return wrapper
    return decorator


def is_workday(d: date, holidays: set = None, workdays: set = None) -> bool:
    if holidays and d in holidays:
        return False
    if workdays and d in workdays:
        return True
    return d.weekday() < 5


def count_workdays(start: date, end: date, holidays: set = None, workdays: set = None) -> int:
    if end < start:
        return 0
    count = 0
    current = start
    while current <= end:
        if is_workday(current, holidays, workdays):
            count += 1
        current += timedelta(days=1)
    return count


def add_workdays(d: date, days: int, holidays: set = None, workdays: set = None) -> date:
    current = d
    added = 0
    while added < days:
        current += timedelta(days=1)
        if is_workday(current, holidays, workdays):
            added += 1
    return current


def encode_cursor(txn_id: int, sort_key: str = "id_desc") -> str:
    import base64, json
    payload = {"id": txn_id, "sk": sort_key}
    raw = json.dumps(payload, separators=(",", ":")).encode()
    sig = hashlib.sha256(raw + CURSOR_SECRET.encode()).hexdigest()[:8]
    data = {"p": payload, "s": sig}
    return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()


def decode_cursor(cursor: str) -> Tuple[Optional[int], Optional[str]]:
    import base64, json
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        payload = data.get("p", {})
        sig = data.get("s", "")
        expected_sig = hashlib.sha256(
            json.dumps(payload, separators=(",", ":")).encode() + CURSOR_SECRET.encode()
        ).hexdigest()[:8]
        if sig != expected_sig:
            return None, None
        return payload.get("id"), payload.get("sk", "id_desc")
    except Exception:
        return None, None


def apply_field_permissions(
    data: dict,
    role: str,
    resource: str,
    field_permissions: list
) -> Tuple[dict, list, list]:
    masked_fields = []
    hidden_fields = []
    result = {}
    for fp in field_permissions:
        if fp.role != role or fp.resource != resource:
            continue
        field = fp.field_name
        if field not in data:
            continue
        if fp.access == "hidden":
            hidden_fields.append(field)
            continue
        if fp.access == "masked" and fp.mask_pattern:
            masked_fields.append(field)
            val = str(data[field])
            if fp.mask_pattern == "name":
                result[field] = val[0] + "**" if len(val) > 1 else val
            elif fp.mask_pattern == "partial":
                result[field] = val[:2] + "***" + val[-2:] if len(val) > 4 else "***"
            else:
                result[field] = fp.mask_pattern
            continue
    for k, v in data.items():
        if k not in hidden_fields and k not in masked_fields:
            result[k] = v
    return result, masked_fields, hidden_fields
