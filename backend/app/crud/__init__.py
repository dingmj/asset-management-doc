"""数据访问层（同步 SQLAlchemy Session）。"""

from app.crud.asset import asset_crud
from app.crud.asset_type import asset_type_crud
from app.crud.base import CRUDBase
from app.crud.borrow import borrow_crud
from app.crud.notification import mail_log_crud, notification_crud, remind_log_crud
from app.crud.role import role_crud
from app.crud.user import user_crud

__all__ = [
    "CRUDBase",
    "user_crud",
    "role_crud",
    "asset_crud",
    "asset_type_crud",
    "borrow_crud",
    "notification_crud",
    "mail_log_crud",
    "remind_log_crud",
]
