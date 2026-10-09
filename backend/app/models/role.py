"""
角色模型。

表：
- ``sys_role``      角色字典（ADMIN / USER ...）
- ``sys_user_role`` 用户-角色关联表（多对多）
"""

from typing import TYPE_CHECKING, List

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import MYSQL_TABLE_ARGS, Base
from app.models.mixins import BigIntFK, BigIntPK, StatusMixin, TimestampMixin

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型提示，避免循环导入
    from app.models.user import SysUser

# 系统内置角色编码，代码中统一引用，避免魔法字符串
ROLE_ADMIN = "ADMIN"
ROLE_USER = "USER"


class SysRole(Base, TimestampMixin, StatusMixin):
    """角色表。"""

    __tablename__ = "sys_role"
    __table_args__ = (dict(MYSQL_TABLE_ARGS),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True, comment="角色ID")
    code: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True, comment="角色编码：ADMIN/USER"
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="角色名称")
    description: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=text("''"), comment="角色描述"
    )
    # 仅用于前端下拉排序，数值越小越靠前
    sort: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), comment="排序值"
    )

    # 反向关系：该角色下的所有用户
    users: Mapped[List["SysUser"]] = relationship(
        secondary="sys_user_role",
        back_populates="roles",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SysRole id={self.id} code={self.code!r}>"


class SysUserRole(Base, TimestampMixin):
    """用户-角色关联表。同一用户不可重复绑定同一角色。"""

    __tablename__ = "sys_user_role"
    __table_args__ = (
        UniqueConstraint("user_id", "role_id", name="uq_sys_user_role_user_role"),
        dict(MYSQL_TABLE_ARGS),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True, comment="主键")
    user_id: Mapped[int] = mapped_column(
        BigIntFK,
        ForeignKey("sys_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="用户ID",
    )
    role_id: Mapped[int] = mapped_column(
        BigIntFK,
        ForeignKey("sys_role.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="角色ID",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SysUserRole user_id={self.user_id} role_id={self.role_id}>"
