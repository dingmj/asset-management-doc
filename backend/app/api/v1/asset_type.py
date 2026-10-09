"""
设备类型接口。

- 查询类接口对已登录用户开放（普通用户浏览设备时需要类型下拉）
- 增删改仅管理员
"""

from typing import Annotated, List, Optional

from fastapi import APIRouter, Query
from sqlalchemy import delete

from app.api.deps import CurrentAdmin, CurrentUser, DbSession
from app.core.exceptions import BizException, conflict, not_found
from app.core.response import ApiResponse, BizCode, ok, ok_page
from app.crud.asset_type import asset_type_crud
from app.models.asset import Asset
from app.schemas.asset_type import (
    AssetTypeCreate,
    AssetTypeOptionOut,
    AssetTypeOut,
    AssetTypeUpdate,
)
from app.schemas.common import PageOut, StatusIn

router = APIRouter(prefix="/asset-types", tags=["10-设备类型"])


@router.get("", response_model=ApiResponse[List[AssetTypeOut]], summary="设备类型列表（登录即可）")
def list_asset_types(
    db: DbSession,
    _: CurrentUser,
    keyword: Optional[str] = Query(default=None, description="名称/编码模糊搜索"),
    status: Optional[int] = Query(default=None, ge=0, le=1, description="状态筛选"),
) -> dict:
    """返回类型列表，附带各类型下的设备数量。"""
    items = asset_type_crud.list_all(db, asset_type_crud.build_query(keyword=keyword, status=status))
    counts = asset_type_crud.count_assets_batch(db, [t.id for t in items])
    return ok([_to_out(t, counts.get(t.id, 0)) for t in items])


@router.get("/options", response_model=ApiResponse[List[AssetTypeOptionOut]], summary="设备类型下拉选项")
def list_asset_type_options(db: DbSession, _: CurrentUser) -> dict:
    return ok(
        [
            AssetTypeOptionOut(label=t.name, value=t.id, code=t.code)
            for t in asset_type_crud.list_enabled(db)
        ]
    )


@router.get("/page", response_model=ApiResponse[PageOut[AssetTypeOut]], summary="设备类型分页（管理员）")
def page_asset_types(
    db: DbSession,
    _: CurrentAdmin,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 10,
    keyword: Optional[str] = Query(default=None),
    status: Optional[int] = Query(default=None, ge=0, le=1),
) -> dict:
    stmt = asset_type_crud.build_query(keyword=keyword, status=status)
    items, total = asset_type_crud.paginate(db, stmt, page, page_size)
    counts = asset_type_crud.count_assets_batch(db, [t.id for t in items])
    return ok_page(
        [_to_out(t, counts.get(t.id, 0)) for t in items], total, page, page_size
    )


@router.post("", response_model=ApiResponse[AssetTypeOut], summary="新增设备类型（管理员）")
def create_asset_type(payload: AssetTypeCreate, db: DbSession, _: CurrentAdmin) -> dict:
    code = payload.code.strip().upper()
    if asset_type_crud.is_code_taken(db, code):
        raise conflict(f"类型编码「{code}」已存在")

    asset_type = asset_type_crud.create(
        db,
        {
            "code": code,
            "name": payload.name.strip(),
            "description": payload.description.strip(),
            "sort": payload.sort,
            "status": payload.status,
        },
    )
    return ok(_to_out(asset_type, 0), message="新增成功")


@router.get("/{type_id}", response_model=ApiResponse[AssetTypeOut], summary="设备类型详情")
def get_asset_type(type_id: int, db: DbSession, _: CurrentUser) -> dict:
    asset_type = _get_or_404(db, type_id)
    return ok(_to_out(asset_type, asset_type_crud.count_assets(db, type_id)))


@router.put("/{type_id}", response_model=ApiResponse[AssetTypeOut], summary="修改设备类型（管理员）")
def update_asset_type(type_id: int, payload: AssetTypeUpdate, db: DbSession, _: CurrentAdmin) -> dict:
    """编码不可修改，避免已录入设备的类型引用失效。"""
    asset_type = _get_or_404(db, type_id)
    values = payload.model_dump(exclude_unset=True)

    if values.get("name") is not None:
        values["name"] = values["name"].strip()
    if values.get("description") is not None:
        values["description"] = values["description"].strip()

    if not values:
        return ok(_to_out(asset_type, asset_type_crud.count_assets(db, type_id)),
                  message="没有需要修改的内容")

    asset_type_crud.update(db, asset_type, values)
    return ok(_to_out(asset_type, asset_type_crud.count_assets(db, type_id)), message="修改成功")


@router.put("/{type_id}/status", response_model=ApiResponse[AssetTypeOut], summary="启用/禁用类型（管理员）")
def change_asset_type_status(type_id: int, payload: StatusIn, db: DbSession, _: CurrentAdmin) -> dict:
    asset_type = _get_or_404(db, type_id)
    asset_type_crud.update(db, asset_type, {"status": payload.status})
    return ok(_to_out(asset_type, asset_type_crud.count_assets(db, type_id)), message="操作成功")


@router.delete("/{type_id}", response_model=ApiResponse[None], summary="删除设备类型（管理员）")
def delete_asset_type(
    type_id: int,
    db: DbSession,
    _: CurrentAdmin,
    purge_deleted_assets: bool = Query(
        default=False,
        description="是否同时彻底清理该类型下已软删除的设备（外键为 RESTRICT，软删除的行仍占用引用）",
    ),
) -> dict:
    """
    删除设备类型。

    两类占用都会拦住删除：
    1. 仍在用的设备（``is_deleted=0``）—— 必须先转移或删除；
    2. 已软删除但仍物理存在于 ``asset`` 表的设备 —— 外键 RESTRICT 会拒绝，
       此时可显式传 ``purge_deleted_assets=true`` 把它们一并物理清理掉。
    """
    asset_type = _get_or_404(db, type_id)

    active = asset_type_crud.count_assets(db, type_id, include_deleted=False)
    if active > 0:
        raise BizException(
            f"该类型下仍有 {active} 台设备，请先转移或删除后再删除类型",
            BizCode.CONFLICT,
        )

    total = asset_type_crud.count_assets(db, type_id, include_deleted=True)
    if total > 0:
        if not purge_deleted_assets:
            raise BizException(
                f"该类型下有 {total} 台已删除设备仍引用此类型，"
                f"如需一并清理请使用 purge_deleted_assets=true",
                BizCode.CONFLICT,
            )
        # 这些设备用户侧已不可见，显式确认后做物理清理，避免外键阻塞
        db.execute(delete(Asset).where(Asset.type_id == type_id, Asset.is_deleted == 1))
        db.commit()

    asset_type_crud.remove(db, asset_type)
    return ok(None, message="删除成功")


# ============================================================
# 内部工具
# ============================================================
def _get_or_404(db, type_id: int):
    asset_type = asset_type_crud.get(db, type_id)
    if asset_type is None:
        raise not_found(f"设备类型不存在（id={type_id}）")
    return asset_type


def _to_out(asset_type, asset_count: int) -> AssetTypeOut:
    data = AssetTypeOut.model_validate(asset_type)
    data.asset_count = asset_count
    return data
