"""设备类型 CRUD。"""

from typing import Dict, List, Optional

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.models.asset import Asset, AssetType


class CRUDAssetType(CRUDBase[AssetType]):
    """设备类型数据访问层。"""

    def get_by_code(self, db: Session, code: str) -> Optional[AssetType]:
        return db.scalars(select(AssetType).where(AssetType.code == code).limit(1)).first()

    def is_code_taken(self, db: Session, code: str, exclude_id: Optional[int] = None) -> bool:
        stmt = select(AssetType.id).where(AssetType.code == code)
        if exclude_id is not None:
            stmt = stmt.where(AssetType.id != exclude_id)
        return db.scalars(stmt.limit(1)).first() is not None

    def build_query(
        self,
        keyword: Optional[str] = None,
        status: Optional[int] = None,
    ) -> Select:
        """按名称/编码模糊 + 状态筛选。"""
        stmt = select(AssetType)
        if keyword:
            kw = f"%{keyword.strip()}%"
            stmt = stmt.where(AssetType.name.like(kw) | AssetType.code.like(kw))
        if status is not None:
            stmt = stmt.where(AssetType.status == status)
        return stmt.order_by(AssetType.sort.asc(), AssetType.id.asc())

    def list_enabled(self, db: Session) -> List[AssetType]:
        """所有启用类型（下拉框用）。"""
        return list(db.scalars(self.build_query(status=1)).all())

    def count_assets(self, db: Session, type_id: int, include_deleted: bool = False) -> int:
        """
        统计该类型下的设备数。

        :param include_deleted: 是否把软删除的设备也算进来。

        为什么要区分：设备是软删除的（``is_deleted=1``），物理行仍在 ``asset`` 表里，
        外键 ``asset.type_id -> asset_type.id`` 是 RESTRICT，所以光看「未删除数量」
        会误判为可以删类型，最后被数据库外键拒绝（500）。删除类型前必须用
        ``include_deleted=True`` 做真正的占用检查。
        """
        stmt = select(func.count()).select_from(Asset).where(Asset.type_id == type_id)
        if not include_deleted:
            stmt = stmt.where(Asset.is_deleted == 0)
        return int(db.scalar(stmt) or 0)

    def count_assets_batch(self, db: Session, type_ids: List[int]) -> Dict[int, int]:
        """
        批量统计各类型设备数，避免列表页 N+1。

        :return: {type_id: count}
        """
        if not type_ids:
            return {}
        stmt = (
            select(Asset.type_id, func.count(Asset.id))
            .where(Asset.type_id.in_(type_ids), Asset.is_deleted == 0)
            .group_by(Asset.type_id)
        )
        return {int(tid): int(cnt) for tid, cnt in db.execute(stmt).all()}


asset_type_crud = CRUDAssetType(AssetType)
