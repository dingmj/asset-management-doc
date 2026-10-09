"""
FastAPI 依赖注入。

权限体系（全部基于 ``Depends`` 实现，不使用中间件）：
- ``get_current_user``   解析 Bearer Token -> 当前用户（未登录/过期直接 401）
- ``get_current_admin``  在 get_current_user 基础上要求 ADMIN 角色
- ``require_roles(...)`` 更细粒度的角色要求（后续阶段按需使用）
"""

from typing import Annotated, Callable, Optional

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.exceptions import BizException, forbidden
from app.core.response import BizCode
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import SysUser

# auto_error=False：自己抛统一格式的 401，而不是 FastAPI 默认的 {"detail": ...}
bearer_scheme = HTTPBearer(auto_error=False, description="请求头格式：Authorization: Bearer <token>")

# 常用依赖别名
DbSession = Annotated[Session, Depends(get_db)]
BearerToken = Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer_scheme)]


def get_current_user(db: DbSession, credentials: BearerToken) -> SysUser:
    """
    从 Authorization 头解析出当前登录用户。

    失败场景：无 Token / Token 无效 / Token 过期 / 用户不存在 / 用户被禁用 / 用户被删除。
    """
    if credentials is None or not credentials.credentials:
        raise BizException("未登录，请先登录", BizCode.UNAUTHORIZED)

    payload = decode_token(credentials.credentials)  # 过期/伪造在此抛 BizException

    subject = payload.get("sub")
    if not subject:
        raise BizException("无效的登录凭证，请重新登录", BizCode.UNAUTHORIZED)

    try:
        user_id = int(subject)
    except (TypeError, ValueError):
        raise BizException("无效的登录凭证，请重新登录", BizCode.UNAUTHORIZED)

    user = db.get(SysUser, user_id)
    if user is None or user.is_deleted:
        raise BizException("账号不存在或已被删除，请重新登录", BizCode.UNAUTHORIZED)
    if user.status != 1:
        raise forbidden("账号已被禁用，请联系管理员")
    return user


# 当前登录用户（任何已登录用户）
CurrentUser = Annotated[SysUser, Depends(get_current_user)]


def get_current_admin(current_user: CurrentUser) -> SysUser:
    """要求当前用户具备管理员角色。"""
    if not current_user.is_admin:
        raise forbidden("无权限，该操作仅系统管理员可执行")
    return current_user


# 当前管理员
CurrentAdmin = Annotated[SysUser, Depends(get_current_admin)]


def require_roles(*role_codes: str) -> Callable[[SysUser], SysUser]:
    """
    生成「要求指定角色之一」的依赖。

    用法::

        @router.get("/xxx", dependencies=[Depends(require_roles(ROLE_ADMIN))])
    """
    allowed = set(role_codes)

    def _checker(current_user: CurrentUser) -> SysUser:
        if not (current_user.role_codes & allowed):
            raise forbidden(f"无权限，需要以下角色之一：{'/'.join(sorted(allowed))}")
        return current_user

    return _checker
