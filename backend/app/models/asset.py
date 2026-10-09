"""
设备类型与设备模型（阶段二核心）。

表：
- ``asset_type`` 设备类型字典（笔记本 / 显示器 / 办公桌椅 ...）
- ``asset``      设备台账，承载「领用状态 + 领用时间 + 到期时间」，
                 因此阶段二的动态筛选（类型 / 状态 / 领用时间范围 / 过期时间范围）
                 可以直接落在单表上，无需 join 领用单。

设计说明：
- 设备状态用 0~3 表达，与 ``borrow_apply`` 的申请状态互不干扰；
- 领用人 / 领用时间 / 到期时间由阶段三的审批通过动作回写到 ``asset``，
  保证「设备列表」一屏即可看到谁在用、什么时候到期；
- 软删除 ``is_deleted``，历史领用记录不会变成孤儿数据。
"""

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import MYSQL_TABLE_ARGS, Base
from app.models.mixins import BigIntFK, BigIntPK, StatusMixin, TimestampMixin

if TYPE_CHECKING:  # pragma: no cover - 仅类型提示，避免循环导入
    from app.models.user import SysUser

# ==================== 设备状态 ====================
ASSET_STATUS_AVAILABLE = 0  # 闲置，可领用
ASSET_STATUS_BORROWED = 1  # 领用中
ASSET_STATUS_REPAIR = 2  # 维修中
ASSET_STATUS_SCRAPPED = 3  # 已报废

ASSET_STATUS_LABELS = {
    ASSET_STATUS_AVAILABLE: "闲置",
    ASSET_STATUS_BORROWED: "领用中",
    ASSET_STATUS_REPAIR: "维修中",
    ASSET_STATUS_SCRAPPED: "已报废",
}

# 可被申请领用的状态集合（阶段三提交申请时校验）
BORROWABLE_STATUSES = (ASSET_STATUS_AVAILABLE,)


class AssetType(Base, TimestampMixin, StatusMixin):
    """设备类型字典表。"""

    __tablename__ = "asset_type"
    __table_args__ = (dict(MYSQL_TABLE_ARGS),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True, comment="类型ID")
    code: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True, comment="类型编码，如 LAPTOP"
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="类型名称，如 笔记本电脑")
    description: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=text("''"), comment="类型描述"
    )
    sort: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), comment="排序值，越小越靠前"
    )

    # 反向关系：该类型下的设备（列表页不需要，用 lazy="raise" 防止误触发 N+1）
    assets: Mapped[List["Asset"]] = relationship(
        back_populates="asset_type",
        lazy="raise",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AssetType id={self.id} code={self.code!r} name={self.name!r}>"


class Asset(Base, TimestampMixin):
    """设备台账表。"""

    __tablename__ = "asset"
    # 最常用的组合筛选（类型 + 状态）单独建联合索引
    __table_args__ = (
        Index("ix_asset_type_status", "type_id", "status"),
        Index("ix_asset_expire_at", "expire_at"),
        dict(MYSQL_TABLE_ARGS),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True, comment="设备ID")
    asset_no: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True, comment="资产编号，唯一"
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="设备名称")
    # 说明：type_id 不单独建索引，(type_id, status) 联合索引已能满足
    #       「按类型筛选」与 MySQL 外键对索引的要求
    type_id: Mapped[int] = mapped_column(
        BigIntFK,
        ForeignKey("asset_type.id", ondelete="RESTRICT"),
        nullable=False,
        comment="设备类型ID",
    )
    brand: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=text("''"), comment="品牌"
    )
    model: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default=text("''"), comment="型号"
    )
    sn: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default=text("''"), comment="序列号/SN"
    )
    price: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0.00"), comment="采购价格（元）"
    )
    location: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default=text("''"), comment="存放位置"
    )
    department: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default=text("''"), comment="归属部门"
    )
    status: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=ASSET_STATUS_AVAILABLE,
        server_default=text("0"),
        index=True,
        comment="设备状态：0=闲置 1=领用中 2=维修中 3=已报废",
    )
    purchase_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="采购日期")
    warranty_until: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="保修截止日期")

    # ---------- 领用信息（阶段三审批通过时回写） ----------
    borrower_id: Mapped[Optional[int]] = mapped_column(
        BigIntFK,
        ForeignKey("sys_user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="当前领用人ID",
    )
    borrow_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, index=True, comment="领用（起租）时间"
    )
    expire_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="领用到期时间，到期提醒依据"
    )

    remark: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=text("''"), comment="备注"
    )
    is_deleted: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0"), comment="软删除：1=已删除"
    )

    # ---------- 关系 ----------
    # 使用 selectin：一条额外查询批量加载，不污染主查询的 LIMIT/COUNT
    asset_type: Mapped["AssetType"] = relationship(back_populates="assets", lazy="selectin")
    borrower: Mapped[Optional["SysUser"]] = relationship(lazy="selectin")

    # ---------- 便捷属性（供 Pydantic from_attributes 直接读取） ----------
    @property
    def type_name(self) -> str:
        return self.asset_type.name if self.asset_type is not None else ""

    @property
    def type_code(self) -> str:
        return self.asset_type.code if self.asset_type is not None else ""

    @property
    def borrower_name(self) -> str:
        """优先展示真实姓名，没有则退回账号。"""
        if self.borrower is None:
            return ""
        return self.borrower.real_name or self.borrower.username

    @property
    def status_label(self) -> str:
        return ASSET_STATUS_LABELS.get(self.status, "未知")

    @property
    def days_to_expire(self) -> Optional[int]:
        """
        距离到期还有多少天。

        - 未领用（无到期时间）-> None
        - 负数表示已超期
        """
        if self.expire_at is None:
            return None
        return (self.expire_at - datetime.now()).days

    @property
    def is_expired(self) -> bool:
        """是否已超期未归还。"""
        return self.expire_at is not None and self.expire_at < datetime.now()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Asset id={self.id} asset_no={self.asset_no!r} status={self.status}>"
