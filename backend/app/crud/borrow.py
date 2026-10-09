"""领用申请 / 审批 CRUD。"""

from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.models.asset import Asset
from app.models.borrow import (
    APPLY_NO_PREFIX,
    APPLY_STATUS_APPROVED,
    APPLY_STATUS_PENDING,
    APPLY_TYPE_BORROW,
    ApprovalRecord,
    BorrowApply,
)
from app.models.user import SysUser
from app.schemas.borrow import BorrowQuery

# 允许排序的字段白名单（防 ORDER BY 注入）
SORTABLE_FIELDS = {"id", "created_at", "expect_expire_at", "approved_at", "returned_at"}


class CRUDBorrow(CRUDBase[BorrowApply]):
    """领用申请数据访问层。"""

    # ---------------- 单号 ----------------
    def generate_apply_no(self, db: Session, apply_type: int) -> str:
        """
        生成申请单号：``前缀 + YYYYMMDD + 4 位当日流水``。

        例如 ``BY202609240001``。并发下极小概率重复，调用方需要捕获
        IntegrityError 后重试（见 ``app.api.v1.borrow._create_apply``）。
        """
        prefix = APPLY_NO_PREFIX.get(apply_type, "BY")
        today = datetime.now().strftime("%Y%m%d")
        like = f"{prefix}{today}%"

        last = db.scalars(
            select(BorrowApply.apply_no)
            .where(BorrowApply.apply_no.like(like))
            .order_by(BorrowApply.apply_no.desc())
            .limit(1)
        ).first()
        seq = int(last[-4:]) + 1 if last else 1
        return f"{prefix}{today}{seq:04d}"

    # ---------------- 查询 ----------------
    def get_by_no(self, db: Session, apply_no: str) -> Optional[BorrowApply]:
        return db.scalars(select(BorrowApply).where(BorrowApply.apply_no == apply_no).limit(1)).first()

    def get_for_update(self, db: Session, apply_id: int) -> Optional[BorrowApply]:
        """加行锁读取申请单，避免同一张单被并发审批两次。"""
        return db.scalars(
            select(BorrowApply).where(BorrowApply.id == apply_id).with_for_update()
        ).first()

    def build_query(
        self,
        query: BorrowQuery,
        *,
        applicant_id: Optional[int] = None,
        status_list: Optional[Sequence[int]] = None,
    ) -> Select:
        """
        拼装查询语句。

        :param applicant_id: 强制限定申请人（普通用户视图传自己的 ID）
        :param status_list: 状态多选（由接口层显式声明为 Query 参数）
        """
        stmt = select(BorrowApply)

        if applicant_id is not None:
            stmt = stmt.where(BorrowApply.applicant_id == applicant_id)

        if query.status is not None:
            stmt = stmt.where(BorrowApply.status == query.status)
        elif status_list:
            stmt = stmt.where(BorrowApply.status.in_(list(status_list)))
        if query.only_pending:
            stmt = stmt.where(BorrowApply.status == APPLY_STATUS_PENDING)

        if query.apply_type is not None:
            stmt = stmt.where(BorrowApply.apply_type == query.apply_type)
        if query.asset_id is not None:
            stmt = stmt.where(BorrowApply.asset_id == query.asset_id)
        if query.applicant_id is not None:
            stmt = stmt.where(BorrowApply.applicant_id == query.applicant_id)

        if query.only_unreturned:
            stmt = stmt.where(
                BorrowApply.status == APPLY_STATUS_APPROVED,
                BorrowApply.apply_type == APPLY_TYPE_BORROW,
                BorrowApply.returned_at.is_(None),
            )

        if query.created_start is not None:
            stmt = stmt.where(BorrowApply.created_at >= query.created_start)
        if query.created_end is not None:
            stmt = stmt.where(BorrowApply.created_at <= query.created_end)

        # ---------- 关键字：单号 / 设备 / 申请人 ----------
        if query.keyword:
            kw = f"%{query.keyword.strip()}%"
            asset_exists = (
                select(Asset.id)
                .where(
                    Asset.id == BorrowApply.asset_id,
                    or_(Asset.asset_no.like(kw), Asset.name.like(kw)),
                )
                .exists()
            )
            user_exists = (
                select(SysUser.id)
                .where(
                    SysUser.id == BorrowApply.applicant_id,
                    or_(SysUser.username.like(kw), SysUser.real_name.like(kw)),
                )
                .exists()
            )
            stmt = stmt.where(or_(BorrowApply.apply_no.like(kw), asset_exists, user_exists))

        # ---------- 按设备类型 ----------
        if query.asset_type_id is not None:
            type_exists = (
                select(Asset.id)
                .where(Asset.id == BorrowApply.asset_id, Asset.type_id == query.asset_type_id)
                .exists()
            )
            stmt = stmt.where(type_exists)

        return stmt.order_by(*self._order_clauses(query))

    @staticmethod
    def _order_clauses(query: BorrowQuery):
        column_name = query.order_by if query.order_by in SORTABLE_FIELDS else "id"
        column = getattr(BorrowApply, column_name)
        return [column.desc() if query.order_desc else column.asc(), BorrowApply.id.desc()]

    # ---------------- 业务校验辅助 ----------------
    def count_pending_by_asset(self, db: Session, asset_id: int) -> int:
        """该设备当前待审批的申请数（同一设备同时只允许一条待审批单）。"""
        stmt = (
            select(func.count())
            .select_from(BorrowApply)
            .where(BorrowApply.asset_id == asset_id, BorrowApply.status == APPLY_STATUS_PENDING)
        )
        return int(db.scalar(stmt) or 0)

    def get_active_borrow(self, db: Session, asset_id: int) -> Optional[BorrowApply]:
        """取该设备当前「已通过且未归还」的领用单。"""
        stmt = (
            select(BorrowApply)
            .where(
                BorrowApply.asset_id == asset_id,
                BorrowApply.status == APPLY_STATUS_APPROVED,
                BorrowApply.apply_type == APPLY_TYPE_BORROW,
                BorrowApply.returned_at.is_(None),
            )
            .order_by(BorrowApply.id.desc())
            .limit(1)
        )
        return db.scalars(stmt).first()

    def count_pending_renew(self, db: Session, parent_id: int) -> int:
        """该领用单是否已有待审批的续借申请。"""
        stmt = (
            select(func.count())
            .select_from(BorrowApply)
            .where(BorrowApply.parent_id == parent_id, BorrowApply.status == APPLY_STATUS_PENDING)
        )
        return int(db.scalar(stmt) or 0)

    # ---------------- 写入 ----------------
    def add_record(
        self,
        db: Session,
        apply: BorrowApply,
        approver_id: Optional[int],
        action: int,
        remark: str = "",
        commit: bool = False,
    ) -> ApprovalRecord:
        """追加一条审批流水。"""
        record = ApprovalRecord(
            apply_id=apply.id,
            approver_id=approver_id,
            action=action,
            remark=(remark or "").strip(),
        )
        db.add(record)
        if commit:
            db.commit()
            db.refresh(record)
        else:
            db.flush()
        return record

    # ---------------- 统计 ----------------
    def count_by_status(
        self, db: Session, applicant_id: Optional[int] = None
    ) -> List[Tuple[int, int]]:
        """按状态分组计数 -> [(status, count)]。"""
        stmt = select(BorrowApply.status, func.count(BorrowApply.id)).group_by(BorrowApply.status)
        if applicant_id is not None:
            stmt = stmt.where(BorrowApply.applicant_id == applicant_id)
        return [(int(s), int(c)) for s, c in db.execute(stmt).all()]

    def count_by_type(self, db: Session, applicant_id: Optional[int] = None) -> List[Tuple[int, int]]:
        """按申请类型计数 -> [(apply_type, count)]。"""
        stmt = select(BorrowApply.apply_type, func.count(BorrowApply.id)).group_by(
            BorrowApply.apply_type
        )
        if applicant_id is not None:
            stmt = stmt.where(BorrowApply.applicant_id == applicant_id)
        return [(int(t), int(c)) for t, c in db.execute(stmt).all()]

    def count_unreturned(self, db: Session, applicant_id: Optional[int] = None) -> int:
        """统计未归还的已通过领用单数量。"""
        stmt = (
            select(func.count())
            .select_from(BorrowApply)
            .where(
                BorrowApply.status == APPLY_STATUS_APPROVED,
                BorrowApply.apply_type == APPLY_TYPE_BORROW,
                BorrowApply.returned_at.is_(None),
            )
        )
        if applicant_id is not None:
            stmt = stmt.where(BorrowApply.applicant_id == applicant_id)
        return int(db.scalar(stmt) or 0)

    def list_due_soon(self, db: Session, days: int = 0, limit: int = 500) -> List[BorrowApply]:
        """
        取「已通过、未归还、且将在 days 天内到期」的领用单（阶段四到期提醒用）。

        :param days: 0 表示只看已超期
        """
        now = datetime.now()
        stmt = select(BorrowApply).where(
            BorrowApply.status == APPLY_STATUS_APPROVED,
            BorrowApply.apply_type == APPLY_TYPE_BORROW,
            BorrowApply.returned_at.is_(None),
            BorrowApply.expect_expire_at.is_not(None),
        )
        if days > 0:
            stmt = stmt.where(
                BorrowApply.expect_expire_at >= now,
                BorrowApply.expect_expire_at <= now + timedelta(days=days),
            )
        else:
            stmt = stmt.where(BorrowApply.expect_expire_at < now)
        return list(db.scalars(stmt.order_by(BorrowApply.expect_expire_at.asc()).limit(limit)).all())


borrow_crud = CRUDBorrow(BorrowApply)
