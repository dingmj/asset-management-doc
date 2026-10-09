"""
认证接口：登录、登出、当前用户、修改密码、刷新令牌。
"""

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.core.exceptions import BizException, forbidden
from app.core.response import ApiResponse, BizCode, ok
from app.core.security import create_access_token, hash_password, verify_password
from app.crud.user import user_crud
from app.schemas.auth import ChangePasswordIn, LoginRequest, TokenOut
from app.schemas.user import UserOut

router = APIRouter(prefix="/auth", tags=["01-认证"])


@router.post("/login", response_model=ApiResponse[TokenOut], summary="登录（获取 JWT）")
def login(payload: LoginRequest, db: DbSession) -> dict:
    """
    账号密码登录。

    - 成功返回 access_token，前端存入 localStorage/Pinia，后续请求带 ``Authorization: Bearer <token>``
    - 账号不存在与密码错误返回同一提示，避免暴露账号是否存在
    """
    user = user_crud.get_by_username(db, payload.username.strip())

    if user is None or not verify_password(payload.password, user.password):
        raise BizException("账号或密码错误", BizCode.UNAUTHORIZED)
    if user.is_deleted:
        raise BizException("账号不存在或已被删除", BizCode.UNAUTHORIZED)
    if user.status != 1:
        raise forbidden("账号已被禁用，请联系管理员")

    token, expires_in = create_access_token(
        user_id=user.id,
        username=user.username,
        roles=sorted(user.role_codes),
    )
    user_crud.touch_login(db, user)

    data = TokenOut(
        access_token=token,
        token_type="Bearer",
        expires_in=expires_in,
        user=UserOut.model_validate(user),
    )
    return ok(data, message="登录成功")


@router.get("/me", response_model=ApiResponse[UserOut], summary="获取当前登录用户信息")
def read_me(current_user: CurrentUser) -> dict:
    """前端刷新页面后用它恢复用户状态与权限。"""
    return ok(UserOut.model_validate(current_user))


@router.post("/refresh", response_model=ApiResponse[TokenOut], summary="刷新令牌")
def refresh_token(current_user: CurrentUser, db: DbSession) -> dict:
    """用旧令牌换取新令牌（旧令牌未过期时可用）。"""
    token, expires_in = create_access_token(
        user_id=current_user.id,
        username=current_user.username,
        roles=sorted(current_user.role_codes),
    )
    data = TokenOut(
        access_token=token,
        token_type="Bearer",
        expires_in=expires_in,
        user=UserOut.model_validate(current_user),
    )
    return ok(data, message="刷新成功")


@router.post("/logout", response_model=ApiResponse[None], summary="登出")
def logout(current_user: CurrentUser) -> dict:
    """
    JWT 无状态，服务端不维护黑名单；
    前端调用后清除本地 token 即可。此接口用于统一埋点/后续写操作日志。
    """
    return ok(None, message="已退出登录")


@router.post("/change-password", response_model=ApiResponse[None], summary="修改本人密码")
def change_password(payload: ChangePasswordIn, current_user: CurrentUser, db: DbSession) -> dict:
    """普通用户与管理员都可调用，只能改自己的密码。"""
    if not verify_password(payload.old_password, current_user.password):
        raise BizException("原密码不正确", BizCode.BIZ_ERROR)
    if payload.old_password == payload.new_password:
        raise BizException("新密码不能与原密码相同", BizCode.BIZ_ERROR)

    current_user.password = hash_password(payload.new_password)
    db.add(current_user)
    db.commit()
    return ok(None, message="密码修改成功，请重新登录")
