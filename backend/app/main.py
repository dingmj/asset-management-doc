"""
FastAPI 应用入口。

职责：
1. 初始化日志
2. 注册 CORS
3. 注册全局异常处理器（统一 { code, message, data }）
4. 挂载 v1 路由
5. 暴露健康检查
6. 随生命周期启停 APScheduler 定时任务（阶段四）

启动：
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import get_logger, setup_logging
from app.core.response import ok
from app.services.scheduler import shutdown_scheduler, start_scheduler

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """应用启动/关闭钩子：启动与关闭 APScheduler。"""
    logger.info("=" * 60)
    logger.info("%s 启动中 | env=%s | debug=%s", settings.APP_NAME, settings.APP_ENV, settings.DEBUG)
    logger.info("接口前缀：%s | 文档：/docs", settings.API_V1_PREFIX)

    # 定时任务（到期提醒 + 失败邮件重试）
    start_scheduler()

    yield

    shutdown_scheduler()
    logger.info("%s 已关闭", settings.APP_NAME)


def create_app() -> FastAPI:
    """应用工厂：便于测试时构造独立实例。"""
    setup_logging()

    app = FastAPI(
        title=settings.APP_NAME,
        description="资产管理系统后端 API（FastAPI + SQLAlchemy 2.0 + MySQL 8）",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ---------- 跨域 ----------
    # 前后端分离部署，开发环境前端跑在 5173
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],  # 导出 Excel 时前端需要读取文件名
    )

    # ---------- 统一异常 ----------
    register_exception_handlers(app)

    # ---------- 业务路由 ----------
    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    # ---------- 基础接口 ----------
    @app.get("/", tags=["00-系统"], summary="服务信息")
    def root() -> dict:
        return ok(
            {
                "app": settings.APP_NAME,
                "env": settings.APP_ENV,
                "version": "1.0.0",
                "docs": "/docs",
                "api_prefix": settings.API_V1_PREFIX,
            }
        )

    @app.get("/health", tags=["00-系统"], summary="健康检查")
    def health() -> dict:
        return ok({"status": "UP"})

    return app


app = create_app()
