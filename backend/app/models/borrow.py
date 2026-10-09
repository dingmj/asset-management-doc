"""
领用申请与审批记录模型（阶段三核心）。

表：
- ``borrow_apply``     领用/续借申请单（贯穿「申请 -> 审批 -> 归还」全流程）
- ``approval_record``  审批流水（谁、什么时候、做了什么决定、理由）

设计要点：
1. **一个申请人 + 一台设备 + 一次申请**：同一台设备同时只允许存在一条待审批单，
   避免多人抢同一台设备造成审批时的状态冲突；
2. 归还**不需要审批**（设备管理员通常希望尽快回收），但会写一条
   ``approval_record(action=RETURN_CONFIRM)`` 留痕，并回填
   ``borrow_apply.returned_at``，因此「领用单」天然带有完整的生命周期；
3. 续借通过 ``parent_id`` 指向原始领用单，形成可追溯的续借链；
4. 设备的「谁在用 / 何时到期」始终落在 ``asset`` 表上（见阶段二），
   本表只负责流程与审计。
"""

from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import MYSQL_TABLE_ARGS, Base
from app.models.mixins import BigIntFK, BigIntPK, TimestampMixin

if TYPE_CHECKING:  # pragma: no cover
    from app.models.asset import Asset
    from app.models.user import SysUser

# ==================== 申请类型 ====================
APPLY_TYPE_BORROW = 1  # 领用申请
APPLY_TYPE_RENEW = 2  # 续借申请

APPLY_TYPE_LABELS = {
    APPLY_TYPE_BORROW: "领用申请",
    APPLY_TYPE_RENEW: "续借申请",
}

# ==================== 申请状态 ====================
APPLY_STATUS_PENDING = 0  # 待审批
APPLY_STATUS_APPROVED = 1  # 审批通过
APPLY_STATUS_REJECTED = 2  # 已驳回
APPLY_STATUS_CANCELED = 3  # 已撤销

APPLY_STATUS_LABELS = {
    APPLY_STATUS_PENDING: "待审批",
    APPLY_STATUS_APPROVED: "已通过",
    APPLY_STATUS_REJECTED: "已驳回",
    APPLY_STATUS_CANCELED: "已撤销",
}

# ==================== 审批动作 ====================
ACTION_APPROVE = 1  # 审批通过
ACTION_REJECT = 2  # 审批驳回
ACTION_CANCEL = 3  # 申请人撤销
ACTION_RETURN = 4  # 归还确认

ACTION_LABELS = {
    ACTION_APPROVE: "审批通过",
    ACTION_REJECT: "审批驳回",
    ACTION_CANCEL: "撤销申请",
    ACTION_RETURN: "归还确认",
}

# 单号前缀
APPLY_NO_PREFIX = {
    APPLY_TYPE_BORROW: "BY",
    APPLY_TYPE_RENEW: "XJ",
}


class BorrowApply(Base, TimestampMixin):
    """领用/续借申请单。"""

    __tablename__ = "borrow_apply"
    __table_args__ = (
        Index("ix_borrow_apply_status_type", "status", "apply_type"),
        Index("ix_borrow_apply_asset_status", "asset_id", "status"),
        dict(MYSQL_TABLE_ARGS),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True, comment="申请ID")
    apply_no: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True, comment="申请单号，如 BY202609240001"
    )
    apply_type: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=APPLY_TYPE_BORROW,
        server_default=text("1"),
        comment="类型：1=领用申请 2=续借申请",
    )
    asset_id: Mapped[int] = mapped_column(
        BigIntFK,
        ForeignKey("asset.id", ondelete="RESTRICT"),
        nullable=False,
        comment="申请的设备ID",
    )
    applicant_id: Mapped[int] = mapped_column(
        BigIntFK,
        ForeignKey("sys_user.id", ondelete="RESTRICT"),
        nullable=False,
        comment="申请人ID",
    )

    reason: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=text("''"), comment="申请理由 / 用途"
    )
    borrow_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=7, server_default=text("7"), comment="申请天数（续借为延期天数）"
    )
    expect_expire_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="期望到期时间（审批通过后作为设备到期时间）"
    )

    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=APPLY_STATUS_PENDING,
        server_default=text("0"),
        index=True,
        comment="状态：0=待审批 1=已通过 2=已驳回 3=已撤销",
    )

    approver_id: Mapped[Optional[int]] = mapped_column(
        BigIntFK,
        ForeignKey("sys_user.id", ondelete="SET NULL"),
        nullable=True,
        comment="审批人ID",
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="审批时间")
    approve_remark: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=text("''"), comment="审批意见"
    )

    returned_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, index=True, comment="实际归还时间，NULL 表示未归还"
    )
    return_remark: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=text("''"), comment="归还备注"
    )

    parent_id: Mapped[Optional[int]] = mapped_column(
        BigIntFK,
        ForeignKey("borrow_apply.id", ondelete="SET NULL"),
        nullable=True,
        comment="续借单指向的原始领用单ID",
    )
    remark: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=text("''"), comment="申请人备注"
    )

    # ---------- 关系 ----------
    asset: Mapped["Asset"] = relationship(lazy="selectin")
    applicant: Mapped["SysUser"] = relationship(foreign_keys=[applicant_id], lazy="selectin")
    approver: Mapped[Optional["SysUser"]] = relationship(foreign_keys=[approver_id], lazy="selectin")
    records: Mapped[List["ApprovalRecord"]] = relationship(
        back_populates="apply",
        lazy="selectin",
        order_by="ApprovalRecord.id",
        cascade="all, delete-orphan",
    )

    # ---------- 便捷属性（Pydantic from_attributes 直接读取） ----------
    @property
    def type_label(self) -> str:
        return APPLY_TYPE_LABELS.get(self.apply_type, "未知")

    @property
    def status_label(self) -> str:
        return APPLY_STATUS_LABELS.get(self.status, "未知")

    @property
    def asset_no(self) -> str:
        return self.asset.asset_no if self.asset is not None else ""

    @property
    def asset_name(self) -> str:
        return self.asset.name if self.asset is not None else ""

    @property
    def applicant_name(self) -> str:
        if self.applicant is None:
            return ""
        return self.applicant.real_name or self.applicant.username

    @property
    def approver_name(self) -> str:
        if self.approver is None:
            return ""
        return self.approver.real_name or self.approver.username

    @property
    def is_pending(self) -> bool:
        return self.status == APPLY_STATUS_PENDING

    @property
    def is_returned(self) -> bool:
        return self.returned_at is not None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BorrowApply id={self.id} no={self.apply_no!r} status={self.status}>"


class ApprovalRecord(Base, TimestampMixin):
    """审批流水：一次申请可以有「提交 -> 驳回 -> 重新提交」等多条记录。"""

    __tablename__ = "approval_record"
    __table_args__ = (
        Index("ix_approval_record_apply_action", "apply_id", "action"),
        dict(MYSQL_TABLE_ARGS),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True, comment="记录ID")
    apply_id: Mapped[int] = mapped_column(
        BigIntFK,
        ForeignKey("borrow_apply.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="申请单ID",
    )
    approver_id: Mapped[Optional[int]] = mapped_column(
        BigIntFK,
        ForeignKey("sys_user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="操作人ID（审批人 / 申请人本人撤销 / 归还确认人）",
    )
    action: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, comment="动作：1=通过 2=驳回 3=撤销 4=归还确认"
    )
    remark: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=text("''"), comment="意见 / 备注"
    )

    apply: Mapped["BorrowApply"] = relationship(back_populates="records", lazy="raise")
    approver: Mapped[Optional["SysUser"]] = relationship(lazy="selectin")

    @property
    def action_label(self) -> str:
        return ACTION_LABELS.get(self.action, "未知")

    @property
    def approver_name(self) -> str:
        if self.approver is None:
            return ""
        return self.approver.real_name or self.approver.username

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ApprovalRecord id={self.id} apply_id={self.apply_id} action={self.action}>"
