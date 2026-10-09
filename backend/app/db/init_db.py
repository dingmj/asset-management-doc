"""
建表与种子数据（幂等，可重复执行）。

用法::

    # 推荐流程：先用 Alembic 建表，再灌种子数据
    alembic upgrade head
    python -m app.db.init_db

    # 本地快速验证（不推荐生产）：直接 create_all + 灌数据
    python -m app.db.init_db --create

    # 强制重建演示设备数据（不动用户/角色）
    python -m app.db.init_db --reset-assets

种子账号：
    管理员    admin / admin123
    普通用户  user  / user123
    演示用户  lisi / wangwu / zhaoliu（密码均为 user123）
"""

import sys
from datetime import date, datetime, timedelta
from typing import Dict

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.core.security import hash_password
from app.db.seed_data import (
    ASSETS,
    ASSET_TYPES,
    BORROW_PLAN,
    BUILTIN_ROLES,
    PENDING_APPLIES,
    SEED_USERS,
)
from app.db.session import SessionLocal, engine
from app.models.asset import (
    ASSET_STATUS_AVAILABLE,
    ASSET_STATUS_BORROWED,
    Asset,
    AssetType,
)
from app.models.borrow import (
    ACTION_APPROVE,
    APPLY_STATUS_APPROVED,
    APPLY_STATUS_PENDING,
    APPLY_TYPE_BORROW,
    ApprovalRecord,
    BorrowApply,
)
from app.models.role import ROLE_ADMIN, ROLE_USER, SysRole, SysUserRole
from app.models.user import SysUser

logger = get_logger(__name__)


# ============================================================
# 角色
# ============================================================
def seed_roles(db: Session) -> Dict[str, SysRole]:
    """写入内置角色，已存在则复用并同步文案。"""
    roles: Dict[str, SysRole] = {}
    for code, name, description, sort in BUILTIN_ROLES:
        role = db.scalars(select(SysRole).where(SysRole.code == code).limit(1)).first()
        if role is None:
            role = SysRole(code=code, name=name, description=description, sort=sort, status=1)
            db.add(role)
            db.flush()
            logger.info("创建角色：%s(%s)", name, code)
        else:
            role.name, role.description, role.sort = name, description, sort
        roles[code] = role
    db.commit()
    return roles


# ============================================================
# 用户
# ============================================================
def seed_users(db: Session, roles: Dict[str, SysRole]) -> Dict[str, SysUser]:
    """写入种子用户并绑定角色；已存在则跳过（不覆盖已修改的密码）。"""
    users: Dict[str, SysUser] = {}
    for username, password, real_name, email, phone, department, role_code, remark in SEED_USERS:
        user = db.scalars(select(SysUser).where(SysUser.username == username).limit(1)).first()
        if user is None:
            user = SysUser(
                username=username,
                password=hash_password(password),
                real_name=real_name,
                email=email,
                phone=phone,
                department=department,
                remark=remark,
                status=1,
                is_deleted=0,
            )
            db.add(user)
            db.flush()
            db.add(SysUserRole(user_id=user.id, role_id=roles[role_code].id))
            db.commit()
            logger.info("创建用户：%-8s / %-8s （%s，%s）", username, password, real_name, role_code)
        else:
            logger.info("用户已存在，跳过：%s", username)
        users[username] = user

    # 兼容 .env 中自定义的种子账号名
    for username, password, role_code in (
        (settings.ADMIN_USERNAME, settings.ADMIN_PASSWORD, ROLE_ADMIN),
        (settings.NORMAL_USERNAME, settings.NORMAL_PASSWORD, ROLE_USER),
    ):
        if username in users:
            continue
        user = db.scalars(select(SysUser).where(SysUser.username == username).limit(1)).first()
        if user is None:
            user = SysUser(
                username=username,
                password=hash_password(password),
                real_name="系统管理员" if role_code == ROLE_ADMIN else "普通用户",
                email=f"{username}@example.com",
                status=1,
                is_deleted=0,
            )
            db.add(user)
            db.flush()
            db.add(SysUserRole(user_id=user.id, role_id=roles[role_code].id))
            db.commit()
            logger.info("创建自定义种子用户：%s / %s（%s）", username, password, role_code)
        users[username] = user

    return users


# ============================================================
# 设备类型
# ============================================================
def seed_asset_types(db: Session) -> Dict[str, AssetType]:
    """写入设备类型字典。"""
    types: Dict[str, AssetType] = {}
    for code, name, description, sort in ASSET_TYPES:
        asset_type = db.scalars(select(AssetType).where(AssetType.code == code).limit(1)).first()
        if asset_type is None:
            asset_type = AssetType(
                code=code, name=name, description=description, sort=sort, status=1
            )
            db.add(asset_type)
            db.flush()
            logger.info("创建设备类型：%s(%s)", name, code)
        else:
            asset_type.name, asset_type.description, asset_type.sort = name, description, sort
        types[code] = asset_type
    db.commit()
    return types


# ============================================================
# 设备台账
# ============================================================
def seed_assets(
    db: Session,
    types: Dict[str, AssetType],
    users: Dict[str, SysUser],
    reset: bool = False,
) -> int:
    """
    写入演示设备。

    :param reset: True 时先清空 asset 表再重建，用于反复演示
    :return: 新建（或重建）的设备数量
    """
    if reset:
        db.execute(delete(Asset))
        db.commit()
        logger.info("已清空 asset 表，准备重建演示设备")

    now = datetime.now()
    created = 0

    for row in ASSETS:
        (
            asset_no,
            name,
            type_code,
            brand,
            model,
            sn,
            price,
            location,
            department,
            purchase_date,
            warranty_until,
            status,
        ) = row

        exists = db.scalars(select(Asset).where(Asset.asset_no == asset_no).limit(1)).first()
        if exists is not None and not reset:
            continue

        borrow_plan = BORROW_PLAN.get(asset_no)
        borrower = users.get(borrow_plan[0]) if borrow_plan else None

        asset = Asset(
            asset_no=asset_no,
            name=name,
            type_id=types[type_code].id,
            brand=brand,
            model=model,
            sn=sn,
            price=price,
            location=location,
            department=department,
            status=status,
            purchase_date=date.fromisoformat(purchase_date),
            warranty_until=date.fromisoformat(warranty_until),
            remark="",
            is_deleted=0,
        )

        # 领用中的设备：回写领用人 / 领用时间 / 到期时间，供到期提醒与筛选演示
        if status == ASSET_STATUS_BORROWED and borrower is not None:
            borrowed_days, expire_in_days = borrow_plan[1], borrow_plan[2]
            asset.borrower_id = borrower.id
            asset.borrow_at = now - timedelta(days=borrowed_days)
            asset.expire_at = now + timedelta(days=expire_in_days)

        db.add(asset)
        created += 1

    db.commit()
    logger.info("写入设备台账：%d 台", created)
    return created


# ============================================================
# 领用单 / 待审批申请
# ============================================================
def seed_borrow_applies(db: Session, users: Dict[str, SysUser], admin: SysUser) -> None:
    """
    为演示数据补上对应的领用单，保证「设备状态」与「领用单」一致。

    - ``BORROW_PLAN`` 里的设备：生成一张已通过且未归还的领用单 + 审批流水；
    - ``PENDING_APPLIES``：生成若干待审批申请，供管理员审批工作台演示。

    幂等：已存在同设备的对应单据时直接跳过。
    """
    now = datetime.now()
    created_approved = 0
    created_pending = 0

    # ---------- 已通过的领用单（与 asset.borrow_* 对齐） ----------
    for asset_no, (username, borrowed_days, expire_in_days) in BORROW_PLAN.items():
        asset = db.scalars(select(Asset).where(Asset.asset_no == asset_no).limit(1)).first()
        applicant = users.get(username)
        if asset is None or applicant is None:
            continue

        exists = db.scalars(
            select(BorrowApply)
            .where(
                BorrowApply.asset_id == asset.id,
                BorrowApply.apply_type == APPLY_TYPE_BORROW,
                BorrowApply.status == APPLY_STATUS_APPROVED,
                BorrowApply.returned_at.is_(None),
            )
            .limit(1)
        ).first()
        if exists is not None:
            continue

        borrow_at = now - timedelta(days=borrowed_days)
        expire_at = now + timedelta(days=expire_in_days)

        apply = BorrowApply(
            apply_no=_seed_apply_no(db, APPLY_TYPE_BORROW),
            apply_type=APPLY_TYPE_BORROW,
            asset_id=asset.id,
            applicant_id=applicant.id,
            reason="项目开发需要，申请长期使用",
            borrow_days=max(borrowed_days + expire_in_days, 1),
            expect_expire_at=expire_at,
            status=APPLY_STATUS_APPROVED,
            approver_id=admin.id,
            approved_at=borrow_at,
            approve_remark="同意",
            remark="",
        )
        db.add(apply)
        db.flush()
        db.add(
            ApprovalRecord(
                apply_id=apply.id,
                approver_id=admin.id,
                action=ACTION_APPROVE,
                remark="同意",
            )
        )
        # 让「提交时间」看起来早于审批时间，更贴近真实流程
        apply.created_at = borrow_at - timedelta(hours=3)
        created_approved += 1

    db.commit()

    # ---------- 待审批申请 ----------
    for asset_no, username, days, reason in PENDING_APPLIES:
        asset = db.scalars(select(Asset).where(Asset.asset_no == asset_no).limit(1)).first()
        applicant = users.get(username)
        if asset is None or applicant is None:
            continue

        exists = db.scalars(
            select(BorrowApply)
            .where(
                BorrowApply.asset_id == asset.id,
                BorrowApply.status == APPLY_STATUS_PENDING,
            )
            .limit(1)
        ).first()
        if exists is not None:
            continue

        db.add(
            BorrowApply(
                apply_no=_seed_apply_no(db, APPLY_TYPE_BORROW),
                apply_type=APPLY_TYPE_BORROW,
                asset_id=asset.id,
                applicant_id=applicant.id,
                reason=reason,
                borrow_days=days,
                expect_expire_at=now + timedelta(days=days),
                status=APPLY_STATUS_PENDING,
                remark="",
            )
        )
        created_pending += 1

    db.commit()
    logger.info("写入领用单：已通过 %d 张 / 待审批 %d 张", created_approved, created_pending)


def _seed_apply_no(db: Session, apply_type: int) -> str:
    """
    复用 CRUD 里的单号生成逻辑，保证种子数据与接口产出格式一致。

    注意：会话是 ``autoflush=False``，必须先 flush，否则本批次刚 ``add`` 的
    单据对 ``generate_apply_no`` 的查询不可见，会生成重复单号触发唯一键冲突。
    """
    from app.crud.borrow import borrow_crud

    db.flush()
    return borrow_crud.generate_apply_no(db, apply_type)


# ============================================================
# 汇总入口
# ============================================================
def init_seed_data(db: Session, reset_assets: bool = False) -> None:
    """完整的种子数据流程。"""
    roles = seed_roles(db)
    users = seed_users(db, roles)
    types = seed_asset_types(db)
    seed_assets(db, types, users, reset=reset_assets)
    seed_borrow_applies(db, users, admin=users[settings.ADMIN_USERNAME])

    logger.info(
        "统计：角色 %d 个 / 用户 %d 个 / 类型 %d 个 / 设备 %d 台",
        len(roles),
        len(users),
        len(types),
        count_rows(db, Asset),
    )


def count_rows(db: Session, model) -> int:
    """统计某张表的行数（仅用于日志展示）。"""
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def main() -> None:
    setup_logging()

    if "--create" in sys.argv:
        from app.db.base import Base
        import app.models  # noqa: F401  确保模型全部注册

        logger.info("按模型定义创建所有表 ...")
        Base.metadata.create_all(bind=engine)

    reset_assets = "--reset-assets" in sys.argv

    db = SessionLocal()
    try:
        init_seed_data(db, reset_assets=reset_assets)
        logger.info("种子数据初始化完成 ✅")
        logger.info("管理员　：%s / %s", settings.ADMIN_USERNAME, settings.ADMIN_PASSWORD)
        logger.info("普通用户：%s / %s", settings.NORMAL_USERNAME, settings.NORMAL_PASSWORD)
        logger.info("演示用户：lisi / wangwu / zhaoliu（密码均为 user123）")
    except Exception:
        db.rollback()
        logger.exception("种子数据初始化失败")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
