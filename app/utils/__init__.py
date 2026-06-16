import time
import random
import logging
from functools import wraps
from typing import Callable, Any, Type, Tuple
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

CONCURRENCY_ERRORS: Tuple[Type[Exception], ...] = (ValueError,)


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


def is_workday(d: "date", holidays: set = None, workdays: set = None) -> bool:
    if holidays and d in holidays:
        return False
    if workdays and d in workdays:
        return True
    return d.weekday() < 5


def count_workdays(start: "date", end: "date", holidays: set = None, workdays: set = None) -> int:
    if end < start:
        return 0
    count = 0
    current = start
    while current <= end:
        if is_workday(current, holidays, workdays):
            count += 1
        current += __import__("datetime").timedelta(days=1)
    return count


def add_workdays(d: "date", days: int, holidays: set = None, workdays: set = None) -> "date":
    current = d
    added = 0
    while added < days:
        current += __import__("datetime").timedelta(days=1)
        if is_workday(current, holidays, workdays):
            added += 1
    return current
