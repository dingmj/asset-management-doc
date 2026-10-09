"""
APScheduler 定时任务装配。

任务：
1. ``due_reminder``  —— 每天 ``REMIND_CRON_HOUR:REMIND_CRON_MINUTE``（默认 09:00）
   扫描即将到期/已超期的在借设备，给普通用户发站内信+邮件、给管理员发汇总；
2. ``mail_retry``    —— 每 ``MAIL_RETRY_INTERVAL_MINUTES`` 分钟重试一次发送失败的邮件。

设计要点：
- 调度器随 FastAPI 生命周期启动/关闭（见 ``app.main`` 的 lifespan）；
- 每个任务自己开一个独立 Session 并保证关闭，避免使用请求级连接；
- 任务内所有异常都被捕获并记录日志，**绝不能让定时任务把进程带崩**；
- ``SCHEDULER_ENABLED=false`` 时完全不启动，便于 pytest、脚本、CI 使用。
"""

from typing import Any, Dict, Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.services import reminder as reminder_service

logger = get_logger(__name__)

JOB_DUE_REMINDER = "due_reminder"
JOB_MAIL_RETRY = "mail_retry"

_scheduler: Optional[BackgroundScheduler] = None


# ============================================================
# 任务函数
# ============================================================
def job_due_reminder() -> None:
    """定时任务：到期提醒扫描。"""
    db = SessionLocal()
    try:
        logger.info("[定时任务] 开始执行到期提醒扫描")
        result = reminder_service.run_due_reminders(db)
        logger.info(
            "[定时任务] 到期提醒完成：候选 %s 台 / 站内信 %s 条 / 邮件 %s 封 / 跳过 %s",
            result["candidates"],
            result["site_messages"],
            result["mails"],
            result["skipped"],
        )
    except Exception as exc:  # noqa: BLE001 - 定时任务必须吞掉异常
        db.rollback()
        logger.exception("[定时任务] 到期提醒执行失败：%s", exc)
    finally:
        db.close()


def job_mail_retry() -> None:
    """定时任务：重试发送失败的邮件。"""
    db = SessionLocal()
    try:
        result = reminder_service.retry_failed_mails(db)
        if result["scanned"]:
            logger.info("[定时任务] 邮件重试：%s", result)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("[定时任务] 邮件重试执行失败：%s", exc)
    finally:
        db.close()


# ============================================================
# 生命周期
# ============================================================
def start_scheduler() -> Optional[BackgroundScheduler]:
    """启动调度器（幂等）。"""
    global _scheduler

    if not settings.SCHEDULER_ENABLED:
        logger.info("SCHEDULER_ENABLED=false，跳过定时任务启动")
        return None
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    scheduler = BackgroundScheduler(timezone=settings.SCHEDULER_TIMEZONE)

    scheduler.add_job(
        job_due_reminder,
        trigger=CronTrigger(
            hour=settings.REMIND_CRON_HOUR,
            minute=settings.REMIND_CRON_MINUTE,
            timezone=settings.SCHEDULER_TIMEZONE,
        ),
        id=JOB_DUE_REMINDER,
        name="设备到期提醒扫描",
        replace_existing=True,
        max_instances=1,
        coalesce=True,  # 服务重启错过多次触发时只补跑一次
        misfire_grace_time=3600,
    )

    scheduler.add_job(
        job_mail_retry,
        trigger=IntervalTrigger(
            minutes=settings.MAIL_RETRY_INTERVAL_MINUTES,
            timezone=settings.SCHEDULER_TIMEZONE,
        ),
        id=JOB_MAIL_RETRY,
        name="失败邮件重试",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )

    scheduler.start()
    _scheduler = scheduler

    logger.info(
        "定时任务已启动：到期提醒 [每天 %02d:%02d %s] / 邮件重试 [每 %d 分钟]",
        settings.REMIND_CRON_HOUR,
        settings.REMIND_CRON_MINUTE,
        settings.SCHEDULER_TIMEZONE,
        settings.MAIL_RETRY_INTERVAL_MINUTES,
    )
    return scheduler


def shutdown_scheduler() -> None:
    """关闭调度器（幂等）。"""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("定时任务已关闭")
    _scheduler = None


def get_scheduler() -> Optional[BackgroundScheduler]:
    return _scheduler


def scheduler_status() -> Dict[str, Any]:
    """调度器状态（供 ``GET /system/scheduler`` 展示）。"""
    scheduler = _scheduler
    if scheduler is None or not scheduler.running:
        return {"running": False, "jobs": []}

    jobs = []
    for job in scheduler.get_jobs():
        jobs.append(
            {
                "id": job.id,
                "name": job.name,
                "trigger": str(job.trigger),
                "next_run_time": job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
                if job.next_run_time
                else None,
            }
        )
    return {
        "running": True,
        "timezone": str(scheduler.timezone),
        "jobs": jobs,
    }
