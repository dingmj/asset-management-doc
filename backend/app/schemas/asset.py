"""
设备 Schema。

阶段二的重点是「动态筛选」：``AssetQuery`` 把前端可能传的筛选条件全部收敛到
一个模型里，配合 ``Depends()`` 自动解析成 query 参数，CRUD 层再按需拼 WHERE。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.asset_type import AssetTypeSimpleOut
from app.schemas.common import PageQuery

# ==================== 可排序字段白名单 ====================
# 白名单机制：前端传任何其它值都会被忽略，杜绝 ORDER BY 注入
SORTABLE_FIELDS = {
    "id",
    "asset_no",
    "name",
    "price",
    "status",
    "purchase_date",
    "warranty_until",
    "borrow_at",
    "expire_at",
    "created_at",
    "updated_at",
}


class AssetBase(BaseModel):
    """设备公共字段。"""

    name: str = Field(..., min_length=1, max_length=100, description="设备名称")
    brand: str = Field(default="", max_length=50, description="品牌")
    model: str = Field(default="", max_length=100, description="型号")
    sn: str = Field(default="", max_length=100, description="序列号/SN")
    price: Decimal = Field(
        default=Decimal("0.00"),
        ge=0,
        max_digits=12,
        decimal_places=2,
        description="采购价格（元）",
    )
    location: str = Field(default="", max_length=100, description="存放位置")
    department: str = Field(default="", max_length=100, description="归属部门")
    status: int = Field(default=0, ge=0, le=3, description="状态：0=闲置 1=领用中 2=维修中 3=已报废")
    purchase_date: Optional[date] = Field(default=None, description="采购日期 YYYY-MM-DD")
    warranty_until: Optional[date] = Field(default=None, description="保修截止日期 YYYY-MM-DD")
    remark: str = Field(default="", max_length=255, description="备注")


class AssetCreate(AssetBase):
    """新增设备。"""

    asset_no: str = Field(..., min_length=1, max_length=50, description="资产编号，唯一")
    type_id: int = Field(..., description="设备类型ID")

    @field_validator("asset_no")
    @classmethod
    def _upper_asset_no(cls, v: str) -> str:
        """资产编号统一大写，避免 A-001 与 a-001 被当成两台设备。"""
        return (v or "").strip().upper()


class AssetUpdate(BaseModel):
    """修改设备：字段全部可选，只更新传入的字段。

    注意：领用信息（borrower_id / borrow_at / expire_at）只能由阶段三的
    审批流程回写，不允许通过普通编辑接口直接篡改。
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    type_id: Optional[int] = Field(default=None, description="设备类型ID")
    brand: Optional[str] = Field(default=None, max_length=50)
    model: Optional[str] = Field(default=None, max_length=100)
    sn: Optional[str] = Field(default=None, max_length=100)
    price: Optional[Decimal] = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    location: Optional[str] = Field(default=None, max_length=100)
    department: Optional[str] = Field(default=None, max_length=100)
    status: Optional[int] = Field(default=None, ge=0, le=3)
    purchase_date: Optional[date] = None
    warranty_until: Optional[date] = None
    remark: Optional[str] = Field(default=None, max_length=255)


class AssetOut(BaseModel):
    """设备输出（含类型名、领用人名、到期天数等派生字段，前端无需再查）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    asset_no: str
    name: str
    type_id: int
    type_name: str = Field(default="", description="设备类型名称")
    type_code: str = Field(default="", description="设备类型编码")
    asset_type: Optional[AssetTypeSimpleOut] = None
    brand: str = ""
    model: str = ""
    sn: str = ""
    price: float = 0.0
    location: str = ""
    department: str = ""
    status: int = 0
    status_label: str = Field(default="", description="状态中文名")
    purchase_date: Optional[date] = None
    warranty_until: Optional[date] = None

    borrower_id: Optional[int] = None
    borrower_name: str = Field(default="", description="当前领用人姓名")
    borrow_at: Optional[datetime] = None
    expire_at: Optional[datetime] = None
    days_to_expire: Optional[int] = Field(default=None, description="距到期天数，负数表示已超期")
    is_expired: bool = Field(default=False, description="是否已超期")

    remark: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AssetSimpleOut(BaseModel):
    """内嵌到领用单等场景的精简设备信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    asset_no: str
    name: str
    type_name: str = ""
    status: int = 0
    status_label: str = ""


class AssetOptionOut(BaseModel):
    """设备下拉选项（领用申请时选择可领用设备）。"""

    label: str = Field(description="显示文本：编号 名称")
    value: int = Field(description="设备ID")
    asset_no: str
    status: int


class AssetQuery(PageQuery):
    """
    设备动态筛选条件。

    覆盖文档要求的四类筛选：
    1. 按类型        -> type_id / type_code
    2. 按状态        -> status / status_list
    3. 按领用时间范围 -> borrow_at_start / borrow_at_end
    4. 按过期时间范围 -> expire_at_start / expire_at_end
    另外提供 keyword 全文模糊、部门/位置、领用人、即将到期等常用条件。
    """

    # ---------- 关键字 ----------
    keyword: Optional[str] = Field(default=None, description="资产编号/名称/序列号/品牌/型号，模糊匹配")
    asset_no: Optional[str] = Field(default=None, description="资产编号，模糊匹配")
    name: Optional[str] = Field(default=None, description="设备名称，模糊匹配")
    sn: Optional[str] = Field(default=None, description="序列号，模糊匹配")

    # ---------- 类型 ----------
    type_id: Optional[int] = Field(default=None, description="设备类型ID")
    type_code: Optional[str] = Field(default=None, description="设备类型编码，如 LAPTOP")

    # ---------- 状态 ----------
    status: Optional[int] = Field(default=None, ge=0, le=3, description="设备状态（单选）")
    only_available: Optional[bool] = Field(default=None, description="只看可领用（等价于 status=0）")
    only_expired: Optional[bool] = Field(default=None, description="只看已超期未归还")

    # ---------- 归属 ----------
    department: Optional[str] = Field(default=None, description="归属部门，模糊匹配")
    location: Optional[str] = Field(default=None, description="存放位置，模糊匹配")
    borrower_id: Optional[int] = Field(default=None, description="当前领用人ID")
    borrower_keyword: Optional[str] = Field(default=None, description="领用人姓名/账号，模糊匹配")

    # ---------- 时间范围 ----------
    borrow_at_start: Optional[datetime] = Field(default=None, description="领用时间起（含）")
    borrow_at_end: Optional[datetime] = Field(default=None, description="领用时间止（含）")
    expire_at_start: Optional[datetime] = Field(default=None, description="到期时间起（含）")
    expire_at_end: Optional[datetime] = Field(default=None, description="到期时间止（含）")
    expire_within_days: Optional[int] = Field(
        default=None, ge=0, le=3650, description="未来 N 天内到期（不含已超期）"
    )

    # ---------- 排序 ----------
    order_by: Optional[str] = Field(default=None, description=f"排序字段，可选：{'/'.join(sorted(SORTABLE_FIELDS))}")
    order_desc: bool = Field(default=True, description="是否倒序，默认 true")

    @field_validator("order_by")
    @classmethod
    def _check_order_by(cls, v: Optional[str]) -> Optional[str]:
        """非白名单字段直接置空，回落到默认排序。"""
        if v is None:
            return None
        v = v.strip()
        return v if v in SORTABLE_FIELDS else None


class AssetStatusIn(BaseModel):
    """修改设备状态。"""

    status: int = Field(..., ge=0, le=3, description="状态：0=闲置 1=领用中 2=维修中 3=已报废")
    remark: str = Field(default="", max_length=255, description="变更备注")


class AssetBatchStatusIn(BaseModel):
    """批量修改设备状态。"""

    ids: List[int] = Field(..., min_length=1, description="设备ID列表")
    status: int = Field(..., ge=0, le=3, description="目标状态")

    @field_validator("ids")
    @classmethod
    def _dedup(cls, v: List[int]) -> List[int]:
        return sorted(set(v))


class AssetStatisticsQuery(BaseModel):
    """统计口径筛选（阶段五统计接口复用）。"""

    type_id: Optional[int] = None
    department: Optional[str] = None
