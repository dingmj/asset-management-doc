"""角色 CRUD。"""

from typing import List, Optional

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.models.role import SysRole, SysUserRole
from app.models.user import SysUser


class CRUDRole(CRUDBase[SysRole]):
    """角色数据访问层。"""

    def get_by_code(self, db: Session, code: str) -> Optional[SysRole]:
        return db.scalars(select(SysRole).where(SysRole.code == code).limit(1)).first()

    def is_code_taken(self, db: Session, code: str, exclude_id: Optional[int] = None) -> bool:
        stmt = select(SysRole.id).where(SysRole.code == code)
        if exclude_id is not None:
            stmt = stmt.where(SysRole.id != exclude_id)
        return db.scalars(stmt.limit(1)).first() is not None

    def build_query(self, keyword: Optional[str] = None, status: Optional[int] = None) -> Select:
        stmt = select(SysRole)
        if keyword:
            kw = f"%{keyword.strip()}%"
            stmt = stmt.where(SysRole.name.like(kw) | SysRole.code.like(kw))
        if status is not None:
            stmt = stmt.where(SysRole.status == status)
        return stmt.order_by(SysRole.sort.asc(), SysRole.id.asc())

    def list_enabled(self, db: Session) -> List[SysRole]:
        """所有启用角色（下拉框用）。"""
        return list(db.scalars(self.build_query(status=1)).all())

    def count_users(self, db: Session, role_id: int) -> int:
        """统计该角色下绑定的用户数（删角色前的占用检查）。"""
        stmt = (
            select(func.count())
            .select_from(SysUserRole)
            .join(SysUser, SysUser.id == SysUserRole.user_id)
            .where(SysUserRole.role_id == role_id, SysUser.is_deleted == 0)
        )
        return int(db.scalar(stmt) or 0)


role_crud = CRUDRole(SysRole)
