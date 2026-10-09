"""
角色接口。

- 查询类接口对已登录用户开放（前端下拉框需要）
- 增删改仅管理员可操作
"""

from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import CurrentAdmin, CurrentUser, DbSession
from app.core.exceptions import BizException, conflict, not_found
from app.core.response import ApiResponse, BizCode, ok, ok_page
from app.crud.role import role_crud
from app.models.role import ROLE_ADMIN
from app.schemas.role import RoleCreate, RoleOptionOut, RoleOut, RoleUpdate
from app.schemas.common import PageOut
from app.schemas.common import PageQuery

router = APIRouter(prefix="/roles", tags=["03-角色管理"])


@router.get("", response_model=ApiResponse[List[RoleOut]], summary="角色下拉列表（登录即可）")
def list_roles(current_user: CurrentUser, db: DbSession) -> dict:
    """返回所有启用角色，供前端下拉选择。"""
    return ok([RoleOut.model_validate(r) for r in role_crud.list_enabled(db)])


@router.get("/options", response_model=ApiResponse[List[RoleOptionOut]], summary="角色选项（登录即可）")
def list_role_options(current_user: CurrentUser, db: DbSession) -> dict:
    roles = role_crud.list_enabled(db)
    return ok([RoleOptionOut(label=r.name, value=r.id, code=r.code) for r in roles])


@router.get("/page", response_model=ApiResponse[PageOut[RoleOut]], summary="角色分页列表（管理员）")
def page_roles(
    db: DbSession,
    _: CurrentAdmin,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 10,
    keyword: Optional[str] = Query(default=None, description="角色名/编码模糊搜索"),
    status: Optional[int] = Query(default=None, ge=0, le=1, description="状态筛选"),
) -> dict:
    params = PageQuery(page=page, page_size=page_size)
    stmt = role_crud.build_query(keyword=keyword, status=status)
    items, total = role_crud.paginate(db, stmt, params.page, params.page_size)
    return ok_page([RoleOut.model_validate(r) for r in items], total, params.page, params.page_size)


@router.post("", response_model=ApiResponse[RoleOut], summary="新增角色（管理员）")
def create_role(payload: RoleCreate, db: DbSession, _: CurrentAdmin) -> dict:
    code = payload.code.strip().upper()
    if role_crud.is_code_taken(db, code):
        raise conflict(f"角色编码「{code}」已存在")

    role = role_crud.create(
        db,
        {
            "code": code,
            "name": payload.name.strip(),
            "description": payload.description.strip(),
            "sort": payload.sort,
            "status": payload.status,
        },
    )
    return ok(RoleOut.model_validate(role), message="新增成功")


@router.get("/{role_id}", response_model=ApiResponse[RoleOut], summary="角色详情（管理员）")
def get_role(role_id: int, db: DbSession, _: CurrentAdmin) -> dict:
    role = role_crud.get(db, role_id)
    if role is None:
        raise not_found(f"角色不存在（id={role_id}）")
    return ok(RoleOut.model_validate(role))


@router.put("/{role_id}", response_model=ApiResponse[RoleOut], summary="修改角色（管理员）")
def update_role(role_id: int, payload: RoleUpdate, db: DbSession, _: CurrentAdmin) -> dict:
    """角色编码不可修改，避免已发放的权限判断失效。"""
    role = role_crud.get(db, role_id)
    if role is None:
        raise not_found(f"角色不存在（id={role_id}）")

    values = payload.model_dump(exclude_unset=True)
    if not values:
        return ok(RoleOut.model_validate(role), message="没有需要修改的内容")

    role = role_crud.update(db, role, values)
    return ok(RoleOut.model_validate(role), message="修改成功")


@router.delete("/{role_id}", response_model=ApiResponse[None], summary="删除角色（管理员）")
def delete_role(role_id: int, db: DbSession, _: CurrentAdmin) -> dict:
    role = role_crud.get(db, role_id)
    if role is None:
        raise not_found(f"角色不存在（id={role_id}）")

    if role.code == ROLE_ADMIN:
        raise BizException("系统内置管理员角色不允许删除", BizCode.BIZ_ERROR)

    used = role_crud.count_users(db, role_id)
    if used > 0:
        raise conflict(f"该角色下仍有 {used} 名用户，请先解除绑定")

    role_crud.remove(db, role)
    return ok(None, message="删除成功")
