"""
站内信 / 邮件日志 / 提醒中心接口（阶段四）。

权限：
- ``/notifications`` 普通用户只能操作自己的站内信；群发接口仅管理员；
- ``/mails``、``/reminders`` 的日志与手动触发均为管理员；
- ``GET /reminders/today`` 普通用户看自己的今日提醒统计。
"""

from datetime import date
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentAdmin, CurrentUser, DbSession
from app.core.config import settings
from app.core.exceptions import forbidden, not_found
from app.core.response import ApiResponse, ok, ok_page
from app.crud.notification import mail_log_crud, notification_crud, remind_log_crud
from app.models.notification import (
    MAIL_MAX_RETRY,
    MailLog,
    Notification,
)
from app.schemas.common import PageOut
from app.schemas.notification import (
    MailLogDetailOut,
    MailLogOut,
    MailLogQuery,
    MailRetryOut,
    NotificationOut,
    NotificationQuery,
    NotificationSendIn,
    RemindLogOut,
    RemindLogQuery,
    RemindRunIn,
    RemindRunOut,
)
from app.services import mail as mail_service
from app.services import notification as notify_service
from app.services import reminder as reminder_service
from app.services import scheduler as scheduler_service

router = APIRouter(tags=["13-消息与提醒"])


# ============================================================
# 站内信
# ============================================================
@router.get(
    "/notifications",
    response_model=ApiResponse[PageOut[NotificationOut]],
    summary="我的站内信",
)
def list_my_notifications(
    query: Annotated[NotificationQuery, Depends()],
    db: DbSession,
    current_user: CurrentUser,
) -> dict:
    """当前登录用户自己的站内信。"""
    stmt = notification_crud.build_query(query, user_id=current_user.id)
    items, total = notification_crud.paginate(db, stmt, query.page, query.page_size)
    return ok_page(
        [NotificationOut.model_validate(n) for n in items], total, query.page, query.page_size
    )


@router.get("/notifications/unread-count", response_model=ApiResponse[dict], summary="未读数与分类统计")
def notification_unread_count(db: DbSession, current_user: CurrentUser) -> dict:
    """前端顶部铃铛角标。"""
    by_type = dict(notification_crud.count_by_type(db, current_user.id))
    return ok(
        {
            "unread": notification_crud.count_unread(db, current_user.id),
            "by_type": {
                "system": by_type.get(1, 0),
                "approval": by_type.get(2, 0),
                "remind": by_type.get(3, 0),
            },
        }
    )


@router.get("/notifications/recent", response_model=ApiResponse[List[NotificationOut]], summary="最近站内信")
def recent_notifications(
    db: DbSession,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> dict:
    """下拉面板用：最近 N 条。"""
    query = NotificationQuery(page=1, page_size=limit)
    stmt = notification_crud.build_query(query, user_id=current_user.id)
    items, _ = notification_crud.paginate(db, stmt, 1, limit)
    return ok([NotificationOut.model_validate(n) for n in items])


@router.put("/notifications/read-all", response_model=ApiResponse[dict], summary="全部标记已读")
def read_all_notifications(db: DbSession, current_user: CurrentUser) -> dict:
    affected = notify_service.mark_all_read(db, current_user.id)
    return ok({"affected": affected}, message=f"已将 {affected} 条消息标记为已读")


@router.post("/notifications/send", response_model=ApiResponse[dict], summary="发送站内信（管理员）")
def send_notification(
    payload: NotificationSendIn, db: DbSession, current_admin: CurrentAdmin
) -> dict:
    """管理员向指定用户群发站内信。"""
    items = notify_service.notify_many(
        db,
        payload.user_ids,
        title=payload.title,
        content=payload.content,
        notify_type=1,  # 系统通知
    )
    return ok({"sent": len(items)}, message=f"已发送 {len(items)} 条站内信")


@router.get("/notifications/{notification_id}", response_model=ApiResponse[NotificationOut], summary="站内信详情")
def get_notification(notification_id: int, db: DbSession, current_user: CurrentUser) -> dict:
    item = db.get(Notification, notification_id)
    if item is None:
        raise not_found(f"站内信不存在（id={notification_id}）")
    if not current_user.is_admin and item.user_id != current_user.id:
        raise forbidden("无权查看他人的站内信")
    return ok(NotificationOut.model_validate(item))


@router.put("/notifications/{notification_id}/read", response_model=ApiResponse[NotificationOut], summary="标记已读")
def read_notification(notification_id: int, db: DbSession, current_user: CurrentUser) -> dict:
    item = notification_crud.get_owned(db, notification_id, current_user.id)
    if item is None:
        # 管理员也可能点开别人的，再兜底查一次
        item = db.get(Notification, notification_id)
        if item is None or not current_user.is_admin:
            raise not_found(f"站内信不存在（id={notification_id}）")
    notify_service.mark_read(db, item)
    return ok(NotificationOut.model_validate(item))


@router.delete("/notifications/{notification_id}", response_model=ApiResponse[dict], summary="删除站内信")
def delete_notification(notification_id: int, db: DbSession, current_user: CurrentUser) -> dict:
    item = notification_crud.get_owned(db, notification_id, current_user.id)
    if item is None:
        raise not_found(f"站内信不存在（id={notification_id}）")
    db.delete(item)
    db.commit()
    return ok({"id": notification_id}, message="已删除")


# ============================================================
# 到期提醒
# ============================================================
@router.get("/reminders/today", response_model=ApiResponse[dict], summary="我的今日提醒概览")
def remind_today(
    db: DbSession,
    current_user: CurrentUser,
    scope: Annotated[str, Query(pattern="^(mine|all)$", description="mine=只看自己 all=全量(管理员)")] = "mine",
) -> dict:
    """
    今日提醒概览。

    普通用户固定只看自己；管理员可传 ``scope=all`` 看全量。
    """
    if scope == "all" and not current_user.is_admin:
        raise forbidden("只有管理员可以查看全量统计")

    if scope == "mine":
        today = date.today()
        query = RemindLogQuery(page=1, page_size=1, user_id=current_user.id, start_date=today, end_date=today)
        stmt = remind_log_crud.build_query(query)
        _, total = remind_log_crud.paginate(db, stmt, 1, 1)
        unread = notify_service.unread_count(db, current_user.id)
        return ok(
            {
                "scope": "mine",
                "remind_date": today.isoformat(),
                "total": total,
                "unread_notifications": unread,
                "days_before": settings.REMIND_DAYS_BEFORE,
            }
        )

    data = remind_log_crud.today_summary(db)
    data["scope"] = "all"
    data["scheduler"] = scheduler_service.scheduler_status()
    return ok(data)


@router.get("/reminders/expiring", response_model=ApiResponse[List[dict]], summary="即将到期/已超期清单")
def list_expiring(
    db: DbSession,
    current_user: CurrentUser,
    days: Annotated[Optional[int], Query(ge=0, le=365, description="提前天数，缺省用配置值")] = None,
) -> dict:
    """
    到期清单预览。

    普通用户只看自己的设备，管理员看全部（手动触发提醒前先预览）。
    """
    days = settings.REMIND_DAYS_BEFORE if days is None else days
    items = reminder_service.collect_expiring_assets(db, days=days)

    result = []
    for item in items:
        asset = item["asset"]
        if not current_user.is_admin and asset.borrower_id != current_user.id:
            continue
        result.append(
            {
                "asset_id": asset.id,
                "asset_no": asset.asset_no,
                "name": asset.name,
                "borrower_id": asset.borrower_id,
                "borrower_name": asset.borrower_name,
                "expire_at": asset.expire_at.isoformat() if asset.expire_at else None,
                "days_left": item["days_left"],
                "remind_type": item["remind_type"],
                "remind_type_label": "已超期" if item["days_left"] < 0 else "即将到期",
            }
        )
    return ok(result)


@router.post("/reminders/run", response_model=ApiResponse[RemindRunOut], summary="手动执行到期提醒（管理员）")
def run_reminders(payload: RemindRunIn, db: DbSession, current_admin: CurrentAdmin) -> dict:
    """
    立即执行一次到期提醒扫描（演示 / 补跑用）。

    幂等：同一天已经提醒过的设备不会重复发送。
    """
    result = reminder_service.run_due_reminders(db, days=payload.days)
    message = (
        f"扫描完成：候选 {result['candidates']} 台，"
        f"站内信 {result['site_messages']} 条，邮件 {result['mails']} 封，"
        f"跳过 {result['skipped']} 条（今日已提醒）"
    )
    return ok(RemindRunOut(**result), message=message)


@router.get("/reminders/logs", response_model=ApiResponse[PageOut[RemindLogOut]], summary="提醒日志（管理员）")
def list_remind_logs(
    query: Annotated[RemindLogQuery, Depends()], db: DbSession, _: CurrentAdmin
) -> dict:
    stmt = remind_log_crud.build_query(query)
    items, total = remind_log_crud.paginate(db, stmt, query.page, query.page_size)
    return ok_page([RemindLogOut.model_validate(r) for r in items], total, query.page, query.page_size)


# ============================================================
# 邮件日志
# ============================================================
@router.get("/mails/logs", response_model=ApiResponse[PageOut[MailLogOut]], summary="邮件日志（管理员）")
def list_mail_logs(query: Annotated[MailLogQuery, Depends()], db: DbSession, _: CurrentAdmin) -> dict:
    stmt = mail_log_crud.build_query(query)
    items, total = mail_log_crud.paginate(db, stmt, query.page, query.page_size)
    return ok_page([MailLogOut.model_validate(m) for m in items], total, query.page, query.page_size)


@router.get("/mails/stats", response_model=ApiResponse[dict], summary="邮件发送统计（管理员）")
def mail_stats(db: DbSession, _: CurrentAdmin) -> dict:
    data = mail_log_crud.stats(db)
    data["max_retry"] = MAIL_MAX_RETRY
    return ok(data)


@router.get("/mails/{mail_id}", response_model=ApiResponse[MailLogDetailOut], summary="邮件详情（管理员）")
def get_mail_log(mail_id: int, db: DbSession, _: CurrentAdmin) -> dict:
    item = db.get(MailLog, mail_id)
    if item is None:
        raise not_found(f"邮件日志不存在（id={mail_id}）")
    return ok(MailLogDetailOut.model_validate(item))


@router.post("/mails/{mail_id}/resend", response_model=ApiResponse[MailLogOut], summary="重发邮件（管理员）")
def resend_mail(mail_id: int, db: DbSession, _: CurrentAdmin) -> dict:
    item = db.get(MailLog, mail_id)
    if item is None:
        raise not_found(f"邮件日志不存在（id={mail_id}）")
    mail_service.resend_mail(db, item)
    return ok(MailLogOut.model_validate(item), message="重发完成")


@router.post("/mails/retry-failed", response_model=ApiResponse[MailRetryOut], summary="批量重试失败邮件（管理员）")
def retry_failed_mails(db: DbSession, _: CurrentAdmin) -> dict:
    result = reminder_service.retry_failed_mails(db)
    return ok(MailRetryOut(**result), message=f"已重试 {result['scanned']} 封，成功 {result['resent']} 封")


# ============================================================
# 调度器状态
# ============================================================
@router.get("/system/scheduler", response_model=ApiResponse[dict], summary="定时任务状态（管理员）")
def scheduler_status(_: CurrentAdmin) -> dict:
    return ok(scheduler_service.scheduler_status())
