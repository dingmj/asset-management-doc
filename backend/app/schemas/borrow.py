"""领用申请 / 审批 Schema。"""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.asset import AssetSimpleOut
from app.schemas.common import PageQuery
from app.schemas.user import UserSimpleOut

# 单次申请/续借的最大天数，防止出现「99999 天」这类脏数据
MAX_BORROW_DAYS = 365


# ==================== 入参 ====================
class BorrowApplyCreate(BaseModel):
    """提交领用申请。"""

    asset_id: int = Field(..., description="设备ID")
    borrow_days: int = Field(default=7, ge=1, le=MAX_BORROW_DAYS, description="领用天数")
    reason: str = Field(..., min_length=2, max_length=255, description="领用理由 / 用途")
    remark: str = Field(default="", max_length=255, description="备注")


class BorrowRenewCreate(BaseModel):
    """提交续借申请。"""

    days: int = Field(default=7, ge=1, le=MAX_BORROW_DAYS, description="续借天数")
    reason: str = Field(..., min_length=2, max_length=255, description="续借理由")
    remark: str = Field(default="", max_length=255, description="备注")


class BorrowApproveIn(BaseModel):
    """审批入参。"""

    approved: bool = Field(..., description="true=通过，false=驳回")
    remark: str = Field(default="", max_length=255, description="审批意见")

    @field_validator("remark")
    @classmethod
    def _trim(cls, v: str) -> str:
        return (v or "").strip()


class BorrowReturnIn(BaseModel):
    """归还入参。"""

    to_status: int = Field(
        default=0,
        ge=0,
        le=3,
        description="归还后设备状态：0=闲置（默认），2=维修中，3=已报废",
    )
    remark: str = Field(default="", max_length=255, description="归还备注，如设备损坏情况")


class BorrowCancelIn(BaseModel):
    """撤销申请入参。"""

    remark: str = Field(default="", max_length=255, description="撤销原因")


# ==================== 出参 ====================
class ApprovalRecordOut(BaseModel):
    """审批流水输出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    apply_id: int
    approver_id: Optional[int] = None
    approver_name: str = ""
    action: int
    action_label: str = ""
    remark: str = ""
    created_at: Optional[datetime] = None


class BorrowApplyOut(BaseModel):
    """申请单输出（已展开设备与人员信息，前端无需二次查询）。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    apply_no: str
    apply_type: int
    type_label: str = ""
    status: int
    status_label: str = ""

    asset_id: int
    asset_no: str = ""
    asset_name: str = ""
    asset: Optional[AssetSimpleOut] = None

    applicant_id: int
    applicant_name: str = ""
    reason: str = ""
    borrow_days: int = 0
    expect_expire_at: Optional[datetime] = None

    approver_id: Optional[int] = None
    approver_name: str = ""
    approved_at: Optional[datetime] = None
    approve_remark: str = ""

    returned_at: Optional[datetime] = None
    return_remark: str = ""
    is_returned: bool = False

    parent_id: Optional[int] = None
    remark: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class BorrowApplyDetailOut(BorrowApplyOut):
    """详情：额外带审批流水。"""

    records: List[ApprovalRecordOut] = Field(default_factory=list)


class BorrowApplySimpleOut(BaseModel):
    """内嵌到站内信等场景的精简申请信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    apply_no: str
    apply_type: int
    status: int
    status_label: str = ""
    asset_no: str = ""
    asset_name: str = ""


class BorrowQuery(PageQuery):
    """
    申请单筛选条件（管理员看全部，普通用户只能看自己的）。

    与设备列表同理：多选状态等「序列型」条件由接口层显式声明为 Query 参数，
    不放在这里（FastAPI 会把模型里的 List 字段当成请求体）。
    """

    keyword: Optional[str] = Field(default=None, description="单号 / 设备编号 / 设备名称 / 申请人，模糊匹配")
    apply_type: Optional[int] = Field(default=None, ge=1, le=2, description="类型：1=领用 2=续借")
    status: Optional[int] = Field(default=None, ge=0, le=3, description="状态（单选）")
    asset_id: Optional[int] = Field(default=None, description="按设备筛选")
    applicant_id: Optional[int] = Field(default=None, description="按申请人筛选")
    asset_type_id: Optional[int] = Field(default=None, description="按设备类型筛选")
    only_pending: Optional[bool] = Field(default=None, description="只看待审批")
    only_unreturned: Optional[bool] = Field(default=None, description="只看未归还的已通过领用单")
    created_start: Optional[datetime] = Field(default=None, description="提交时间起（含）")
    created_end: Optional[datetime] = Field(default=None, description="提交时间止（含）")
    order_by: Optional[str] = Field(default=None, description="排序字段：id/created_at/expect_expire_at")
    order_desc: bool = Field(default=True, description="是否倒序")


class BorrowStatisticsOut(BaseModel):
    """申请单统计（首页卡片用）。"""

    total: int = 0
    pending: int = 0
    approved: int = 0
    rejected: int = 0
    canceled: int = 0
    unreturned: int = 0
    by_type: dict = Field(default_factory=dict, description="按类型计数")
