"""站内信 / 邮件日志 / 提醒日志的请求与响应模型（阶段四）。"""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import PageQuery


# ============================================================
# 站内信
# ============================================================
class NotificationOut(BaseModel):
    """站内信输出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    title: str
    content: str
    notify_type: int
    type_label: str = ""
    biz_type: str = ""
    biz_id: Optional[int] = None
    is_read: int
    read_at: Optional[datetime] = None
    created_at: datetime


class NotificationQuery(PageQuery):
    """站内信列表筛选。"""

    notify_type: Optional[int] = Field(None, ge=1, le=3, description="类型：1=系统 2=审批 3=提醒")
    is_read: Optional[int] = Field(None, ge=0, le=1, description="0=未读 1=已读")
    only_unread: bool = Field(False, description="只看未读（等价于 is_read=0）")


class NotificationSendIn(BaseModel):
    """管理员发送站内信。"""

    user_ids: List[int] = Field(..., min_length=1, description="接收人ID列表")
    title: str = Field(..., min_length=1, max_length=120)
    content: str = Field(..., min_length=1, max_length=2000)

    @field_validator("title", "content")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("不能为空")
        return v


# ============================================================
# 邮件日志
# ============================================================
class MailLogOut(BaseModel):
    """邮件日志输出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    to_email: str
    to_user_id: Optional[int] = None
    subject: str
    status: int
    status_label: str = ""
    error: str = ""
    retry_count: int
    sent_at: Optional[datetime] = None
    biz_type: str = ""
    biz_id: Optional[int] = None
    created_at: datetime


class MailLogDetailOut(MailLogOut):
    """邮件详情（含正文）。"""

    content: str


class MailLogQuery(PageQuery):
    """邮件日志筛选。"""

    status: Optional[int] = Field(None, ge=0, le=2, description="0=待发送 1=已发送 2=失败")
    to_email: Optional[str] = Field(None, max_length=120)
    biz_type: Optional[str] = Field(None, max_length=30)


# ============================================================
# 提醒日志
# ============================================================
class RemindLogOut(BaseModel):
    """提醒日志输出。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    asset_id: Optional[int] = None
    asset_no: str = ""
    apply_id: Optional[int] = None
    user_id: int
    user_name: str = ""
    remind_type: int
    type_label: str = ""
    remind_date: date
    channel: int
    channel_label: str = ""
    status: int
    status_label: str = ""
    message: str = ""
    days_left: Optional[int] = None
    created_at: datetime


class RemindLogQuery(PageQuery):
    """提醒日志筛选。"""

    remind_type: Optional[int] = Field(None, ge=1, le=2)
    channel: Optional[int] = Field(None, ge=1, le=2)
    user_id: Optional[int] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class RemindRunIn(BaseModel):
    """手动触发到期提醒扫描。"""

    days: Optional[int] = Field(
        None, ge=0, le=365, description="提前提醒天数，缺省使用配置 REMIND_DAYS_BEFORE"
    )


class RemindRunOut(BaseModel):
    """提醒扫描结果摘要。"""

    scan_date: str
    days_before: int
    candidates: int
    due_soon: int
    overdue: int
    site_messages: int
    mails: int
    skipped: int
    admin_summary_sent: int
    details: List[dict] = Field(default_factory=list)


class MailRetryOut(BaseModel):
    """邮件重试结果。"""

    scanned: int
    resent: int
    still_failed: int
