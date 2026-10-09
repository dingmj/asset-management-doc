"""
全局配置模块。

设计约定：
1. 所有可变参数集中在此，通过 .env / 环境变量注入，业务代码禁止硬编码；
2. 统一出口 ``from app.core.config import settings``；
3. pydantic-settings 会自动做类型转换与校验，缺失字段回落到默认值。
"""

from functools import lru_cache
from pathlib import Path
from typing import List
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录：config.py -> core -> app -> backend
BASE_DIR: Path = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """项目全部配置项。"""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",  # 允许 .env 中存在多余键
        case_sensitive=False,
    )

    # ==================== 应用 ====================
    APP_NAME: str = "资产管理系统"
    APP_ENV: str = "dev"
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # ==================== MySQL 8 ====================
    MYSQL_HOST: str = "127.0.0.1"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = "123456"
    MYSQL_DB: str = "asset_db"
    # 若显式配置则优先使用（例如测试时用 sqlite）
    DATABASE_URL: str = ""

    SQL_ECHO: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_RECYCLE: int = 3600  # 秒，防止 MySQL 8h 断开空闲连接

    # ==================== JWT ====================
    JWT_SECRET_KEY: str = "please-change-me"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 720

    # ==================== CORS ====================
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ==================== 种子账号 ====================
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin123"
    NORMAL_USERNAME: str = "user"
    NORMAL_PASSWORD: str = "user123"

    # ==================== 邮件（阶段四） ====================
    # MAIL_ENABLED=false 时进入「开发模拟模式」：不真正投递，
    # 但依然写 mail_log 并把邮件正文落盘到 logs/mail/，方便查看与演示。
    MAIL_ENABLED: bool = False
    MAIL_HOST: str = "smtp.example.com"
    MAIL_PORT: int = 465
    MAIL_USERNAME: str = ""
    MAIL_PASSWORD: str = ""
    MAIL_FROM: str = "asset-system@example.com"
    MAIL_FROM_NAME: str = "资产管理系统"
    MAIL_USE_SSL: bool = True  # True=SSL(465) / False=STARTTLS(587)
    MAIL_TIMEOUT: int = 15
    MAIL_DUMP_DIR: str = "logs/mail"  # 相对 backend/

    # ==================== 到期提醒（阶段四） ====================
    REMIND_DAYS_BEFORE: int = 3  # 提前多少天开始提醒
    REMIND_TO_ADMIN: bool = True  # 是否给管理员推送到期汇总
    REMIND_MAIL_ENABLED: bool = True  # 普通用户是否额外发邮件（站内信始终发送）

    # ==================== 定时任务（APScheduler） ====================
    SCHEDULER_ENABLED: bool = True  # pytest / 脚本执行时应设为 false
    SCHEDULER_TIMEZONE: str = "Asia/Shanghai"
    REMIND_CRON_HOUR: int = 9  # 到期提醒扫描：每天 09:00
    REMIND_CRON_MINUTE: int = 0
    MAIL_RETRY_INTERVAL_MINUTES: int = 30  # 失败邮件重试扫描间隔

    # ==================== 派生属性 ====================
    @property
    def database_url(self) -> str:
        """最终使用的 SQLAlchemy 连接串。"""
        if self.DATABASE_URL:
            return self.DATABASE_URL
        # 密码可能含 @ : / 等特殊字符，必须 URL 编码
        return (
            f"mysql+pymysql://{self.MYSQL_USER}:{quote_plus(self.MYSQL_PASSWORD)}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DB}?charset=utf8mb4"
        )

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    """带缓存的配置工厂（便于测试时 clear 后重载）。"""
    return Settings()


# 全局单例
settings: Settings = get_settings()
