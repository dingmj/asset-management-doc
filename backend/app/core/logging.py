"""
日志初始化。

统一控制台 + 文件双输出，文件按天切割（TimedRotatingFileHandler），
方便后续排查定时任务、邮件发送等后台行为。
"""

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from app.core.config import BASE_DIR, settings

LOG_DIR: Path = BASE_DIR / "logs"

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def setup_logging() -> None:
    """进程启动时调用一次即可。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    level = logging.DEBUG if settings.DEBUG else logging.INFO
    formatter = logging.Formatter(_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)

    file_handler = TimedRotatingFileHandler(
        filename=str(LOG_DIR / "app.log"),
        when="midnight",
        backupCount=15,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    # 避免 uvicorn --reload 重复注册 handler
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)

    # SQLAlchemy 的 SQL 回显交给 echo 参数控制，这里压到 WARNING 避免刷屏
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
