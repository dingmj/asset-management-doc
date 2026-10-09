"""
邮件发送服务。

两种模式：
1. **真实投递**（``MAIL_ENABLED=true``）：走 SMTP（fastapi-mail / aiosmtplib）。
   由于 fastapi-mail 是异步库，这里用 ``asyncio.run`` 包一层，让同步业务代码
   可以像调用普通函数一样发邮件。
2. **开发模拟**（``MAIL_ENABLED=false``，默认）：不投递，但
   - 依然写 ``mail_log``（状态=已发送，error 里注明「开发模式未真实投递」）；
   - 把邮件正文落盘到 ``logs/mail/<时间>_<收件人>_<主题>.html``，
     方便本地直接打开查看「应发出的邮件」。

无论哪种模式，发送结果都会落到 ``mail_log``，失败时记录原因并支持重试。
"""

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.core.config import BASE_DIR, settings
from app.core.logging import get_logger
from app.models.notification import (
    MAIL_STATUS_FAILED,
    MAIL_STATUS_SENT,
    MailLog,
)

logger = get_logger(__name__)

# 文件名里不能出现的字符
_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|\s]+')


def _safe_filename(text: str, limit: int = 40) -> str:
    """把主题转成安全的文件名片段。"""
    cleaned = _UNSAFE_FILENAME.sub("_", text.strip())
    return (cleaned or "mail")[:limit]


def _dump_to_file(to_email: str, subject: str, html: str) -> Optional[str]:
    """把邮件正文写到 logs/mail/，返回落盘路径（失败返回 None）。"""
    try:
        dump_dir: Path = BASE_DIR / settings.MAIL_DUMP_DIR
        dump_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        path = dump_dir / f"{stamp}_{_safe_filename(to_email, 30)}_{_safe_filename(subject)}.html"
        path.write_text(html, encoding="utf-8")
        return str(path)
    except OSError as exc:  # 磁盘/权限问题不应阻断业务
        logger.warning("邮件正文落盘失败：%s", exc)
        return None


async def _send_via_smtp(to_email: str, subject: str, html: str) -> None:
    """真实投递（异步）。"""
    from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType

    conf = ConnectionConfig(
        MAIL_USERNAME=settings.MAIL_USERNAME,
        MAIL_PASSWORD=settings.MAIL_PASSWORD,
        MAIL_FROM=settings.MAIL_FROM,
        MAIL_FROM_NAME=settings.MAIL_FROM_NAME,
        MAIL_PORT=settings.MAIL_PORT,
        MAIL_SERVER=settings.MAIL_HOST,
        MAIL_STARTTLS=not settings.MAIL_USE_SSL,
        MAIL_SSL_TLS=settings.MAIL_USE_SSL,
        USE_CREDENTIALS=bool(settings.MAIL_USERNAME),
        VALIDATE_CERTS=True,
        TIMEOUT=settings.MAIL_TIMEOUT,
    )
    message = MessageSchema(
        subject=subject,
        recipients=[to_email],
        body=html,
        subtype=MessageType.html,
    )
    await FastMail(conf).send_message(message)


def send_mail(
    db: Session,
    to_email: str,
    subject: str,
    html: str,
    *,
    to_user_id: Optional[int] = None,
    biz_type: str = "",
    biz_id: Optional[int] = None,
    commit: bool = True,
) -> Tuple[MailLog, bool]:
    """
    发送一封邮件并记录日志。

    :return: (MailLog 实例, 是否成功)
    """
    log = MailLog(
        to_email=(to_email or "").strip(),
        to_user_id=to_user_id,
        subject=subject,
        content=html,
        biz_type=biz_type,
        biz_id=biz_id,
    )

    if not log.to_email:
        log.status = MAIL_STATUS_FAILED
        log.error = "收件人邮箱为空"
    elif not settings.MAIL_ENABLED:
        # 开发模拟模式：落盘 + 标记成功，并注明未真实投递
        path = _dump_to_file(log.to_email, subject, html)
        log.status = MAIL_STATUS_SENT
        log.sent_at = datetime.now()
        log.error = f"开发模式未真实投递（MAIL_ENABLED=false），正文已保存：{path or '落盘失败'}"
        logger.info("[模拟发信] to=%s subject=%s", log.to_email, subject)
    else:
        try:
            asyncio.run(_send_via_smtp(log.to_email, subject, html))
            log.status = MAIL_STATUS_SENT
            log.sent_at = datetime.now()
        except Exception as exc:  # noqa: BLE001 - 任何 SMTP 异常都要落库，不能影响主流程
            log.status = MAIL_STATUS_FAILED
            log.error = f"{type(exc).__name__}: {exc}"[:500]
            logger.warning("邮件发送失败 to=%s：%s", log.to_email, log.error)

    db.add(log)
    if commit:
        db.commit()
        db.refresh(log)
    else:
        db.flush()

    return log, log.status == MAIL_STATUS_SENT


def resend_mail(db: Session, log: MailLog, commit: bool = True) -> MailLog:
    """
    重发一封历史邮件（管理员手动补发 / 定时任务自动重试）。

    复用原日志行：更新状态与重试次数，不新建记录，避免日志表膨胀。
    """
    log.retry_count += 1

    if not settings.MAIL_ENABLED:
        path = _dump_to_file(log.to_email, log.subject, log.content)
        log.status = MAIL_STATUS_SENT
        log.sent_at = datetime.now()
        log.error = f"开发模式未真实投递（MAIL_ENABLED=false），正文已保存：{path or '落盘失败'}"
    else:
        try:
            asyncio.run(_send_via_smtp(log.to_email, log.subject, log.content))
            log.status = MAIL_STATUS_SENT
            log.sent_at = datetime.now()
            log.error = ""
        except Exception as exc:  # noqa: BLE001
            log.status = MAIL_STATUS_FAILED
            log.error = f"{type(exc).__name__}: {exc}"[:500]

    db.add(log)
    if commit:
        db.commit()
        db.refresh(log)
    else:
        db.flush()
    return log
