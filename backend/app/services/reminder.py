"""
到期提醒服务（阶段四核心）。

业务规则（对应项目文档第 8 条）：
- **管理员**：收到一条「今日到期汇总」站内信；
- **普通用户**：收到自己名下设备的站内信（逐台），并额外收到邮件；
- 提醒范围：``REMIND_DAYS_BEFORE`` 天内即将到期 + 已经超期；
- **幂等**：``remind_log`` 保证同一天同一设备/同一用户/同一渠道只提醒一次，
  定时任务被重复触发（重启、手动执行）都不会造成重复打扰。

同时提供：
- ``retry_failed_mails``：失败邮件自动重试（定时任务调用）。
"""

from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.asset import ASSET_STATUS_BORROWED, Asset
from app.models.notification import (
    CHANNEL_MAIL,
    CHANNEL_SITE,
    MAIL_MAX_RETRY,
    MAIL_STATUS_FAILED,
    NOTIFY_TYPE_REMIND,
    REMIND_STATUS_FAILED,
    REMIND_STATUS_SUCCESS,
    REMIND_TYPE_DUE_SOON,
    REMIND_TYPE_LABELS,
    REMIND_TYPE_OVERDUE,
    MailLog,
    RemindLog,
)
from app.models.user import SysUser
from app.services import mail as mail_service
from app.services import notification as notify_service

logger = get_logger(__name__)


# ============================================================
# 提醒候选集
# ============================================================
def collect_expiring_assets(
    db: Session, days: Optional[int] = None
) -> List[Dict]:
    """
    收集需要提醒的设备。

    :param days: 提前提醒天数，默认取配置 ``REMIND_DAYS_BEFORE``
    :return: [{'asset': Asset, 'remind_type': int, 'days_left': int}, ...]
    """
    days = settings.REMIND_DAYS_BEFORE if days is None else days
    now = datetime.now()
    deadline = now + timedelta(days=days)

    stmt = (
        select(Asset)
        .where(
            Asset.is_deleted == 0,
            Asset.status == ASSET_STATUS_BORROWED,
            Asset.borrower_id.is_not(None),
            Asset.expire_at.is_not(None),
            Asset.expire_at <= deadline,  # 含已超期（expire_at < now）
        )
        .order_by(Asset.expire_at.asc())
    )

    items: List[Dict] = []
    for asset in db.scalars(stmt).all():
        days_left = (asset.expire_at - now).days
        items.append(
            {
                "asset": asset,
                "remind_type": REMIND_TYPE_OVERDUE if days_left < 0 else REMIND_TYPE_DUE_SOON,
                "days_left": days_left,
            }
        )
    return items


# ============================================================
# 幂等判断
# ============================================================
def already_reminded(
    db: Session,
    *,
    today: date,
    user_id: int,
    remind_type: int,
    channel: int,
    asset_id: Optional[int],
    apply_id: Optional[int] = None,
) -> bool:
    """
    今天是否已经就该「设备 + 用户 + 渠道」提醒过。

    注意：MySQL 唯一索引里 NULL 互不相等，所以汇总类记录（asset_id 为 NULL）
    不能依赖唯一键，必须显式查询判断。
    """
    stmt = select(RemindLog.id).where(
        RemindLog.remind_date == today,
        RemindLog.user_id == user_id,
        RemindLog.remind_type == remind_type,
        RemindLog.channel == channel,
    )
    stmt = stmt.where(
        RemindLog.asset_id.is_(None) if asset_id is None else RemindLog.asset_id == asset_id
    )
    if apply_id is not None:
        stmt = stmt.where(RemindLog.apply_id == apply_id)
    return db.scalars(stmt.limit(1)).first() is not None


def _add_remind_log(
    db: Session,
    *,
    asset_id: Optional[int],
    apply_id: Optional[int],
    user_id: int,
    remind_type: int,
    channel: int,
    message: str,
    days_left: Optional[int],
    status: int = REMIND_STATUS_SUCCESS,
) -> RemindLog:
    """写入一条提醒日志。"""
    row = RemindLog(
        asset_id=asset_id,
        apply_id=apply_id,
        user_id=user_id,
        remind_type=remind_type,
        remind_date=date.today(),
        channel=channel,
        status=status,
        message=message[:500],
        days_left=days_left,
    )
    db.add(row)
    return row


# ============================================================
# 邮件正文
# ============================================================
def _render_remind_mail(real_name: str, rows: Sequence[Dict]) -> str:
    """渲染到期提醒邮件（HTML，无第三方模板依赖，保证离线可用）。"""
    trs = []
    for row in rows:
        asset = row["asset"]
        days_left = row["days_left"]
        if days_left < 0:
            status_text = f'<span style="color:#d93025">已超期 {abs(days_left)} 天</span>'
        elif days_left == 0:
            status_text = '<span style="color:#e37400">今天到期</span>'
        else:
            status_text = f'<span style="color:#e37400">还有 {days_left} 天到期</span>'
        trs.append(
            f"<tr>"
            f"<td style='padding:8px;border:1px solid #e0e0e0'>{asset.asset_no}</td>"
            f"<td style='padding:8px;border:1px solid #e0e0e0'>{asset.name}</td>"
            f"<td style='padding:8px;border:1px solid #e0e0e0'>{asset.expire_at:%Y-%m-%d %H:%M}</td>"
            f"<td style='padding:8px;border:1px solid #e0e0e0'>{status_text}</td>"
            f"</tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>设备到期提醒</title></head>
<body style="font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;color:#202124">
  <h2 style="margin:0 0 12px">设备到期提醒</h2>
  <p>{real_name}，您好：</p>
  <p>以下设备即将到期或已经超期，请及时办理<b>续借</b>或<b>归还</b>：</p>
  <table style="border-collapse:collapse;font-size:14px">
    <thead>
      <tr style="background:#f5f5f5">
        <th style='padding:8px;border:1px solid #e0e0e0;text-align:left'>资产编号</th>
        <th style='padding:8px;border:1px solid #e0e0e0;text-align:left'>设备名称</th>
        <th style='padding:8px;border:1px solid #e0e0e0;text-align:left'>到期时间</th>
        <th style='padding:8px;border:1px solid #e0e0e0;text-align:left'>状态</th>
      </tr>
    </thead>
    <tbody>{''.join(trs)}</tbody>
  </table>
  <p style="color:#5f6368;font-size:13px;margin-top:16px">
    本邮件由资产管理系统自动发送，请勿直接回复。
  </p>
</body>
</html>"""


# ============================================================
# 主流程
# ============================================================
def run_due_reminders(db: Session, days: Optional[int] = None) -> Dict:
    """
    执行一次到期提醒扫描。

    步骤：
    1. 找出「N 天内到期 + 已超期」的在借设备；
    2. 按用户分组：普通用户逐台发站内信（+邮件），管理员发一条汇总站内信；
    3. 每条提醒都写 ``remind_log``，已提醒过的自动跳过。

    :return: 执行结果摘要（接口直接返回给前端展示）
    """
    days = settings.REMIND_DAYS_BEFORE if days is None else days
    today = date.today()
    now = datetime.now()

    candidates = collect_expiring_assets(db, days=days)
    result: Dict = {
        "scan_date": today.isoformat(),
        "days_before": days,
        "candidates": len(candidates),
        "due_soon": 0,
        "overdue": 0,
        "site_messages": 0,
        "mails": 0,
        "skipped": 0,
        "admin_summary_sent": 0,
        "details": [],
    }

    if not candidates:
        logger.info("到期提醒扫描完成：没有需要提醒的设备")
        return result

    # ---------- 按领用人分组 ----------
    by_user: Dict[int, List[Dict]] = {}
    for item in candidates:
        asset: Asset = item["asset"]
        if item["remind_type"] == REMIND_TYPE_OVERDUE:
            result["overdue"] += 1
        else:
            result["due_soon"] += 1
        by_user.setdefault(int(asset.borrower_id), []).append(item)

    # ---------- 取用户信息 ----------
    user_ids = list(by_user.keys())
    users = {
        u.id: u
        for u in db.scalars(select(SysUser).where(SysUser.id.in_(user_ids))).all()
    }
    emails = dict(notify_service.emails_of_users(db, user_ids))

    # ---------- 逐用户发送 ----------
    for user_id, items in by_user.items():
        user = users.get(user_id)
        if user is None or user.is_deleted or user.status != 1:
            result["skipped"] += len(items)
            continue

        real_name = user.real_name or user.username
        pending_rows: List[Dict] = []

        for item in items:
            asset: Asset = item["asset"]
            remind_type = item["remind_type"]
            days_left = item["days_left"]

            # ---- 站内信（每人每台设备每天一条） ----
            if already_reminded(
                db,
                today=today,
                user_id=user_id,
                remind_type=remind_type,
                channel=CHANNEL_SITE,
                asset_id=asset.id,
            ):
                result["skipped"] += 1
            else:
                if days_left < 0:
                    summary = f"设备 {asset.asset_no}（{asset.name}）已超期 {abs(days_left)} 天"
                    tip = "请尽快办理归还，或联系管理员说明情况。"
                else:
                    summary = f"设备 {asset.asset_no}（{asset.name}）将在 {days_left} 天后到期"
                    tip = "如需继续使用，请及时提交续借申请。"
                notify_service.notify(
                    db,
                    user_id,
                    title=f"【到期提醒】{asset.asset_no} {REMIND_TYPE_LABELS[remind_type]}",
                    content=(
                        f"{real_name}，您好：\n\n{summary}。\n"
                        f"到期时间：{asset.expire_at:%Y-%m-%d %H:%M}\n\n{tip}"
                    ),
                    notify_type=NOTIFY_TYPE_REMIND,
                    biz_type="asset",
                    biz_id=asset.id,
                    commit=False,
                )
                _add_remind_log(
                    db,
                    asset_id=asset.id,
                    apply_id=None,
                    user_id=user_id,
                    remind_type=remind_type,
                    channel=CHANNEL_SITE,
                    message=summary,
                    days_left=days_left,
                )
                result["site_messages"] += 1

            pending_rows.append(item)

        # ---- 邮件：把该用户今天所有待提醒设备合并成一封 ----
        if settings.REMIND_MAIL_ENABLED and pending_rows and user_id in emails:
            overdue_count = sum(1 for r in pending_rows if r["days_left"] < 0)
            subject = f"【资产管理系统】设备到期提醒（{len(pending_rows)} 台）"
            html = _render_remind_mail(real_name, pending_rows)
            log, ok = mail_service.send_mail(
                db,
                emails[user_id],
                subject,
                html,
                to_user_id=user_id,
                biz_type="remind",
                biz_id=None,
                commit=False,
            )
            # 邮件日志按「用户 + 渠道」每天记一条，避免逐台设备刷屏
            mail_remind_type = REMIND_TYPE_OVERDUE if overdue_count == len(pending_rows) else REMIND_TYPE_DUE_SOON
            if not already_reminded(
                db,
                today=today,
                user_id=user_id,
                remind_type=mail_remind_type,
                channel=CHANNEL_MAIL,
                asset_id=None,
            ):
                _add_remind_log(
                    db,
                    asset_id=None,
                    apply_id=None,
                    user_id=user_id,
                    remind_type=mail_remind_type,
                    channel=CHANNEL_MAIL,
                    message=f"邮件提醒 {len(pending_rows)} 台设备（{'成功' if ok else '失败'}）",
                    days_left=None,
                    status=REMIND_STATUS_SUCCESS if ok else REMIND_STATUS_FAILED,
                )
            if ok:
                result["mails"] += 1
            result["details"].append(
                {
                    "user": real_name,
                    "email": emails[user_id],
                    "assets": [r["asset"].asset_no for r in pending_rows],
                    "mail_sent": ok,
                    "mail_log_id": log.id,
                }
            )

    db.commit()

    # ---------- 管理员汇总 ----------
    if settings.REMIND_TO_ADMIN:
        result["admin_summary_sent"] = _send_admin_summary(db, candidates, days)

    logger.info(
        "到期提醒扫描完成：候选 %d 台（即将到期 %d / 已超期 %d），站内信 %d 条，邮件 %d 封，跳过 %d，管理员汇总 %d",
        result["candidates"],
        result["due_soon"],
        result["overdue"],
        result["site_messages"],
        result["mails"],
        result["skipped"],
        result["admin_summary_sent"],
    )
    return result


def _send_admin_summary(db: Session, candidates: Sequence[Dict], days: int) -> int:
    """给所有管理员发一条「到期汇总」站内信，返回实际发送人数。"""
    today = date.today()
    overdue = [c for c in candidates if c["days_left"] < 0]
    due_soon = [c for c in candidates if c["days_left"] >= 0]
    # 汇总记录的提醒类型按「更紧急的一类」归类
    summary_type = REMIND_TYPE_OVERDUE if overdue else REMIND_TYPE_DUE_SOON

    lines = [
        f"今日到期提醒汇总（{today.isoformat()}）",
        f"统计范围：{days} 天内到期 + 已超期",
        f"即将到期 {len(due_soon)} 台，已超期 {len(overdue)} 台，合计 {len(candidates)} 台。",
        "",
    ]
    if overdue:
        lines.append("【已超期】")
        for c in overdue[:20]:
            a = c["asset"]
            lines.append(f"  · {a.asset_no} {a.name}（{a.borrower_name}，超期 {abs(c['days_left'])} 天）")
        if len(overdue) > 20:
            lines.append(f"  …… 其余 {len(overdue) - 20} 台省略")
        lines.append("")
    if due_soon:
        lines.append("【即将到期】")
        for c in due_soon[:20]:
            a = c["asset"]
            lines.append(f"  · {a.asset_no} {a.name}（{a.borrower_name}，{c['days_left']} 天后到期）")
        if len(due_soon) > 20:
            lines.append(f"  …… 其余 {len(due_soon) - 20} 台省略")

    content = "\n".join(lines)
    sent = 0
    for admin_id in notify_service.admin_user_ids(db):
        if already_reminded(
            db,
            today=today,
            user_id=admin_id,
            remind_type=summary_type,
            channel=CHANNEL_SITE,
            asset_id=None,
        ):
            continue
        notify_service.notify(
            db,
            admin_id,
            title=f"【到期汇总】{len(candidates)} 台设备需要关注",
            content=content,
            notify_type=NOTIFY_TYPE_REMIND,
            biz_type="asset",
            biz_id=None,
            commit=False,
        )
        _add_remind_log(
            db,
            asset_id=None,
            apply_id=None,
            user_id=admin_id,
            remind_type=summary_type,
            channel=CHANNEL_SITE,
            message=f"管理员到期汇总：{len(candidates)} 台",
            days_left=None,
        )
        sent += 1

    db.commit()
    return sent


# ============================================================
# 失败邮件重试
# ============================================================
def retry_failed_mails(db: Session, limit: int = 50) -> Dict:
    """
    重试发送失败的邮件。

    :return: {'scanned': n, 'resent': n, 'still_failed': n}
    """
    stmt = (
        select(MailLog)
        .where(
            MailLog.status == MAIL_STATUS_FAILED,
            MailLog.retry_count < MAIL_MAX_RETRY,
            MailLog.to_email != "",
        )
        .order_by(MailLog.id.asc())
        .limit(limit)
    )
    logs = list(db.scalars(stmt).all())

    resent = 0
    still_failed = 0
    for log in logs:
        mail_service.resend_mail(db, log, commit=False)
        if log.status == MAIL_STATUS_FAILED:
            still_failed += 1
        else:
            resent += 1
    db.commit()

    if logs:
        logger.info("失败邮件重试：扫描 %d 封，成功 %d 封，仍失败 %d 封", len(logs), resent, still_failed)
    return {"scanned": len(logs), "resent": resent, "still_failed": still_failed}
