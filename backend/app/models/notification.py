"""
站内信、邮件日志、提醒日志模型（阶段四）。

表：
- ``notification``  站内信（管理员通知 / 审批结果 / 到期提醒）
- ``mail_log``      邮件发送日志（含重试次数与失败原因，便于排查与补发）
- ``remind_log``    到期提醒日志（**幂等表**：同一天同一设备同一用户同一渠道只提醒一次）

设计要点：
1. 到期提醒是「每天扫描一次 + 逐条落库」的模式，必须防重复：
   ``remind_log`` 上建唯一键 ``(asset_id, apply_id, user_id, remind_type, remind_date, channel)``，
   即使定时任务被重复触发（重启、手动执行）也不会给用户重复发消息；
2. 邮件失败不影响站内信：站内信先落库，邮件单独记录状态并支持重试；
3. ``mail_log`` 保留邮件正文，方便开发环境下直接查看「本应发出的邮件」。
"""

from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import MYSQL_TABLE_ARGS, Base
from app.models.mixins import BigIntFK, BigIntPK, TimestampMixin

if TYPE_CHECKING:  # pragma: no cover
    from app.models.asset import Asset
    from app.models.user import SysUser

# ==================== 站内信类型 ====================
NOTIFY_TYPE_SYSTEM = 1  # 系统通知
NOTIFY_TYPE_APPROVAL = 2  # 审批结果
NOTIFY_TYPE_REMIND = 3  # 到期提醒

NOTIFY_TYPE_LABELS = {
    NOTIFY_TYPE_SYSTEM: "系统通知",
    NOTIFY_TYPE_APPROVAL: "审批结果",
    NOTIFY_TYPE_REMIND: "到期提醒",
}

# 站内信是否已读
NOTIFY_UNREAD = 0
NOTIFY_READ = 1

# ==================== 邮件状态 ====================
MAIL_STATUS_PENDING = 0  # 待发送
MAIL_STATUS_SENT = 1  # 发送成功
MAIL_STATUS_FAILED = 2  # 发送失败

MAIL_STATUS_LABELS = {
    MAIL_STATUS_PENDING: "待发送",
    MAIL_STATUS_SENT: "已发送",
    MAIL_STATUS_FAILED: "发送失败",
}

# 邮件最大重试次数
MAIL_MAX_RETRY = 3

# ==================== 提醒类型 ====================
REMIND_TYPE_DUE_SOON = 1  # 即将到期
REMIND_TYPE_OVERDUE = 2  # 已超期

REMIND_TYPE_LABELS = {
    REMIND_TYPE_DUE_SOON: "即将到期",
    REMIND_TYPE_OVERDUE: "已超期",
}

# ==================== 提醒渠道 ====================
CHANNEL_SITE = 1  # 站内信
CHANNEL_MAIL = 2  # 邮件

CHANNEL_LABELS = {
    CHANNEL_SITE: "站内信",
    CHANNEL_MAIL: "邮件",
}

# ==================== 提醒状态 ====================
REMIND_STATUS_SUCCESS = 1
REMIND_STATUS_FAILED = 2

REMIND_STATUS_LABELS = {
    REMIND_STATUS_SUCCESS: "成功",
    REMIND_STATUS_FAILED: "失败",
}


class Notification(Base, TimestampMixin):
    """站内信。"""

    __tablename__ = "notification"
    __table_args__ = (
        Index("ix_notification_user_read", "user_id", "is_read"),
        dict(MYSQL_TABLE_ARGS),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True, comment="站内信ID")
    user_id: Mapped[int] = mapped_column(
        BigIntFK,
        ForeignKey("sys_user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="接收人ID",
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False, comment="标题")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="正文")
    notify_type: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=NOTIFY_TYPE_SYSTEM,
        server_default=text("1"),
        index=True,
        comment="类型：1=系统通知 2=审批结果 3=到期提醒",
    )
    # 关联业务对象，便于前端「点击跳转到对应设备/申请单」
    biz_type: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("''"), comment="业务类型：asset / borrow_apply"
    )
    biz_id: Mapped[Optional[int]] = mapped_column(BigIntFK, nullable=True, comment="业务对象ID")

    is_read: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=NOTIFY_UNREAD,
        server_default=text("0"),
        index=True,
        comment="是否已读：0=未读 1=已读",
    )
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="阅读时间")

    user: Mapped["SysUser"] = relationship(lazy="selectin")

    @property
    def type_label(self) -> str:
        return NOTIFY_TYPE_LABELS.get(self.notify_type, "未知")

    @property
    def is_unread(self) -> bool:
        return self.is_read == NOTIFY_UNREAD

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Notification id={self.id} user_id={self.user_id} read={self.is_read}>"


class MailLog(Base, TimestampMixin):
    """邮件发送日志。"""

    __tablename__ = "mail_log"
    __table_args__ = (
        Index("ix_mail_log_status_retry", "status", "retry_count"),
        dict(MYSQL_TABLE_ARGS),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True, comment="日志ID")
    to_email: Mapped[str] = mapped_column(String(120), nullable=False, index=True, comment="收件人邮箱")
    to_user_id: Mapped[Optional[int]] = mapped_column(
        BigIntFK,
        ForeignKey("sys_user.id", ondelete="SET NULL"),
        nullable=True,
        comment="收件人用户ID",
    )
    subject: Mapped[str] = mapped_column(String(200), nullable=False, comment="邮件主题")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="邮件正文（HTML）")

    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=MAIL_STATUS_PENDING,
        server_default=text("0"),
        index=True,
        comment="状态：0=待发送 1=已发送 2=发送失败",
    )
    error: Mapped[str] = mapped_column(
        String(500), nullable=False, server_default=text("''"), comment="失败原因"
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0"), comment="重试次数"
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, comment="发送时间")

    biz_type: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default=text("''"), comment="业务类型：remind / approval / test"
    )
    biz_id: Mapped[Optional[int]] = mapped_column(BigIntFK, nullable=True, comment="业务对象ID")

    @property
    def status_label(self) -> str:
        return MAIL_STATUS_LABELS.get(self.status, "未知")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MailLog id={self.id} to={self.to_email!r} status={self.status}>"


class RemindLog(Base, TimestampMixin):
    """
    到期提醒日志（幂等）。

    唯一键保证：同一设备 + 同一领用单 + 同一用户 + 同一提醒类型 + 同一天 + 同一渠道
    只会产生一条记录，从根上避免定时任务重复触发导致用户被反复打扰。
    """

    __tablename__ = "remind_log"
    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "apply_id",
            "user_id",
            "remind_type",
            "remind_date",
            "channel",
            name="uq_remind_log_dedup",
        ),
        Index("ix_remind_log_date_type", "remind_date", "remind_type"),
        dict(MYSQL_TABLE_ARGS),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True, comment="日志ID")
    # 允许为空：asset_id 为 NULL 的行表示「管理员日常汇总」提醒，不对应具体设备
    asset_id: Mapped[Optional[int]] = mapped_column(
        BigIntFK,
        ForeignKey("asset.id", ondelete="CASCADE"),
        nullable=True,
        comment="设备ID；NULL 表示管理员汇总提醒",
    )
    apply_id: Mapped[Optional[int]] = mapped_column(
        BigIntFK,
        ForeignKey("borrow_apply.id", ondelete="CASCADE"),
        nullable=True,
        comment="关联领用单ID",
    )
    user_id: Mapped[int] = mapped_column(
        BigIntFK,
        ForeignKey("sys_user.id", ondelete="CASCADE"),
        nullable=False,
        comment="被提醒人ID",
    )

    remind_type: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, comment="类型：1=即将到期 2=已超期"
    )
    remind_date: Mapped[date] = mapped_column(Date, nullable=False, comment="提醒日期（幂等键）")
    channel: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, comment="渠道：1=站内信 2=邮件"
    )
    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=REMIND_STATUS_SUCCESS,
        server_default=text("1"),
        comment="结果：1=成功 2=失败",
    )
    message: Mapped[str] = mapped_column(
        String(500), nullable=False, server_default=text("''"), comment="提醒内容摘要 / 失败原因"
    )
    days_left: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="距到期天数（负数表示已超期）"
    )

    asset: Mapped["Asset"] = relationship(lazy="selectin")
    user: Mapped["SysUser"] = relationship(lazy="selectin")

    @property
    def type_label(self) -> str:
        return REMIND_TYPE_LABELS.get(self.remind_type, "未知")

    @property
    def channel_label(self) -> str:
        return CHANNEL_LABELS.get(self.channel, "未知")

    @property
    def status_label(self) -> str:
        return REMIND_STATUS_LABELS.get(self.status, "未知")

    @property
    def user_name(self) -> str:
        if self.user is None:
            return ""
        return self.user.real_name or self.user.username

    @property
    def asset_no(self) -> str:
        return self.asset.asset_no if self.asset is not None else ""

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<RemindLog id={self.id} asset_id={self.asset_id} "
            f"type={self.remind_type} date={self.remind_date}>"
        )
