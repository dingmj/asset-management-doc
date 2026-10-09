"""
统一异常处理。

约定：
- 业务层只抛 ``BizException``，由全局处理器转换成统一响应体；
- 参数校验失败（Pydantic）统一转成 code=400；
- 未捕获异常统一转成 code=500，DEBUG=true 时把堆栈放进 message 便于排查。
"""

from typing import Any, Optional

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.response import BizCode, code_to_http_status, fail


class BizException(Exception):
    """业务异常。所有可预期的错误都应使用它抛出。"""

    def __init__(
        self,
        message: str,
        code: int = int(BizCode.BIZ_ERROR),
        http_status: Optional[int] = None,
    ) -> None:
        self.message = message
        self.code = int(code)
        self.http_status = http_status or code_to_http_status(self.code)
        super().__init__(message)


# ---------- 常用快捷异常 ----------
def bad_request(message: str = "请求参数有误") -> BizException:
    return BizException(message, BizCode.PARAM_ERROR)


def unauthorized(message: str = "未登录或登录已过期") -> BizException:
    return BizException(message, BizCode.UNAUTHORIZED)


def forbidden(message: str = "无权限执行该操作") -> BizException:
    return BizException(message, BizCode.FORBIDDEN)


def not_found(message: str = "资源不存在") -> BizException:
    return BizException(message, BizCode.NOT_FOUND)


def conflict(message: str = "数据冲突") -> BizException:
    return BizException(message, BizCode.CONFLICT)


def _json(code: int, message: str, http_status: int) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content=fail(code=code, message=message),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """把所有异常处理器注册到 FastAPI 应用上。"""

    @app.exception_handler(BizException)
    async def _biz_handler(_: Request, exc: BizException) -> JSONResponse:
        return _json(exc.code, exc.message, exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        """把 Pydantic 校验错误压缩成一句人话，方便前端直接弹 toast。"""
        first: Any = (exc.errors() or [{}])[0]
        loc = ".".join(str(x) for x in first.get("loc", []) if x not in ("body", "query", "path"))
        msg = first.get("msg", "参数校验失败")
        detail = f"{loc}: {msg}" if loc else msg
        return _json(int(BizCode.PARAM_ERROR), f"参数校验失败（{detail}）", 400)

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = exc.status_code if exc.status_code in (400, 401, 403, 404, 405, 409) else int(BizCode.SERVER_ERROR)
        return _json(int(code), str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
        # 生产环境不应把内部细节返回给前端
        message = f"服务器内部错误：{exc}" if settings.DEBUG else "服务器内部错误，请联系管理员"
        return _json(int(BizCode.SERVER_ERROR), message, status.HTTP_500_INTERNAL_SERVER_ERROR)
