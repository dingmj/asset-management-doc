"""
数据库引擎与会话管理（同步 SQLAlchemy 2.0）。

- 使用同步 ``Session``，不使用 async SQLAlchemy；
- ``get_db`` 作为 FastAPI 依赖，请求结束自动 close，异常自动 rollback；
- 连接池开启 pre_ping + recycle，避免 MySQL 空闲断连导致的首个请求报错。
"""

from collections.abc import Generator
from typing import Any, Dict

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# ---------- 引擎参数 ----------
engine_kwargs: Dict[str, Any] = {
    "echo": settings.SQL_ECHO,
    "pool_pre_ping": True,  # 取连接前先 ping，自动剔除失效连接
    "future": True,
}

if settings.is_sqlite:
    # SQLite 仅用于本地快速验证/单测
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    engine_kwargs.update(
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_recycle=settings.DB_POOL_RECYCLE,
    )

engine = create_engine(settings.database_url, **engine_kwargs)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,  # 提交后对象仍可读，省去一次无谓查询
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每个请求一个独立 Session。"""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
