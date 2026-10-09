"""
Alembic 运行环境。

要点：
1. 连接串从 ``app.core.config.settings`` 读取（即 .env），不写在 alembic.ini 里；
2. ``target_metadata`` 指向 Base.metadata，且必须先 import app.models 让模型全部注册；
3. 以离线(offline)/在线(online) 两种模式运行；
4. 自定义 server_default 比较规则，消除 MySQL ``now()`` / ``CURRENT_TIMESTAMP``
   归一化带来的无意义 ALTER 噪声。

注意：alembic.ini 会被 configparser 以「系统本地编码」(中文 Windows 为 GBK) 读取，
因此该文件必须保持纯 ASCII；所有中文说明统一放在本文件里。
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 保证能 import app.*（alembic 从 backend/ 目录执行）
BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
import app.models  # noqa: E402,F401  导入以注册所有 ORM 模型

config = context.config

# 把 .env 里的连接串注入 alembic；% 需转义，否则 configparser 会插值报错
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _normalize_default(value) -> str | None:
    """
    归一化 server_default，便于比较。

    ``inspected_default`` 是数据库回显的字符串，而 ``metadata_default`` 是
    ``DefaultClause`` 包装对象，需要先取 ``.arg`` 再转字符串。

    样例::

        "CURRENT_TIMESTAMP" / "now()" / "NOW()"  ->  "current_timestamp"
        "''"（空字符串默认值）                    ->  ""
        "0" / "0.00" / "1"                       ->  原样保留
    """
    if value is None:
        return None
    # DefaultClause -> 取内部的 SQL 表达式
    arg = getattr(value, "arg", value)
    if arg is None:
        return None
    text = str(arg).strip().strip("'").lower()
    if text.endswith("()"):
        text = text[:-2]
    if text in ("now", "current_timestamp", "localtimestamp", "localtime"):
        return "current_timestamp"
    return text


def compare_server_default(
    context,
    inspected_column,
    metadata_column,
    inspected_default,
    metadata_default,
    rendered_metadata_default,
) -> bool:
    """
    自定义 server_default 比较规则（返回 True 表示「有差异」）。

    背景：MySQL 建表时把 ``now()`` 存成 ``CURRENT_TIMESTAMP``，回显出来与元数据里的
    ``now()`` 字面不同；默认比较会让每次 autogenerate 都生成一堆没有实际作用的
    ``ALTER COLUMN``。这里先归一化再比较。
    """
    return _normalize_default(inspected_default) != _normalize_default(metadata_default)


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连数据库。"""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=compare_server_default,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：直连数据库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,                              # 检测字段类型变化
            compare_server_default=compare_server_default,  # 检测默认值变化（已消除 now() 噪声）
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
