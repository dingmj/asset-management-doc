"""ORM 模型公共列与类型别名。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, SmallInteger, func
from sqlalchemy.orm import Mapped, mapped_column

# MySQL 用 BIGINT，SQLite（本地测试）自动降级为 INTEGER 以支持自增主键
BigIntPK = BigInteger().with_variant(Integer, "sqlite")
BigIntFK = BigInteger().with_variant(Integer, "sqlite")


class TimestampMixin:
    """创建/更新时间列，所有业务表复用。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间",
    )


class StatusMixin:
    """启用/禁用状态列：1=启用，0=禁用。"""

    status: Mapped[int] = mapped_column(
        SmallInteger,
        default=1,
        server_default="1",
        nullable=False,
        comment="状态：1=启用，0=禁用",
    )
