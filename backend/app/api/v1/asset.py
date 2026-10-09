"""
设备接口。

权限划分：
- 查询（列表/详情/下拉/即将到期）：任何已登录用户都能浏览设备库
- 增删改、批量改状态：仅管理员

筛选能力见 ``AssetQuery``，对应文档要求：
按类型、按状态、按领用时间范围、按过期时间范围。

注意：本文件中的路由顺序是刻意的 —— 所有「静态路径」必须写在
``/{asset_id}`` 之前，否则 ``PUT /assets/batch/status`` 会被
``PUT /assets/{asset_id}/status`` 抢先匹配，导致 asset_id="batch" 解析失败。
"""

from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentAdmin, CurrentUser, DbSession
from app.core.exceptions import BizException, conflict, not_found
from app.core.response import ApiResponse, BizCode, ok, ok_page
from app.crud.asset import asset_crud
from app.crud.asset_type import asset_type_crud
from app.models.asset import (
    ASSET_STATUS_AVAILABLE,
    ASSET_STATUS_BORROWED,
    ASSET_STATUS_LABELS,
    Asset,
)
from app.schemas.asset import (
    AssetBatchStatusIn,
    AssetCreate,
    AssetOptionOut,
    AssetOut,
    AssetQuery,
    AssetStatusIn,
    AssetUpdate,
)
from app.schemas.common import IdsIn, PageOut

router = APIRouter(prefix="/assets", tags=["11-设备管理"])


# ============================================================
# 一、查询接口（登录即可）
# ============================================================
@router.get("", response_model=ApiResponse[PageOut[AssetOut]], summary="设备分页列表（动态筛选）")
def list_assets(
    query: Annotated[AssetQuery, Depends()],
    db: DbSession,
    _: CurrentUser,
    status_list: Annotated[
        Optional[List[int]],
        Query(description="设备状态多选，重复传参，如 ?status_list=0&status_list=1"),
    ] = None,
) -> dict:
    """
    动态筛选 + 分页。传什么条件就筛什么，条件之间是 AND。

    常用示例::

        # 按类型 + 状态
        GET /api/v1/assets?type_id=1&status=0

        # 状态多选（闲置 + 维修中）
        GET /api/v1/assets?status_list=0&status_list=2

        # 按领用时间范围
        GET /api/v1/assets?borrow_at_start=2026-01-01 00:00:00&borrow_at_end=2026-12-31 23:59:59

        # 按过期时间范围
        GET /api/v1/assets?expire_at_start=2026-09-01 00:00:00&expire_at_end=2026-09-30 23:59:59

        # 未来 7 天内到期
        GET /api/v1/assets?expire_within_days=7

        # 关键字 + 排序
        GET /api/v1/assets?keyword=ThinkPad&order_by=price&order_desc=false
    """
    stmt = asset_crud.build_query(query, status_list=status_list)
    items, total = asset_crud.paginate(db, stmt, query.page, query.page_size)
    return ok_page([AssetOut.model_validate(a) for a in items], total, query.page, query.page_size)


@router.get("/options", response_model=ApiResponse[List[AssetOptionOut]], summary="可领用设备下拉")
def list_asset_options(
    db: DbSession,
    _: CurrentUser,
    keyword: str = Query(default="", description="编号/名称模糊搜索"),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    """只返回「闲置」状态的设备，供领用申请选择。"""
    assets = asset_crud.list_borrowable(db, keyword=keyword, limit=limit)
    return ok(
        [
            AssetOptionOut(
                label=f"{a.asset_no} {a.name}", value=a.id, asset_no=a.asset_no, status=a.status
            )
            for a in assets
        ]
    )


@router.get("/expiring", response_model=ApiResponse[List[AssetOut]], summary="即将到期 / 已超期设备")
def list_expiring_assets(
    db: DbSession,
    current_user: CurrentUser,
    days: int = Query(default=7, ge=0, le=365, description="未来 N 天内到期"),
    include_expired: bool = Query(default=True, description="是否包含已超期设备"),
    mine_only: bool = Query(default=False, description="仅看本人领用的设备"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """
    到期提醒页面数据源。

    权限：普通用户强制只看自己的；管理员默认看全部，也可传 ``mine_only=true``。
    """
    only_mine = mine_only or not current_user.is_admin

    assets = _collect_assets(db, only_mine=only_mine, user_id=current_user.id,
                             expire_within_days=days, only_expired=False, limit=limit)
    if include_expired:
        assets += _collect_assets(db, only_mine=only_mine, user_id=current_user.id,
                                  expire_within_days=None, only_expired=True, limit=limit)

    # 按到期时间升序：最紧急的排最前
    assets.sort(key=lambda a: (a.expire_at is None, a.expire_at or a.created_at))
    return ok([AssetOut.model_validate(a) for a in assets[:limit]])


@router.get("/status-options", response_model=ApiResponse[List[dict]], summary="设备状态字典")
def list_status_options(_: CurrentUser) -> dict:
    """前端下拉/标签渲染用，避免前端硬编码状态文案。"""
    return ok([{"label": label, "value": value} for value, label in ASSET_STATUS_LABELS.items()])


# ============================================================
# 二、管理员写操作 —— 批量接口（必须早于 /{asset_id} 注册）
# ============================================================
@router.put("/batch/status", response_model=ApiResponse[dict], summary="批量修改设备状态（管理员）")
def batch_change_status(payload: AssetBatchStatusIn, db: DbSession, _: CurrentAdmin) -> dict:
    """领用中的设备会被跳过，避免与未结束的领用单产生冲突。"""
    assets = list(db.scalars(asset_crud.build_query(AssetQuery(), ids=payload.ids)).all())
    if not assets:
        raise not_found("没有找到可操作的设备")

    skipped: List[str] = []
    updated = 0
    for asset in assets:
        if asset.status == ASSET_STATUS_BORROWED and payload.status != ASSET_STATUS_BORROWED:
            skipped.append(f"{asset.asset_no}（领用中）")
            continue
        _apply_status_change(asset, payload.status)
        asset.status = payload.status
        db.add(asset)
        updated += 1

    db.commit()
    message = f"成功更新 {updated} 台设备"
    if skipped:
        message += f"，跳过 {len(skipped)} 台：{'、'.join(skipped)}"
    return ok({"updated": updated, "skipped": skipped}, message=message)


@router.post("/batch/delete", response_model=ApiResponse[dict], summary="批量删除设备（管理员）")
def batch_delete_assets(payload: IdsIn, db: DbSession, _: CurrentAdmin) -> dict:
    assets = list(db.scalars(asset_crud.build_query(AssetQuery(), ids=payload.ids)).all())
    if not assets:
        raise not_found("没有找到可删除的设备")

    skipped: List[str] = []
    deleted = 0
    for asset in assets:
        if asset.status == ASSET_STATUS_BORROWED or asset.borrower_id is not None:
            skipped.append(f"{asset.asset_no}（领用中）")
            continue
        asset.is_deleted = 1
        db.add(asset)
        deleted += 1

    db.commit()
    message = f"成功删除 {deleted} 台设备"
    if skipped:
        message += f"，跳过 {len(skipped)} 台：{'、'.join(skipped)}"
    return ok({"deleted": deleted, "skipped": skipped}, message=message)


# ============================================================
# 三、管理员写操作 —— 单条接口
# ============================================================
@router.post("", response_model=ApiResponse[AssetOut], summary="新增设备（管理员）")
def create_asset(payload: AssetCreate, db: DbSession, _: CurrentAdmin) -> dict:
    asset_no = payload.asset_no.strip().upper()
    if asset_crud.is_asset_no_taken(db, asset_no):
        raise conflict(f"资产编号「{asset_no}」已存在")

    _ensure_type_exists(db, payload.type_id)

    # 「领用中」只能由领用审批流程产生，禁止直接新增
    if payload.status == ASSET_STATUS_BORROWED:
        raise BizException("新增设备时状态不能为「领用中」，请通过领用审批流程变更", BizCode.PARAM_ERROR)

    values = payload.model_dump(exclude={"asset_no"})
    values.update(
        {
            "asset_no": asset_no,
            "name": payload.name.strip(),
            "brand": payload.brand.strip(),
            "model": payload.model.strip(),
            "sn": payload.sn.strip(),
            "location": payload.location.strip(),
            "department": payload.department.strip(),
            "remark": payload.remark.strip(),
            "borrower_id": None,
            "borrow_at": None,
            "expire_at": None,
        }
    )

    asset = asset_crud.create(db, values)
    return ok(AssetOut.model_validate(asset), message="新增成功")


# ============================================================
# 四、按 ID 的单条接口（放在最后，避免抢占静态路径）
# ============================================================
@router.get("/{asset_id}", response_model=ApiResponse[AssetOut], summary="设备详情")
def get_asset(asset_id: int, db: DbSession, _: CurrentUser) -> dict:
    return ok(AssetOut.model_validate(_get_or_404(db, asset_id)))


@router.put("/{asset_id}", response_model=ApiResponse[AssetOut], summary="修改设备（管理员）")
def update_asset(asset_id: int, payload: AssetUpdate, db: DbSession, _: CurrentAdmin) -> dict:
    """
    只更新传入字段。

    注意：领用信息（borrower_id / borrow_at / expire_at）不在更新白名单里，
    只能由领用审批 / 归还流程改写。
    """
    asset = _get_or_404(db, asset_id)
    values = payload.model_dump(exclude_unset=True)

    if values.get("type_id") is not None:
        _ensure_type_exists(db, values["type_id"])

    for field in ("name", "brand", "model", "sn", "location", "department", "remark"):
        if values.get(field) is not None:
            values[field] = values[field].strip()

    if "status" in values:
        _apply_status_change(asset, values["status"])

    if not values:
        return ok(AssetOut.model_validate(asset), message="没有需要修改的内容")

    asset_crud.update(db, asset, values)
    return ok(AssetOut.model_validate(asset), message="修改成功")


@router.put("/{asset_id}/status", response_model=ApiResponse[AssetOut], summary="修改设备状态（管理员）")
def change_asset_status(asset_id: int, payload: AssetStatusIn, db: DbSession, _: CurrentAdmin) -> dict:
    asset = _get_or_404(db, asset_id)
    _apply_status_change(asset, payload.status)

    values: dict = {"status": payload.status}
    if payload.remark:
        values["remark"] = payload.remark.strip()

    asset_crud.update(db, asset, values)
    return ok(
        AssetOut.model_validate(asset),
        message=f"状态已更新为「{ASSET_STATUS_LABELS[payload.status]}」",
    )


@router.delete("/{asset_id}", response_model=ApiResponse[None], summary="删除设备（管理员）")
def delete_asset(asset_id: int, db: DbSession, _: CurrentAdmin) -> dict:
    """软删除设备；领用中的设备必须先归还。"""
    asset = _get_or_404(db, asset_id)
    if asset.status == ASSET_STATUS_BORROWED or asset.borrower_id is not None:
        raise BizException("该设备正在领用中，请先办理归还后再删除", BizCode.CONFLICT)

    asset.is_deleted = 1
    db.add(asset)
    db.commit()
    return ok(None, message="删除成功")


# ============================================================
# 内部工具
# ============================================================
def _get_or_404(db, asset_id: int) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None or asset.is_deleted:
        raise not_found(f"设备不存在（id={asset_id}）")
    return asset


def _ensure_type_exists(db, type_id: int) -> None:
    asset_type = asset_type_crud.get(db, type_id)
    if asset_type is None:
        raise not_found(f"设备类型不存在（id={type_id}）")
    if asset_type.status != 1:
        raise BizException(f"设备类型「{asset_type.name}」已禁用，无法使用", BizCode.BIZ_ERROR)


def _apply_status_change(asset: Asset, new_status: int) -> None:
    """
    状态变更的副作用处理。

    设备从「领用中」改为其它状态（闲置/维修/报废）时，说明领用关系已结束，
    必须清空领用人信息，否则会出现「已报废但还挂着领用人」的脏数据。
    """
    if new_status != ASSET_STATUS_BORROWED and asset.borrower_id is not None:
        asset.borrower_id = None
        asset.borrow_at = None
        asset.expire_at = None


def _collect_assets(db, *, only_mine: bool, user_id: int, expire_within_days, only_expired: bool,
                    limit: int) -> List[Asset]:
    """构造「即将到期」或「已超期」两类查询并执行。"""
    query = AssetQuery(expire_within_days=expire_within_days, only_expired=only_expired or None)
    stmt = asset_crud.build_query(query)
    if only_mine:
        stmt = stmt.where(Asset.borrower_id == user_id)
    return list(db.scalars(stmt.limit(limit)).all())