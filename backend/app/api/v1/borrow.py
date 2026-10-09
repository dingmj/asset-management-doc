"""
领用申请 / 审批 / 归还 / 续借接口。

权限矩阵：
┌──────────────────────────┬──────────┬──────────┐
│ 接口                      │ 普通用户  │ 管理员    │
├──────────────────────────┼──────────┼──────────┤
│ POST   /borrows           │ 提交申请  │ 提交申请  │
│ GET    /borrows/my        │ 仅自己    │ 仅自己    │
│ GET    /borrows           │ ✗        │ 全部      │
│ GET    /borrows/pending   │ ✗        │ 全部待审批│
│ PUT    /borrows/{id}/approve │ ✗     │ 审批      │
│ PUT    /borrows/{id}/cancel  │ 仅自己 │ ✗        │
│ POST   /borrows/{id}/renew   │ 仅自己 │ ✗        │
│ POST   /borrows/{id}/return  │ 仅自己 │ 任意      │
└──────────────────────────┴──────────┴──────────┘

并发安全：审批与归还都会对 ``borrow_apply`` 与 ``asset`` 加行锁
（``SELECT ... FOR UPDATE``），防止同一台设备被两条审批同时占用。
"""

from datetime import datetime, timedelta
from typing import Annotated, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentAdmin, CurrentUser, DbSession
from app.core.exceptions import BizException, forbidden, not_found
from app.core.logging import get_logger
from app.core.response import ApiResponse, BizCode, ok, ok_page
from app.crud.asset import asset_crud
from app.crud.borrow import borrow_crud
from app.models.asset import (
    ASSET_STATUS_AVAILABLE,
    ASSET_STATUS_BORROWED,
    ASSET_STATUS_LABELS,
    ASSET_STATUS_REPAIR,
    ASSET_STATUS_SCRAPPED,
)
from app.models.borrow import (
    ACTION_APPROVE,
    ACTION_CANCEL,
    ACTION_REJECT,
    ACTION_RETURN,
    APPLY_STATUS_APPROVED,
    APPLY_STATUS_CANCELED,
    APPLY_STATUS_LABELS,
    APPLY_STATUS_PENDING,
    APPLY_STATUS_REJECTED,
    APPLY_TYPE_BORROW,
    APPLY_TYPE_RENEW,
    BorrowApply,
)
from app.schemas.borrow import (
    ApprovalRecordOut,
    BorrowApplyCreate,
    BorrowApplyDetailOut,
    BorrowApplyOut,
    BorrowApproveIn,
    BorrowCancelIn,
    BorrowQuery,
    BorrowRenewCreate,
    BorrowReturnIn,
    BorrowStatisticsOut,
)
from app.schemas.common import PageOut
from app.services import notification as notify_service

logger = get_logger(__name__)

router = APIRouter(prefix="/borrows", tags=["12-领用管理"])

# 允许「归还后」指定的设备状态（不允许直接归还成「领用中」）
RETURN_ALLOWED_STATUSES = (ASSET_STATUS_AVAILABLE, ASSET_STATUS_REPAIR, ASSET_STATUS_SCRAPPED)


# ============================================================
# 一、静态路径（必须早于 /{apply_id} 注册）
# ============================================================
@router.post("", response_model=ApiResponse[BorrowApplyOut], summary="提交领用申请")
def create_borrow_apply(
    payload: BorrowApplyCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> dict:
    """
    提交领用申请。

    校验：
    1. 设备必须存在且未删除；
    2. 设备当前必须是「闲置」（可领用）；
    3. 同一台设备同时只能有一条待审批申请（避免抢设备）；
    4. 同一用户对同一台设备不能重复占用。
    """
    asset = db.get(asset_crud.model, payload.asset_id)
    if asset is None or asset.is_deleted:
        raise not_found(f"设备不存在（id={payload.asset_id}）")
    if asset.status != ASSET_STATUS_AVAILABLE:
        raise BizException(
            f"设备「{asset.asset_no}」当前状态为「{ASSET_STATUS_LABELS.get(asset.status, '未知')}」，不可领用",
            BizCode.CONFLICT,
        )
    if asset.borrower_id is not None:
        raise BizException(f"设备「{asset.asset_no}」已被他人领用", BizCode.CONFLICT)
    if borrow_crud.count_pending_by_asset(db, asset.id) > 0:
        raise BizException(f"设备「{asset.asset_no}」已有待审批的申请，请勿重复提交", BizCode.CONFLICT)

    now = datetime.now()
    apply = _create_apply(
        db,
        apply_type=APPLY_TYPE_BORROW,
        asset_id=asset.id,
        applicant_id=current_user.id,
        borrow_days=payload.borrow_days,
        expect_expire_at=now + timedelta(days=payload.borrow_days),
        reason=payload.reason.strip(),
        remark=payload.remark.strip(),
    )

    # 通知管理员有新的待审批申请（站内信，不影响主流程）
    _notify_admins_new_apply(db, apply, asset, current_user)

    return ok(BorrowApplyOut.model_validate(apply), message="申请已提交，等待管理员审批")


@router.get("/my", response_model=ApiResponse[PageOut[BorrowApplyOut]], summary="我的申请列表")
def list_my_applies(
    query: Annotated[BorrowQuery, Depends()],
    db: DbSession,
    current_user: CurrentUser,
    status_list: Annotated[
        Optional[List[int]], Query(description="状态多选，重复传参，如 ?status_list=0&status_list=1")
    ] = None,
) -> dict:
    """普通用户视图：永远只能看到自己的申请。"""
    stmt = borrow_crud.build_query(query, applicant_id=current_user.id, status_list=status_list)
    items, total = borrow_crud.paginate(db, stmt, query.page, query.page_size)
    return ok_page([BorrowApplyOut.model_validate(a) for a in items], total, query.page, query.page_size)


@router.get("/my/current", response_model=ApiResponse[List[BorrowApplyOut]], summary="我当前领用的设备")
def list_my_current(db: DbSession, current_user: CurrentUser) -> dict:
    """已通过且尚未归还的领用单（我的设备页 / 归还入口）。"""
    query = BorrowQuery(page=1, page_size=200, only_unreturned=True)
    stmt = borrow_crud.build_query(query, applicant_id=current_user.id)
    return ok([BorrowApplyOut.model_validate(a) for a in db.scalars(stmt).all()])


@router.get("/status-options", response_model=ApiResponse[List[dict]], summary="申请状态字典")
def list_status_options(_: CurrentUser) -> dict:
    return ok([{"label": label, "value": value} for value, label in APPLY_STATUS_LABELS.items()])


@router.get("/type-options", response_model=ApiResponse[List[dict]], summary="申请类型字典")
def list_type_options(_: CurrentUser) -> dict:
    return ok(
        [
            {"label": "领用申请", "value": APPLY_TYPE_BORROW},
            {"label": "续借申请", "value": APPLY_TYPE_RENEW},
        ]
    )


@router.get("/statistics", response_model=ApiResponse[BorrowStatisticsOut], summary="申请统计")
def borrow_statistics(db: DbSession, current_user: CurrentUser) -> dict:
    """
    统计口径：管理员看全量，普通用户看自己。
    """
    scope = None if current_user.is_admin else current_user.id

    by_status: Dict[int, int] = dict(borrow_crud.count_by_status(db, scope))
    by_type: Dict[int, int] = dict(borrow_crud.count_by_type(db, scope))

    data = BorrowStatisticsOut(
        total=sum(by_status.values()),
        pending=by_status.get(APPLY_STATUS_PENDING, 0),
        approved=by_status.get(APPLY_STATUS_APPROVED, 0),
        rejected=by_status.get(APPLY_STATUS_REJECTED, 0),
        canceled=by_status.get(APPLY_STATUS_CANCELED, 0),
        unreturned=borrow_crud.count_unreturned(db, scope),
        by_type={
            "borrow": by_type.get(APPLY_TYPE_BORROW, 0),
            "renew": by_type.get(APPLY_TYPE_RENEW, 0),
        },
    )
    return ok(data)


@router.get("/pending-count", response_model=ApiResponse[dict], summary="待审批数量（管理员）")
def pending_count(db: DbSession, _: CurrentAdmin) -> dict:
    """前端顶部角标用，避免拉全量列表。"""
    counts = dict(borrow_crud.count_by_status(db))
    return ok({"pending": counts.get(APPLY_STATUS_PENDING, 0)})


@router.get("/pending", response_model=ApiResponse[PageOut[BorrowApplyOut]], summary="待审批列表（管理员）")
def list_pending(
    db: DbSession,
    _: CurrentAdmin,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 10,
) -> dict:
    """审批工作台默认视图：按提交时间正序，先来先审。"""
    query = BorrowQuery(page=page, page_size=page_size, only_pending=True, order_desc=False)
    stmt = borrow_crud.build_query(query)
    items, total = borrow_crud.paginate(db, stmt, page, page_size)
    return ok_page([BorrowApplyOut.model_validate(a) for a in items], total, page, page_size)


@router.get("", response_model=ApiResponse[PageOut[BorrowApplyOut]], summary="全部申请列表（管理员）")
def list_all_applies(
    query: Annotated[BorrowQuery, Depends()],
    db: DbSession,
    _: CurrentAdmin,
    status_list: Annotated[Optional[List[int]], Query(description="状态多选")] = None,
) -> dict:
    """管理员视图：可按状态、类型、申请人、设备、时间范围筛选。"""
    stmt = borrow_crud.build_query(query, status_list=status_list)
    items, total = borrow_crud.paginate(db, stmt, query.page, query.page_size)
    return ok_page([BorrowApplyOut.model_validate(a) for a in items], total, query.page, query.page_size)


# ============================================================
# 二、按 ID 操作
# ============================================================
@router.get("/{apply_id}", response_model=ApiResponse[BorrowApplyDetailOut], summary="申请详情（含审批流水）")
def get_apply(apply_id: int, db: DbSession, current_user: CurrentUser) -> dict:
    apply = _get_or_404(db, apply_id)
    _ensure_can_view(apply, current_user)
    return ok(BorrowApplyDetailOut.model_validate(apply))


@router.put("/{apply_id}/approve", response_model=ApiResponse[BorrowApplyOut], summary="审批申请（管理员）")
def approve_apply(
    apply_id: int,
    payload: BorrowApproveIn,
    db: DbSession,
    current_admin: CurrentAdmin,
) -> dict:
    """
    审批通过 / 驳回。

    通过时对设备加行锁后二次校验状态：
    - 领用：设备必须仍是「闲置」，否则说明已被别人抢先占用 -> 409；
    - 续借：设备当前必须由申请人领用，通过后延长到期时间。
    """
    apply = borrow_crud.get_for_update(db, apply_id)
    if apply is None:
        raise not_found(f"申请单不存在（id={apply_id}）")
    if apply.status != APPLY_STATUS_PENDING:
        raise BizException(
            f"该申请已处理（当前状态：{APPLY_STATUS_LABELS.get(apply.status, '未知')}）", BizCode.CONFLICT
        )

    now = datetime.now()
    asset = asset_crud.get_for_update(db, apply.asset_id)
    if asset is None or asset.is_deleted:
        raise not_found("申请对应的设备已不存在，无法审批")

    if payload.approved:
        if apply.apply_type == APPLY_TYPE_BORROW:
            if asset.status != ASSET_STATUS_AVAILABLE or asset.borrower_id is not None:
                raise BizException(
                    f"设备「{asset.asset_no}」已被占用（当前状态："
                    f"{ASSET_STATUS_LABELS.get(asset.status, '未知')}），无法通过",
                    BizCode.CONFLICT,
                )
            asset_crud.mark_borrowed(
                db,
                asset,
                borrower_id=apply.applicant_id,
                borrow_at=now,
                expire_at=apply.expect_expire_at or (now + timedelta(days=apply.borrow_days)),
            )
        else:  # 续借
            if asset.borrower_id != apply.applicant_id:
                raise BizException(
                    f"设备「{asset.asset_no}」当前并非由申请人领用，无法续借", BizCode.CONFLICT
                )
            # 续借基数：取「当前到期时间」与「此刻」中较晚者，避免已超期的设备续借后仍为负
            base = max(asset.expire_at or now, now)
            new_expire = base + timedelta(days=apply.borrow_days)
            asset.expire_at = new_expire
            db.add(asset)
            apply.expect_expire_at = new_expire

        apply.status = APPLY_STATUS_APPROVED
    else:
        apply.status = APPLY_STATUS_REJECTED

    apply.approver_id = current_admin.id
    apply.approved_at = now
    apply.approve_remark = payload.remark
    db.add(apply)

    borrow_crud.add_record(
        db,
        apply,
        approver_id=current_admin.id,
        action=ACTION_APPROVE if payload.approved else ACTION_REJECT,
        remark=payload.remark,
    )
    db.commit()
    db.refresh(apply)

    # 审批结果通知申请人
    _notify_applicant_result(
        db, apply, asset, approved=payload.approved, remark=payload.remark, admin_name=current_admin.real_name or current_admin.username
    )

    message = "审批通过" if payload.approved else "已驳回"
    return ok(BorrowApplyOut.model_validate(apply), message=message)


@router.put("/{apply_id}/cancel", response_model=ApiResponse[BorrowApplyOut], summary="撤销申请（本人）")
def cancel_apply(
    apply_id: int,
    payload: BorrowCancelIn,
    db: DbSession,
    current_user: CurrentUser,
) -> dict:
    """只有申请人本人可以撤销，且只有待审批状态可以撤销。"""
    apply = borrow_crud.get_for_update(db, apply_id)
    if apply is None:
        raise not_found(f"申请单不存在（id={apply_id}）")
    if apply.applicant_id != current_user.id:
        raise forbidden("只能撤销自己提交的申请")
    if apply.status != APPLY_STATUS_PENDING:
        raise BizException(
            f"当前状态为「{APPLY_STATUS_LABELS.get(apply.status, '未知')}」，无法撤销", BizCode.CONFLICT
        )

    apply.status = APPLY_STATUS_CANCELED
    apply.approve_remark = payload.remark.strip()
    db.add(apply)
    borrow_crud.add_record(
        db, apply, approver_id=current_user.id, action=ACTION_CANCEL, remark=payload.remark
    )
    db.commit()
    db.refresh(apply)
    return ok(BorrowApplyOut.model_validate(apply), message="已撤销申请")


@router.post("/{apply_id}/renew", response_model=ApiResponse[BorrowApplyOut], summary="提交续借申请（本人）")
def renew_apply(
    apply_id: int,
    payload: BorrowRenewCreate,
    db: DbSession,
    current_user: CurrentUser,
) -> dict:
    """
    针对一张「已通过且未归还」的领用单发起续借。

    续借本身仍然需要审批；待审批期间设备到期时间不变，
    审批通过后才延长。
    """
    origin = _get_or_404(db, apply_id)
    if origin.applicant_id != current_user.id:
        raise forbidden("只能为自己的领用单申请续借")
    if origin.apply_type != APPLY_TYPE_BORROW:
        raise BizException("只有领用单可以续借", BizCode.BIZ_ERROR)
    if origin.status != APPLY_STATUS_APPROVED:
        raise BizException("只有审批通过的领用单可以续借", BizCode.CONFLICT)
    if origin.returned_at is not None:
        raise BizException("该设备已归还，无法续借", BizCode.CONFLICT)
    if borrow_crud.count_pending_renew(db, origin.id) > 0:
        raise BizException("该领用单已有待审批的续借申请，请等待处理", BizCode.CONFLICT)

    asset = db.get(asset_crud.model, origin.asset_id)
    if asset is None or asset.is_deleted:
        raise not_found("续借对应的设备已不存在")
    if asset.borrower_id != current_user.id:
        raise BizException("该设备当前不在您名下，无法续借", BizCode.CONFLICT)

    now = datetime.now()
    base = max(asset.expire_at or now, now)
    renew = _create_apply(
        db,
        apply_type=APPLY_TYPE_RENEW,
        asset_id=asset.id,
        applicant_id=current_user.id,
        borrow_days=payload.days,
        expect_expire_at=base + timedelta(days=payload.days),
        reason=payload.reason.strip(),
        remark=payload.remark.strip(),
        parent_id=origin.id,
    )
    return ok(BorrowApplyOut.model_validate(renew), message="续借申请已提交，等待管理员审批")


@router.post("/{apply_id}/return", response_model=ApiResponse[BorrowApplyOut], summary="归还设备")
def return_asset(
    apply_id: int,
    payload: BorrowReturnIn,
    db: DbSession,
    current_user: CurrentUser,
) -> dict:
    """
    归还设备（本人或管理员）。

    归还**不需要审批**，立即生效：
    1. 清空设备的领用人 / 领用时间 / 到期时间；
    2. 把设备状态改为指定状态（默认「闲置」，可选「维修中 / 已报废」）；
    3. 回填领用单的 ``returned_at`` 并写一条 ``ACTION_RETURN`` 流水。
    """
    if payload.to_status not in RETURN_ALLOWED_STATUSES:
        raise BizException("归还后的设备状态只能是：0=闲置 / 2=维修中 / 3=已报废", BizCode.PARAM_ERROR)

    apply = borrow_crud.get_for_update(db, apply_id)
    if apply is None:
        raise not_found(f"领用单不存在（id={apply_id}）")

    if not current_user.is_admin and apply.applicant_id != current_user.id:
        raise forbidden("只能归还自己领用的设备")
    if apply.apply_type != APPLY_TYPE_BORROW:
        raise BizException("只有领用单可以归还", BizCode.BIZ_ERROR)
    if apply.status != APPLY_STATUS_APPROVED:
        raise BizException("只有审批通过的领用单可以归还", BizCode.CONFLICT)
    if apply.returned_at is not None:
        raise BizException("该设备已归还，请勿重复操作", BizCode.CONFLICT)

    asset = asset_crud.get_for_update(db, apply.asset_id)
    if asset is None or asset.is_deleted:
        raise not_found("归还对应的设备已不存在")

    # 归还后设备不再挂领用人（无论归还成闲置、维修还是报废）
    asset_crud.mark_returned(db, asset, status=payload.to_status)

    now = datetime.now()
    apply.returned_at = now
    apply.return_remark = payload.remark.strip()
    # 若此前没有审批人（历史数据），管理员归还时补记操作人，便于追溯
    if current_user.is_admin and apply.approver_id is None:
        apply.approver_id = current_user.id
    db.add(apply)

    borrow_crud.add_record(
        db, apply, approver_id=current_user.id, action=ACTION_RETURN, remark=payload.remark
    )
    db.commit()
    db.refresh(apply)

    label = ASSET_STATUS_LABELS.get(payload.to_status, "未知")
    return ok(BorrowApplyOut.model_validate(apply), message=f"归还成功，设备状态已更新为「{label}」")


@router.get("/{apply_id}/records", response_model=ApiResponse[List[ApprovalRecordOut]], summary="审批流水")
def list_apply_records(apply_id: int, db: DbSession, current_user: CurrentUser) -> dict:
    apply = _get_or_404(db, apply_id)
    _ensure_can_view(apply, current_user)
    return ok([ApprovalRecordOut.model_validate(r) for r in apply.records])


# ============================================================
# 内部工具
# ============================================================
def _get_or_404(db, apply_id: int) -> BorrowApply:
    apply = db.get(BorrowApply, apply_id)
    if apply is None:
        raise not_found(f"申请单不存在（id={apply_id}）")
    return apply


def _notify_admins_new_apply(db, apply: BorrowApply, asset, applicant) -> None:
    """有新申请时给管理员发站内信（失败不影响主流程）。"""
    try:
        admins = notify_service.admin_user_ids(db)
        if not admins:
            return
        type_label = "领用" if apply.apply_type == APPLY_TYPE_BORROW else "续借"
        notify_service.notify_many(
            db,
            admins,
            title=f"【待审批】{applicant.real_name or applicant.username} 提交了{type_label}申请",
            content=(
                f"申请单号：{apply.apply_no}\n"
                f"申请人：{applicant.real_name or applicant.username}\n"
                f"设备：{asset.asset_no}（{asset.name}）\n"
                f"申请天数：{apply.borrow_days} 天\n"
                f"申请理由：{apply.reason or '（未填写）'}\n\n"
                "请前往「审批管理」处理。"
            ),
            notify_type=2,  # 审批结果类通知
            biz_type="borrow_apply",
            biz_id=apply.id,
        )
    except Exception as exc:  # noqa: BLE001 - 通知属于旁路，不能影响申请提交
        logger.warning("发送待审批通知失败：%s", exc)


def _notify_applicant_result(
    db, apply: BorrowApply, asset, *, approved: bool, remark: str, admin_name: str
) -> None:
    """审批结果通知申请人，并追加一条申请人的站内信。"""
    try:
        type_label = "领用" if apply.apply_type == APPLY_TYPE_BORROW else "续借"
        result_label = "已通过" if approved else "被驳回"
        if approved and apply.apply_type == APPLY_TYPE_BORROW:
            tail = f"到期时间：{apply.expect_expire_at:%Y-%m-%d %H:%M}，请按时归还或续借。"
        elif approved:
            tail = f"新的到期时间：{apply.expect_expire_at:%Y-%m-%d %H:%M}。"
        else:
            tail = "如有疑问请联系管理员。"

        notify_service.notify(
            db,
            apply.applicant_id,
            title=f"【审批结果】{type_label}申请{result_label}",
            content=(
                f"申请单号：{apply.apply_no}\n"
                f"设备：{asset.asset_no}（{asset.name}）\n"
                f"审批人：{admin_name}\n"
                f"审批意见：{remark or '（无）'}\n\n{tail}"
            ),
            notify_type=2,
            biz_type="borrow_apply",
            biz_id=apply.id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("发送审批结果通知失败：%s", exc)


def _ensure_can_view(apply: BorrowApply, user) -> None:
    """普通用户只能看自己的申请，管理员可看全部。"""
    if not user.is_admin and apply.applicant_id != user.id:
        raise forbidden("无权查看他人的申请")


def _create_apply(
    db,
    *,
    apply_type: int,
    asset_id: int,
    applicant_id: int,
    borrow_days: int,
    expect_expire_at: datetime,
    reason: str,
    remark: str,
    parent_id: Optional[int] = None,
) -> BorrowApply:
    """
    创建申请单。

    单号由「当日序号 + 1」生成，高并发下可能撞唯一键，
    这里最多重试 3 次。
    """
    last_error: Optional[Exception] = None
    for _ in range(3):
        apply = BorrowApply(
            apply_no=borrow_crud.generate_apply_no(db, apply_type),
            apply_type=apply_type,
            asset_id=asset_id,
            applicant_id=applicant_id,
            borrow_days=borrow_days,
            expect_expire_at=expect_expire_at,
            status=APPLY_STATUS_PENDING,
            reason=reason,
            remark=remark,
            parent_id=parent_id,
        )
        db.add(apply)
        try:
            db.commit()
            db.refresh(apply)
            return apply
        except IntegrityError as exc:  # 单号重复，换一个再试
            db.rollback()
            last_error = exc
    raise BizException("申请单号生成失败，请重试", BizCode.SERVER_ERROR) from last_error
