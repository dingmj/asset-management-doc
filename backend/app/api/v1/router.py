"""
v1 路由聚合。

后续阶段新增模块时，只需在此 import 并 include_router 即可。
"""

from fastapi import APIRouter

from app.api.v1 import asset, asset_type, auth, borrow, notification, role, user

api_router = APIRouter()

# ---------- 阶段一：用户与权限 ----------
api_router.include_router(auth.router)
api_router.include_router(user.router)
api_router.include_router(role.router)

# ---------- 阶段二：设备 ----------
api_router.include_router(asset_type.router)
api_router.include_router(asset.router)

# ---------- 阶段三：领用流程 ----------
api_router.include_router(borrow.router)

# ---------- 阶段四：站内信 / 邮件 / 到期提醒 ----------
api_router.include_router(notification.router)

__all__ = ["api_router"]
