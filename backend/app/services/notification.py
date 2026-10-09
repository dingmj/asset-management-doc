"""
站内信服务。

约定：
- 所有站内信写入都走 ``notify`` / ``notify_many``，便于统一控制标题长度、类型；
- 站内信**不会因为邮件失败而回滚**，两者相互独立；
- ``biz_type`` / ``biz_id`` 让前端可以「点通知直接跳到对应设备或申请单」。
"""

from datetime import datetime
from typing import Iterable, List, Optional, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.notification import (
    NOTIFY_READ,
    NOTIFY_TYPE_SYSTEM,
    Notification,
)

logger = get_logger(__name__)

# 标题长度上限（与数据库列宽一致）
TITLE_MAX_LEN = 120


def notify(
    db: Session,
    user_id: int,
    title: str,
    content: str,
    *,
    notify_type: int = NOTIFY_TYPE_SYSTEM,
    biz_type: str = "",
    biz_id: Optional[int] = None,
    commit: bool = True,
) -> Notification:
    """给单个用户发一条站内信。"""
    item = Notification(
        user_id=user_id,
        title=title[:TITLE_MAX_LEN],
        content=content,
        notify_type=notify_type,
        biz_type=biz_type,
        biz_id=biz_id,
    )
    db.add(item)
    if commit:
        db.commit()
        db.refresh(item)
    else:
        db.flush()
    return item


def notify_many(
    db: Session,
    user_ids: Iterable[int],
    title: str,
    content: str,
    *,
    notify_type: int = NOTIFY_TYPE_SYSTEM,
    biz_type: str = "",
    biz_id: Optional[int] = None,
    commit: bool = True,
) -> List[Notification]:
    """群发站内信（自动去重）。"""
    items: List[Notification] = []
    for user_id in sorted(set(user_ids)):
        items.append(
            Notification(
                user_id=user_id,
                title=title[:TITLE_MAX_LEN],
                content=content,
                notify_type=notify_type,
                biz_type=biz_type,
                biz_id=biz_id,
            )
        )
    if items:
        db.add_all(items)
        if commit:
            db.commit()
        else:
            db.flush()
    return items


def unread_count(db: Session, user_id: int) -> int:
    """未读站内信数量。"""
    stmt = (
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.is_read == 0)
    )
    return int(db.scalar(stmt) or 0)


def mark_read(db: Session, notification: Notification, commit: bool = True) -> Notification:
    """把一条站内信标记为已读。"""
    if notification.is_read == 0:
        notification.is_read = NOTIFY_READ
        notification.read_at = datetime.now()
        db.add(notification)
        if commit:
            db.commit()
            db.refresh(notification)
        else:
            db.flush()
    return notification


def mark_all_read(db: Session, user_id: int) -> int:
    """一键全部已读，返回受影响条数。"""
    result = db.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.is_read == 0)
        .values(is_read=NOTIFY_READ, read_at=datetime.now())
    )
    db.commit()
    return int(result.rowcount or 0)


def admin_user_ids(db: Session) -> List[int]:
    """取所有启用状态的管理员 ID（用于给管理员推送汇总）。"""
    from app.models.role import ROLE_ADMIN, SysRole, SysUserRole
    from app.models.user import SysUser

    stmt = (
        select(SysUser.id)
        .join(SysUserRole, SysUserRole.user_id == SysUser.id)
        .join(SysRole, SysRole.id == SysUserRole.role_id)
        .where(
            SysRole.code == ROLE_ADMIN,
            SysUser.status == 1,
            SysUser.is_deleted == 0,
        )
    )
    return [int(i) for i in db.scalars(stmt).all()]


def emails_of_users(db: Session, user_ids: Sequence[int]) -> List[tuple]:
    """
    批量取用户的 (id, email)，用于发提醒邮件。

    只返回邮箱非空的启用用户。
    """
    from app.models.user import SysUser

    if not user_ids:
        return []
    stmt = select(SysUser.id, SysUser.email).where(
        SysUser.id.in_(list(user_ids)),
        SysUser.status == 1,
        SysUser.is_deleted == 0,
        SysUser.email != "",
    )
    return [(int(i), str(e)) for i, e in db.execute(stmt).all()]
