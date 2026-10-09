"""
用户管理接口。

权限划分（体现「管理员视图 / 普通用户视图」的核心差异）：
- ``/users/me``       任意登录用户，只能读写自己的资料
- ``/users`` 及其余接口 仅 ADMIN，用于后台用户管理
"""

from typing import Annotated, List

from fastapi import APIRouter, Depends
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.api.deps import CurrentAdmin, CurrentUser, DbSession
from app.core.exceptions import BizException, conflict, not_found
from app.core.response import ApiResponse, BizCode, ok, ok_page
from app.core.security import hash_password
from app.crud.role import role_crud
from app.crud.user import user_crud
from app.models.role import ROLE_ADMIN, SysUserRole
from app.models.user import SysUser
from app.schemas.common import PageOut
from app.schemas.user import (
    AssignRolesIn,
    ResetPasswordIn,
    StatusIn,
    UserCreate,
    UserOut,
    UserQuery,
    UserSimpleOut,
    UserUpdate,
)

router = APIRouter(prefix="/users", tags=["02-用户管理"])


# ============================================================
# 普通用户视图：只能操作自己
# ============================================================
@router.get("/me", response_model=ApiResponse[UserOut], summary="查看本人资料")
def read_my_profile(current_user: CurrentUser) -> dict:
    return ok(UserOut.model_validate(current_user))


@router.put("/me", response_model=ApiResponse[UserOut], summary="修改本人资料")
def update_my_profile(payload: UserUpdate, current_user: CurrentUser, db: DbSession) -> dict:
    """
    普通用户可自助修改联系方式。

    注意：``status`` 与 ``role_ids`` 会被忽略，防止用户给自己提权。
    """
    values = payload.model_dump(exclude_unset=True)
    values.pop("status", None)
    values.pop("role_ids", None)

    if not values:
        return ok(UserOut.model_validate(current_user), message="没有需要修改的内容")

    user_crud.update(db, current_user, values)
    return ok(UserOut.model_validate(current_user), message="修改成功")


# ============================================================
# 管理员视图：用户 CRUD
# ============================================================
@router.get("", response_model=ApiResponse[PageOut[UserOut]], summary="用户分页列表（管理员）")
def list_users(
    query: Annotated[UserQuery, Depends()],
    db: DbSession,
    _: CurrentAdmin,
) -> dict:
    """支持按账号/姓名/部门模糊搜索，按状态、角色筛选，分页返回。"""
    stmt = user_crud.build_query(query)
    items, total = user_crud.paginate(db, stmt, query.page, query.page_size)
    return ok_page([UserOut.model_validate(u) for u in items], total, query.page, query.page_size)


@router.get("/options", response_model=ApiResponse[List[UserSimpleOut]], summary="用户下拉选项（管理员）")
def list_user_options(
    db: DbSession,
    _: CurrentAdmin,
    keyword: str = "",
    limit: int = 50,
) -> dict:
    """用于「选择领用人」等场景。"""
    stmt = user_crud.search_kw(keyword).limit(max(1, min(limit, 200)))
    return ok([UserSimpleOut.model_validate(u) for u in db.scalars(stmt).all()])


@router.post("", response_model=ApiResponse[UserOut], summary="新增用户（管理员）")
def create_user(payload: UserCreate, db: DbSession, _: CurrentAdmin) -> dict:
    """创建用户并绑定角色。账号唯一，密码入库前做 bcrypt 哈希。"""
    username = payload.username.strip()
    if user_crud.is_username_taken(db, username):
        raise conflict(f"账号「{username}」已存在，请更换")

    _ensure_roles_exist(db, payload.role_ids)

    user = SysUser(
        username=username,
        password=hash_password(payload.password),
        real_name=payload.real_name.strip(),
        email=payload.email.strip(),
        phone=payload.phone.strip(),
        department=payload.department.strip(),
        remark=payload.remark.strip(),
        status=payload.status,
    )
    db.add(user)
    db.flush()  # 拿到自增 ID，供角色绑定使用

    if payload.role_ids:
        user_crud.set_roles(db, user, payload.role_ids)

    db.commit()
    db.refresh(user)
    return ok(UserOut.model_validate(user), message="新增成功")


@router.get("/{user_id}", response_model=ApiResponse[UserOut], summary="用户详情（管理员）")
def get_user(user_id: int, db: DbSession, _: CurrentAdmin) -> dict:
    return ok(UserOut.model_validate(_get_user_or_404(db, user_id)))


@router.put("/{user_id}", response_model=ApiResponse[UserOut], summary="修改用户（管理员）")
def update_user(user_id: int, payload: UserUpdate, db: DbSession, _: CurrentAdmin) -> dict:
    """只更新传入的字段；传了 role_ids 则全量覆盖角色绑定。"""
    user = _get_user_or_404(db, user_id)
    values = payload.model_dump(exclude_unset=True)
    role_ids = values.pop("role_ids", None)

    if role_ids is not None:
        _ensure_roles_exist(db, role_ids)
        _guard_last_admin(db, user, role_ids)

    if values:
        user_crud.update(db, user, values, commit=False)
    if role_ids is not None:
        user_crud.set_roles(db, user, role_ids, commit=False)

    db.commit()
    db.refresh(user)
    return ok(UserOut.model_validate(user), message="修改成功")


@router.delete("/{user_id}", response_model=ApiResponse[None], summary="删除用户（管理员）")
def delete_user(user_id: int, db: DbSession, current_admin: CurrentAdmin) -> dict:
    """
    软删除：保留历史领用/审批记录的关联关系。
    """
    if user_id == current_admin.id:
        raise BizException("不能删除当前登录账号", BizCode.BIZ_ERROR)

    user = _get_user_or_404(db, user_id)
    _guard_last_admin(db, user, new_role_ids=[])

    user.is_deleted = 1
    user.status = 0
    db.add(user)
    # 一并解绑角色，避免残留无效关联
    db.execute(delete(SysUserRole).where(SysUserRole.user_id == user.id))
    db.commit()
    return ok(None, message="删除成功")


@router.put("/{user_id}/status", response_model=ApiResponse[UserOut], summary="启用/禁用用户（管理员）")
def change_user_status(user_id: int, payload: StatusIn, db: DbSession, current_admin: CurrentAdmin) -> dict:
    if user_id == current_admin.id and payload.status == 0:
        raise BizException("不能禁用当前登录账号", BizCode.BIZ_ERROR)

    user = _get_user_or_404(db, user_id)
    if payload.status == 0:
        _guard_last_admin(db, user)

    user_crud.update(db, user, {"status": payload.status})
    return ok(UserOut.model_validate(user), message="操作成功")


@router.put("/{user_id}/roles", response_model=ApiResponse[UserOut], summary="分配角色（管理员）")
def assign_roles(user_id: int, payload: AssignRolesIn, db: DbSession, _: CurrentAdmin) -> dict:
    user = _get_user_or_404(db, user_id)
    _ensure_roles_exist(db, payload.role_ids)
    _guard_last_admin(db, user, payload.role_ids)

    user_crud.set_roles(db, user, payload.role_ids)
    return ok(UserOut.model_validate(user), message="角色分配成功")


@router.put("/{user_id}/password", response_model=ApiResponse[None], summary="重置密码（管理员）")
def reset_password(user_id: int, payload: ResetPasswordIn, db: DbSession, _: CurrentAdmin) -> dict:
    user = _get_user_or_404(db, user_id)
    user_crud.update(db, user, {"password": hash_password(payload.new_password)})
    return ok(None, message="密码已重置")


# ============================================================
# 内部工具函数
# ============================================================
def _get_user_or_404(db: Session, user_id: int) -> SysUser:
    user = user_crud.get(db, user_id)
    if user is None or user.is_deleted:
        raise not_found(f"用户不存在（id={user_id}）")
    return user


def _ensure_roles_exist(db: Session, role_ids: List[int]) -> None:
    """校验角色 ID 是否都真实存在。"""
    for role_id in set(role_ids or []):
        if role_crud.get(db, role_id) is None:
            raise not_found(f"角色不存在（id={role_id}）")


def _guard_last_admin(db: Session, user: SysUser, new_role_ids: List[int] | None = None) -> None:
    """
    防止把系统里最后一个管理员降级/禁用，导致无人可管理。

    触发条件：该用户当前是 ADMIN，且操作后系统内将不存在其它可用管理员。
    """
    if ROLE_ADMIN not in user.role_codes:
        return
    if new_role_ids is not None:
        admin_role = role_crud.get_by_code(db, ROLE_ADMIN)
        if admin_role and admin_role.id in new_role_ids:
            return  # 仍保留管理员角色

    query = UserQuery(page=1, page_size=1, role_code=ROLE_ADMIN, status=1)
    stmt = user_crud.build_query(query).where(SysUser.id != user.id)
    if user_crud.count(db, stmt) == 0:
        raise BizException("系统必须保留至少一名启用状态的管理员", BizCode.BIZ_ERROR)
