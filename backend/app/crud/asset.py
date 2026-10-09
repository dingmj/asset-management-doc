"""
设备 CRUD。

``build_query`` 是阶段二的核心：把 ``AssetQuery`` 里的每一个可选条件
翻译成一段 WHERE 子句，实现「传什么就筛什么」的动态筛选。
"""

from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.models.asset import (
    ASSET_STATUS_AVAILABLE,
    ASSET_STATUS_BORROWED,
    ASSET_STATUS_LABELS,
    Asset,
    AssetType,
)
from app.models.user import SysUser
from app.schemas.asset import AssetQuery


class CRUDAsset(CRUDBase[Asset]):
    """设备数据访问层。"""

    # ---------------- 唯一性 ----------------
    def get_by_asset_no(self, db: Session, asset_no: str) -> Optional[Asset]:
        """按资产编号查询（含已软删除记录，由业务层判断）。"""
        return db.scalars(select(Asset).where(Asset.asset_no == asset_no).limit(1)).first()

    def is_asset_no_taken(self, db: Session, asset_no: str, exclude_id: Optional[int] = None) -> bool:
        stmt = select(Asset.id).where(Asset.asset_no == asset_no)
        if exclude_id is not None:
            stmt = stmt.where(Asset.id != exclude_id)
        return db.scalars(stmt.limit(1)).first() is not None

    # ---------------- 动态筛选 ----------------
    def build_query(
        self,
        query: AssetQuery,
        *,
        ids: Optional[Sequence[int]] = None,
        status_list: Optional[Sequence[int]] = None,
    ) -> Select:
        """
        根据筛选条件拼装查询语句。

        约定：所有条件都是「传了才生效」，条件之间是 AND 关系。

        :param ids: 按主键列表筛选（批量操作使用，不走 HTTP 查询串）
        :param status_list: 状态多选。之所以不放进 ``AssetQuery``：
            FastAPI 会把模型里的 ``List[...]`` 字段当成**请求体**而不是查询参数，
            导致 GET 请求无法传值。这类「多值」条件统一由接口层显式声明为
            ``Query(...)`` 参数后再传进来。
        """
        stmt = select(Asset).where(Asset.is_deleted == 0)

        # ---------- 主键列表（批量操作） ----------
        if ids:
            stmt = stmt.where(Asset.id.in_(list(ids)))

        # ---------- 关键字：一次匹配多个字段 ----------
        if query.keyword:
            kw = f"%{query.keyword.strip()}%"
            stmt = stmt.where(
                or_(
                    Asset.asset_no.like(kw),
                    Asset.name.like(kw),
                    Asset.sn.like(kw),
                    Asset.brand.like(kw),
                    Asset.model.like(kw),
                )
            )
        if query.asset_no:
            stmt = stmt.where(Asset.asset_no.like(f"%{query.asset_no.strip()}%"))
        if query.name:
            stmt = stmt.where(Asset.name.like(f"%{query.name.strip()}%"))
        if query.sn:
            stmt = stmt.where(Asset.sn.like(f"%{query.sn.strip()}%"))

        # ---------- 按类型 ----------
        # type_code 需要 join asset_type；用 EXISTS 子查询避免影响分页 count
        if query.type_id is not None:
            stmt = stmt.where(Asset.type_id == query.type_id)
        if query.type_code:
            type_exists = (
                select(AssetType.id)
                .where(AssetType.id == Asset.type_id, AssetType.code == query.type_code.strip())
                .exists()
            )
            stmt = stmt.where(type_exists)

        # ---------- 按状态 ----------
        if query.status is not None:
            stmt = stmt.where(Asset.status == query.status)
        elif status_list:
            stmt = stmt.where(Asset.status.in_(list(status_list)))
        if query.only_available:
            stmt = stmt.where(Asset.status == ASSET_STATUS_AVAILABLE)

        # ---------- 归属 / 领用人 ----------
        if query.department:
            stmt = stmt.where(Asset.department.like(f"%{query.department.strip()}%"))
        if query.location:
            stmt = stmt.where(Asset.location.like(f"%{query.location.strip()}%"))
        if query.borrower_id is not None:
            stmt = stmt.where(Asset.borrower_id == query.borrower_id)
        if query.borrower_keyword:
            kw = f"%{query.borrower_keyword.strip()}%"
            borrower_exists = (
                select(SysUser.id)
                .where(
                    SysUser.id == Asset.borrower_id,
                    or_(SysUser.real_name.like(kw), SysUser.username.like(kw)),
                )
                .exists()
            )
            stmt = stmt.where(borrower_exists)

        # ---------- 按领用时间范围 ----------
        if query.borrow_at_start is not None:
            stmt = stmt.where(Asset.borrow_at >= query.borrow_at_start)
        if query.borrow_at_end is not None:
            stmt = stmt.where(Asset.borrow_at <= query.borrow_at_end)

        # ---------- 按过期（到期）时间范围 ----------
        if query.expire_at_start is not None:
            stmt = stmt.where(Asset.expire_at >= query.expire_at_start)
        if query.expire_at_end is not None:
            stmt = stmt.where(Asset.expire_at <= query.expire_at_end)
        if query.only_expired:
            stmt = stmt.where(Asset.expire_at.is_not(None), Asset.expire_at < datetime.now())
        if query.expire_within_days is not None:
            # [now, now + N 天] 内到期，排除已超期
            now = datetime.now()
            stmt = stmt.where(
                Asset.expire_at.is_not(None),
                Asset.expire_at >= now,
                Asset.expire_at <= now + timedelta(days=query.expire_within_days),
            )

        # ---------- 排序 ----------
        return stmt.order_by(*self._order_clauses(query))

    @staticmethod
    def _order_clauses(query: AssetQuery):
        """默认按 ID 倒序（最新录入优先）；指定字段时该字段为主排序。"""
        desc = query.order_desc
        if query.order_by:
            column = getattr(Asset, query.order_by)
            return [column.desc() if desc else column.asc(), Asset.id.desc()]
        return [Asset.id.desc()]

    # ---------------- 常用查询 ----------------
    def list_borrowable(self, db: Session, keyword: str = "", limit: int = 100) -> List[Asset]:
        """可领用设备（状态=闲置且未删除），用于领用申请时的设备选择器。"""
        stmt = select(Asset).where(
            Asset.is_deleted == 0,
            Asset.status == ASSET_STATUS_AVAILABLE,
        )
        if keyword:
            kw = f"%{keyword.strip()}%"
            stmt = stmt.where(or_(Asset.asset_no.like(kw), Asset.name.like(kw)))
        return list(db.scalars(stmt.order_by(Asset.id.desc()).limit(max(1, min(limit, 500)))).all())

    def get_for_update(self, db: Session, asset_id: int) -> Optional[Asset]:
        """
        加行锁读取设备（审批通过/归还时使用）。

        MySQL 下 ``SELECT ... FOR UPDATE`` 防止同一台设备被两条并发审批同时占用；
        SQLite 不支持该语法，由 SQLAlchemy 自动忽略。
        """
        return db.scalars(
            select(Asset).where(Asset.id == asset_id).with_for_update()
        ).first()

    # ---------------- 统计辅助 ----------------
    def count_group_by_status(self, db: Session, type_id: Optional[int] = None,
                              department: Optional[str] = None) -> List[Tuple[int, int]]:
        """按设备状态分组计数 -> [(status, count)]。"""
        stmt = (
            select(Asset.status, func.count(Asset.id))
            .where(Asset.is_deleted == 0)
            .group_by(Asset.status)
        )
        if type_id is not None:
            stmt = stmt.where(Asset.type_id == type_id)
        if department:
            stmt = stmt.where(Asset.department.like(f"%{department.strip()}%"))
        return [(int(s), int(c)) for s, c in db.execute(stmt).all()]

    def count_group_by_type(self, db: Session) -> List[Tuple[int, str, int]]:
        """按设备类型分组计数 -> [(type_id, type_name, count)]。"""
        stmt = (
            select(AssetType.id, AssetType.name, func.count(Asset.id))
            .join(Asset, Asset.type_id == AssetType.id)
            .where(Asset.is_deleted == 0)
            .group_by(AssetType.id, AssetType.name)
            .order_by(func.count(Asset.id).desc())
        )
        return [(int(i), str(n), int(c)) for i, n, c in db.execute(stmt).all()]

    def status_label(self, status: int) -> str:
        return ASSET_STATUS_LABELS.get(status, "未知")

    def mark_borrowed(self, db: Session, asset: Asset, borrower_id: int,
                      borrow_at: datetime, expire_at: datetime, commit: bool = False) -> Asset:
        """审批通过时回写领用信息（阶段三调用）。"""
        asset.status = ASSET_STATUS_BORROWED
        asset.borrower_id = borrower_id
        asset.borrow_at = borrow_at
        asset.expire_at = expire_at
        db.add(asset)
        if commit:
            db.commit()
            db.refresh(asset)
        else:
            db.flush()
        return asset

    def mark_returned(self, db: Session, asset: Asset, status: int = ASSET_STATUS_AVAILABLE,
                      commit: bool = False) -> Asset:
        """归还时清除领用信息（阶段三调用）。"""
        asset.status = status
        asset.borrower_id = None
        asset.borrow_at = None
        asset.expire_at = None
        db.add(asset)
        if commit:
            db.commit()
            db.refresh(asset)
        else:
            db.flush()
        return asset


asset_crud = CRUDAsset(Asset)
