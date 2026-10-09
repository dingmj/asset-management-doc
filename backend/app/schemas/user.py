"""用户 Schema。"""

import re
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import PageQuery, StatusIn  # noqa: F401  StatusIn 在此转出，保持原有导入路径可用
from app.schemas.role import RoleSimpleOut

# 宽松的邮箱校验：允许为空串，非空时必须形如 a@b.c
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# 手机号：允许为空串，非空时为 11 位大陆手机号
_PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


class ContactBase(BaseModel):
    """联系方式字段 + 校验，用户新增时复用。"""

    email: str = Field(default="", max_length=120, description="邮箱（领取到期提醒用）")
    phone: str = Field(default="", max_length=20, description="手机号")

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: str) -> str:
        v = (v or "").strip()
        if v and not _EMAIL_RE.match(v):
            raise ValueError("邮箱格式不正确")
        return v

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: str) -> str:
        v = (v or "").strip()
        if v and not _PHONE_RE.match(v):
            raise ValueError("手机号格式不正确")
        return v


class PartialContactBase(BaseModel):
    """更新场景：字段可选，但仍做格式校验。"""

    email: Optional[str] = Field(default=None, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=20)

    @field_validator("email")
    @classmethod
    def _check_email(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if v and not _EMAIL_RE.match(v):
            raise ValueError("邮箱格式不正确")
        return v

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if v and not _PHONE_RE.match(v):
            raise ValueError("手机号格式不正确")
        return v


class UserBase(ContactBase):
    real_name: str = Field(default="", max_length=50, description="真实姓名")
    department: str = Field(default="", max_length=100, description="所属部门")
    remark: str = Field(default="", max_length=255, description="备注")


class UserCreate(UserBase):
    username: str = Field(..., min_length=3, max_length=50, description="登录账号，唯一")
    password: str = Field(..., min_length=6, max_length=64, description="登录密码，至少 6 位")
    status: int = Field(default=1, ge=0, le=1, description="状态：1=启用，0=禁用")
    role_ids: List[int] = Field(default_factory=list, description="绑定的角色ID列表")


class UserUpdate(PartialContactBase):
    """用户更新：字段全部可选。注意：不允许修改 username。"""

    real_name: Optional[str] = Field(default=None, max_length=50)
    department: Optional[str] = Field(default=None, max_length=100)
    remark: Optional[str] = Field(default=None, max_length=255)
    status: Optional[int] = Field(default=None, ge=0, le=1)
    role_ids: Optional[List[int]] = Field(default=None, description="传则全量覆盖角色绑定")


class UserOut(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    status: int
    last_login_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    roles: List[RoleSimpleOut] = Field(default_factory=list, description="已绑定角色")


class UserSimpleOut(BaseModel):
    """其它模块内嵌用户信息时使用。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    real_name: str
    department: str = ""


class UserQuery(PageQuery):
    """用户列表筛选条件。"""

    username: Optional[str] = Field(default=None, description="账号，模糊匹配")
    real_name: Optional[str] = Field(default=None, description="姓名，模糊匹配")
    department: Optional[str] = Field(default=None, description="部门，模糊匹配")
    status: Optional[int] = Field(default=None, ge=0, le=1, description="状态")
    role_id: Optional[int] = Field(default=None, description="按角色ID筛选")
    role_code: Optional[str] = Field(default=None, description="按角色编码筛选，如 ADMIN")


class AssignRolesIn(BaseModel):
    role_ids: List[int] = Field(default_factory=list, description="角色ID列表，全量覆盖")


class ResetPasswordIn(BaseModel):
    new_password: str = Field(..., min_length=6, max_length=64, description="新密码，至少 6 位")
