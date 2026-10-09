"""
声明式基类。

统一在此处定义：
1. 约束命名规范（便于 Alembic 生成稳定的迁移脚本、便于后续 DROP）；
2. MySQL 表参数（InnoDB + utf8mb4），保证中文与 emoji 都能正常存储。
"""

from typing import Any, Dict

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase, declared_attr

from app.core.config import settings

# 约束命名规范：避免 MySQL 自动命名导致 Alembic autogenerate 误判为“新约束”
NAMING_CONVENTION: Dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# MySQL 建表参数（非 MySQL 方言会自动忽略）
MYSQL_TABLE_ARGS: Dict[str, Any] = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
} if not settings.is_sqlite else {}


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    @declared_attr.directive
    def __table_args__(cls) -> Dict[str, Any]:  # noqa: N805
        """默认给每张表挂上 MySQL 建表参数；子类可覆写。"""
        return dict(MYSQL_TABLE_ARGS)
