"""设备类型 Schema。"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class AssetTypeBase(BaseModel):
    """设备类型公共字段。"""

    name: str = Field(..., min_length=1, max_length=50, description="类型名称，如 笔记本电脑")
    description: str = Field(default="", max_length=255, description="类型描述")
    sort: int = Field(default=0, description="排序值，越小越靠前")
    status: int = Field(default=1, ge=0, le=1, description="状态：1=启用，0=禁用")


class AssetTypeCreate(AssetTypeBase):
    """新增设备类型。"""

    code: str = Field(..., min_length=1, max_length=50, description="类型编码，唯一，如 LAPTOP")


class AssetTypeUpdate(BaseModel):
    """修改设备类型：字段全部可选，只更新传入的字段；编码不允许修改。"""

    name: Optional[str] = Field(default=None, min_length=1, max_length=50)
    description: Optional[str] = Field(default=None, max_length=255)
    sort: Optional[int] = None
    status: Optional[int] = Field(default=None, ge=0, le=1)


class AssetTypeOut(AssetTypeBase):
    """设备类型输出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    asset_count: int = Field(default=0, description="该类型下的设备数量（仅分页列表填充）")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AssetTypeSimpleOut(BaseModel):
    """内嵌到设备详情中的精简类型信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str


class AssetTypeOptionOut(BaseModel):
    """设备类型下拉选项。"""

    label: str = Field(description="类型名称")
    value: int = Field(description="类型ID")
    code: str = Field(description="类型编码")


class AssetTypeQuery(BaseModel):
    """设备类型筛选条件。"""

    keyword: Optional[str] = Field(default=None, description="类型名称/编码，模糊匹配")
    status: Optional[int] = Field(default=None, ge=0, le=1, description="状态筛选")


class AssetTypeListOut(BaseModel):
    list: List[AssetTypeOut] = Field(default_factory=list)
    total: int = 0
