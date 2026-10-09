"""角色 Schema。"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class RoleBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=50, description="角色名称")
    description: str = Field(default="", max_length=255, description="角色描述")
    sort: int = Field(default=0, description="排序值，越小越靠前")
    status: int = Field(default=1, ge=0, le=1, description="状态：1=启用，0=禁用")


class RoleCreate(RoleBase):
    code: str = Field(..., min_length=2, max_length=50, description="角色编码，唯一，如 ADMIN")


class RoleUpdate(BaseModel):
    """角色更新：所有字段可选，只更新传入的字段。"""

    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    description: Optional[str] = Field(default=None, max_length=255)
    sort: Optional[int] = None
    status: Optional[int] = Field(default=None, ge=0, le=1)


class RoleOut(RoleBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class RoleSimpleOut(BaseModel):
    """用于用户详情里内嵌的角色信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str


class RoleOptionOut(BaseModel):
    """角色下拉选项。"""

    label: str = Field(description="角色名称")
    value: int = Field(description="角色ID")
    code: str = Field(description="角色编码")


class RoleListOut(BaseModel):
    list: List[RoleOut] = Field(default_factory=list)
    total: int = 0
