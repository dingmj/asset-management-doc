"""
用户模型：``sys_user``。

说明：
- 密码列只保存 bcrypt 哈希；
- 采用软删除（``is_deleted``），保证历史领用记录不会因为删用户而变成孤儿数据；
- ``roles`` 使用 selectin 预加载，列表接口不会产生 N+1 查询。
"""

from datetime import datetime
from typing import TYPE_CHECKING, List, Set

from sqlalchemy import DateTime, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import MYSQL_TABLE_ARGS, Base
from app.models.mixins import BigIntPK, StatusMixin, TimestampMixin
from app.models.role import ROLE_ADMIN

if TYPE_CHECKING:  # pragma: no cover
    from app.models.role import SysRole


class SysUser(Base, TimestampMixin, StatusMixin):
    """系统用户表。"""

    __tablename__ = "sys_user"
    __table_args__ = (dict(MYSQL_TABLE_ARGS),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True, comment="用户ID")
    username: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True, comment="登录账号"
    )
    password: Mapped[str] = mapped_column(String(255), nullable=False, comment="密码（bcrypt 哈希）")
    real_name: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=text("''"), comment="真实姓名"
    )
    email: Mapped[str] = mapped_column(
        String(120), nullable=False, server_default=text("''"), comment="邮箱（到期提醒用）"
    )
    phone: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("''"), comment="手机号"
    )
    department: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default=text("''"), comment="所属部门"
    )
    remark: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=text("''"), comment="备注"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="最后登录时间")
    is_deleted: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default=text("0"),
        comment="软删除：1=已删除",
    )

    roles: Mapped[List["SysRole"]] = relationship(
        secondary="sys_user_role",
        back_populates="users",
        lazy="selectin",
    )

    # ---------- 便捷属性 ----------
    @property
    def role_codes(self) -> Set[str]:
        return {r.code for r in self.roles}

    @property
    def role_names(self) -> List[str]:
        return [r.name for r in self.roles]

    @property
    def is_admin(self) -> bool:
        """是否具备管理员身份（拥有 ADMIN 角色）。"""
        return ROLE_ADMIN in self.role_codes

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SysUser id={self.id} username={self.username!r}>"
