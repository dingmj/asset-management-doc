"""公共 Schema：分页请求参数等。"""

from typing import Generic, List, Optional, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field

T = TypeVar("T")


class PageQuery(BaseModel):
    """
    分页查询基类。

    用法（FastAPI 会把字段自动识别为 query 参数）::

        def list_users(q: Annotated[UserQuery, Depends()]): ...
    """

    page: int = Field(default=1, ge=1, description="页码，从 1 开始")
    page_size: int = Field(default=10, ge=1, le=200, description="每页条数，1~200")


class PageOut(BaseModel, Generic[T]):
    """统一分页返回结构（与 app.core.response.page_data 对应）。"""

    list: List[T] = Field(default_factory=list, description="当前页数据")
    total: int = Field(default=0, description="总记录数")
    page: int = Field(default=1, description="当前页码")
    page_size: int = Field(default=10, description="每页条数")
    pages: int = Field(default=0, description="总页数")


class IdsIn(BaseModel):
    """批量操作入参：ID 列表。"""

    ids: List[int] = Field(default_factory=list, min_length=1, description="主键列表")


class OptionOut(BaseModel):
    """下拉选项通用结构（角色/类型等）。"""

    label: str = Field(description="显示文本")
    value: str | int = Field(description="实际值")
    extra: Optional[dict] = Field(default=None, description="附加信息")


class StatusIn(BaseModel):
    """通用「启用/禁用」入参（用户、角色、设备类型共用）。"""

    status: int = Field(..., ge=0, le=1, description="状态：1=启用，0=禁用")


# 兼容直接作为依赖使用的写法
def page_params(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(10, ge=1, le=200, description="每页条数，1~200"),
) -> PageQuery:
    return PageQuery(page=page, page_size=page_size)
