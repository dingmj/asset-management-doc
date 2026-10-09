"""用户 CRUD。"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import Select, delete, or_, select
from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.models.role import SysRole, SysUserRole
from app.models.user import SysUser
from app.schemas.user import UserQuery


class CRUDUser(CRUDBase[SysUser]):
    """用户数据访问层。"""

    def get_by_username(self, db: Session, username: str) -> Optional[SysUser]:
        """按账号查询（含已软删除记录，由业务层判断）。"""
        return db.scalars(select(SysUser).where(SysUser.username == username).limit(1)).first()

    def is_username_taken(self, db: Session, username: str, exclude_id: Optional[int] = None) -> bool:
        """账号是否已被占用（含软删除用户，避免复用造成历史数据混淆）。"""
        stmt = select(SysUser.id).where(SysUser.username == username)
        if exclude_id is not None:
            stmt = stmt.where(SysUser.id != exclude_id)
        return db.scalars(stmt.limit(1)).first() is not None

    def build_query(self, query: UserQuery) -> Select:
        """根据筛选条件拼装查询语句。"""
        stmt = select(SysUser).where(SysUser.is_deleted == 0)

        if query.username:
            stmt = stmt.where(SysUser.username.like(f"%{query.username.strip()}%"))
        if query.real_name:
            stmt = stmt.where(SysUser.real_name.like(f"%{query.real_name.strip()}%"))
        if query.department:
            stmt = stmt.where(SysUser.department.like(f"%{query.department.strip()}%"))
        if query.status is not None:
            stmt = stmt.where(SysUser.status == query.status)

        # 按角色筛选：走关联表 EXISTS，避免 join 造成重复行
        if query.role_id is not None or query.role_code:
            role_cond = [SysUserRole.user_id == SysUser.id]
            role_stmt = select(SysUserRole.id).join(SysRole, SysRole.id == SysUserRole.role_id).where(*role_cond)
            if query.role_id is not None:
                role_stmt = role_stmt.where(SysUserRole.role_id == query.role_id)
            if query.role_code:
                role_stmt = role_stmt.where(SysRole.code == query.role_code)
            stmt = stmt.where(role_stmt.exists())

        # 默认按 ID 倒序，最新创建的排前面
        return stmt.order_by(SysUser.id.desc())

    def set_roles(self, db: Session, user: SysUser, role_ids: List[int], commit: bool = True) -> None:
        """全量覆盖用户角色绑定。"""
        # 先清空旧绑定，再写入新绑定（去重，避免唯一键冲突）
        db.execute(delete(SysUserRole).where(SysUserRole.user_id == user.id))
        for role_id in sorted(set(role_ids)):
            db.add(SysUserRole(user_id=user.id, role_id=role_id))
        if commit:
            db.commit()
            db.refresh(user)
        else:
            db.flush()

    def touch_login(self, db: Session, user: SysUser) -> None:
        """记录最后登录时间。"""
        user.last_login_at = datetime.now()
        db.add(user)
        db.commit()
        db.refresh(user)

    def get_emails_by_role(self, db: Session, role_code: str) -> List[str]:
        """取某角色下所有启用用户的邮箱（阶段四到期提醒会用到）。"""
        stmt = (
            select(SysUser.email)
            .join(SysUserRole, SysUserRole.user_id == SysUser.id)
            .join(SysRole, SysRole.id == SysUserRole.role_id)
            .where(
                SysRole.code == role_code,
                SysUser.status == 1,
                SysUser.is_deleted == 0,
                SysUser.email != "",
            )
        )
        return [e for e in db.scalars(stmt).all() if e]

    def search_kw(self, keyword: str) -> Select:
        """按账号/姓名模糊搜索（用于选择器）。"""
        kw = f"%{keyword.strip()}%"
        return (
            select(SysUser)
            .where(SysUser.is_deleted == 0, SysUser.status == 1, or_(SysUser.username.like(kw), SysUser.real_name.like(kw)))
            .order_by(SysUser.id.desc())
        )


user_crud = CRUDUser(SysUser)
