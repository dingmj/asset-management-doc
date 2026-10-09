"""站内信 / 邮件日志 / 提醒日志 CRUD。"""

from datetime import date, datetime
from typing import List, Optional, Sequence, Tuple

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.crud.base import CRUDBase
from app.models.notification import (
    CHANNEL_MAIL,
    CHANNEL_SITE,
    MailLog,
    Notification,
    RemindLog,
)
from app.schemas.notification import (
    MailLogQuery,
    NotificationQuery,
    RemindLogQuery,
)


# ============================================================
# 站内信
# ============================================================
class CRUDNotification(CRUDBase[Notification]):
    """站内信数据访问层。"""

    def build_query(
        self, query: NotificationQuery, *, user_id: Optional[int] = None
    ) -> Select:
        """
        拼装站内信查询。

        :param user_id: 强制限定接收人（普通用户只允许看自己的）
        """
        stmt = select(Notification)
        if user_id is not None:
            stmt = stmt.where(Notification.user_id == user_id)
        if query.is_read is not None:
            stmt = stmt.where(Notification.is_read == query.is_read)
        elif query.only_unread:
            stmt = stmt.where(Notification.is_read == 0)
        if query.notify_type is not None:
            stmt = stmt.where(Notification.notify_type == query.notify_type)

        return stmt.order_by(Notification.is_read.asc(), Notification.id.desc())

    def count_unread(self, db: Session, user_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.is_read == 0)
        )
        return int(db.scalar(stmt) or 0)

    def count_by_type(self, db: Session, user_id: int) -> List[Tuple[int, int]]:
        """按类型统计（含未读数），前端角标用。"""
        stmt = (
            select(Notification.notify_type, func.count(Notification.id))
            .where(Notification.user_id == user_id)
            .group_by(Notification.notify_type)
        )
        return [(int(t), int(c)) for t, c in db.execute(stmt).all()]

    def get_owned(self, db: Session, notification_id: int, user_id: int) -> Optional[Notification]:
        """取属于指定用户的站内信（越权防护）。"""
        return db.scalars(
            select(Notification)
            .where(Notification.id == notification_id, Notification.user_id == user_id)
            .limit(1)
        ).first()


notification_crud = CRUDNotification(Notification)


# ============================================================
# 邮件日志
# ============================================================
class CRUDMailLog(CRUDBase[MailLog]):
    """邮件日志数据访问层。"""

    def build_query(self, query: MailLogQuery) -> Select:
        stmt = select(MailLog)
        if query.status is not None:
            stmt = stmt.where(MailLog.status == query.status)
        if query.to_email:
            stmt = stmt.where(MailLog.to_email.like(f"%{query.to_email.strip()}%"))
        if query.biz_type:
            stmt = stmt.where(MailLog.biz_type == query.biz_type)
        return stmt.order_by(MailLog.id.desc())

    def count_by_status(self, db: Session) -> List[Tuple[int, int]]:
        stmt = select(MailLog.status, func.count(MailLog.id)).group_by(MailLog.status)
        return [(int(s), int(c)) for s, c in db.execute(stmt).all()]

    def stats(self, db: Session) -> dict:
        counts = dict(self.count_by_status(db))
        total = sum(counts.values())
        return {
            "total": total,
            "pending": counts.get(0, 0),
            "sent": counts.get(1, 0),
            "failed": counts.get(2, 0),
        }


mail_log_crud = CRUDMailLog(MailLog)


# ============================================================
# 提醒日志
# ============================================================
class CRUDRemindLog(CRUDBase[RemindLog]):
    """提醒日志数据访问层。"""

    def build_query(self, query: RemindLogQuery) -> Select:
        stmt = select(RemindLog)
        if query.remind_type is not None:
            stmt = stmt.where(RemindLog.remind_type == query.remind_type)
        if query.channel is not None:
            stmt = stmt.where(RemindLog.channel == query.channel)
        if query.user_id is not None:
            stmt = stmt.where(RemindLog.user_id == query.user_id)
        if query.start_date is not None:
            stmt = stmt.where(RemindLog.remind_date >= query.start_date)
        if query.end_date is not None:
            stmt = stmt.where(RemindLog.remind_date <= query.end_date)
        return stmt.order_by(RemindLog.id.desc())

    def count_by_type(self, db: Session, remind_date: Optional[date] = None) -> List[Tuple[int, int]]:
        stmt = select(RemindLog.remind_type, func.count(RemindLog.id)).group_by(RemindLog.remind_type)
        if remind_date is not None:
            stmt = stmt.where(RemindLog.remind_date == remind_date)
        return [(int(t), int(c)) for t, c in db.execute(stmt).all()]

    def count_by_channel(self, db: Session, remind_date: Optional[date] = None) -> List[Tuple[int, int]]:
        stmt = select(RemindLog.channel, func.count(RemindLog.id)).group_by(RemindLog.channel)
        if remind_date is not None:
            stmt = stmt.where(RemindLog.remind_date == remind_date)
        return [(int(c), int(n)) for c, n in db.execute(stmt).all()]

    def today_summary(self, db: Session) -> dict:
        """今日提醒概览（前端「提醒中心」用）。"""
        today = date.today()
        by_type = dict(self.count_by_type(db, today))
        by_channel = dict(self.count_by_channel(db, today))
        return {
            "remind_date": today.isoformat(),
            "total": sum(by_type.values()),
            "due_soon": by_type.get(1, 0),
            "overdue": by_type.get(2, 0),
            "site": by_channel.get(CHANNEL_SITE, 0),
            "mail": by_channel.get(CHANNEL_MAIL, 0),
        }

    def recent(self, db: Session, limit: int = 10) -> Sequence[RemindLog]:
        stmt = select(RemindLog).order_by(RemindLog.id.desc()).limit(limit)
        return db.scalars(stmt).all()


remind_log_crud = CRUDRemindLog(RemindLog)
