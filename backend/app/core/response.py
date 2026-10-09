"""
统一响应格式。

全站接口固定返回::

    { "code": 200, "message": "success", "data": ... }

分页接口的 data 固定为::

    { "list": [...], "total": 100, "page": 1, "page_size": 10, "pages": 10 }

前端只需判断 code 即可，无需关心 HTTP 状态码差异（两者语义保持一致的）。
"""

from enum import IntEnum
from math import ceil
from typing import Any, Generic, List, Optional, Sequence, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class BizCode(IntEnum):
    """业务状态码。1xx-5xx 与 HTTP 语义对齐，4xxx 表示业务级错误。"""

    SUCCESS = 200
    PARAM_ERROR = 400
    UNAUTHORIZED = 401
    TOKEN_EXPIRED = 40101
    FORBIDDEN = 403
    NOT_FOUND = 404
    METHOD_NOT_ALLOWED = 405
    CONFLICT = 409
    BIZ_ERROR = 4000  # 通用业务错误（如：用户名已存在）
    SERVER_ERROR = 500


# 业务码 -> HTTP 状态码
_CODE_TO_HTTP = {
    BizCode.SUCCESS: 200,
    BizCode.PARAM_ERROR: 400,
    BizCode.UNAUTHORIZED: 401,
    BizCode.TOKEN_EXPIRED: 401,
    BizCode.FORBIDDEN: 403,
    BizCode.NOT_FOUND: 404,
    BizCode.METHOD_NOT_ALLOWED: 405,
    BizCode.CONFLICT: 409,
    BizCode.BIZ_ERROR: 400,
    BizCode.SERVER_ERROR: 500,
}


def code_to_http_status(code: int) -> int:
    """把业务码映射为合理的 HTTP 状态码。"""
    try:
        return _CODE_TO_HTTP[BizCode(code)]
    except ValueError:
        pass
    # 未登记的自定义码：本身就是合法 HTTP 状态码则直接用，否则归为 400
    return code if 100 <= code <= 599 else 400


# ==================== 响应模型（用于 OpenAPI 文档） ====================
class ApiResponse(BaseModel, Generic[T]):
    """统一响应体。"""

    code: int = Field(default=int(BizCode.SUCCESS), description="业务状态码，200 表示成功")
    message: str = Field(default="success", description="提示信息")
    data: Optional[T] = Field(default=None, description="业务数据")


class PageData(BaseModel, Generic[T]):
    """统一分页数据结构。"""

    list: List[T] = Field(default_factory=list, description="当前页数据")
    total: int = Field(default=0, description="总记录数")
    page: int = Field(default=1, description="当前页码，从 1 开始")
    page_size: int = Field(default=10, description="每页条数")
    pages: int = Field(default=0, description="总页数")


# ==================== 响应构造工具 ====================
def ok(data: Any = None, message: str = "success") -> dict:
    """构造成功响应体。"""
    return {"code": int(BizCode.SUCCESS), "message": message, "data": data}


def fail(code: int = int(BizCode.BIZ_ERROR), message: str = "操作失败") -> dict:
    """构造失败响应体。"""
    return {"code": int(code), "message": message, "data": None}


def page_data(
    items: Sequence[Any],
    total: int,
    page: int,
    page_size: int,
) -> dict:
    """把 (当前页列表, 总数, 页码, 每页条数) 组装成统一分页结构。"""
    pages = ceil(total / page_size) if page_size > 0 else 0
    return {
        "list": list(items),
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


def ok_page(items: Sequence[Any], total: int, page: int, page_size: int) -> dict:
    """分页成功响应体。"""
    return ok(page_data(items, total, page, page_size))
