"""stage1: 用户与角色（sys_user / sys_role / sys_user_role）

Revision ID: 0001
Revises:
Create Date: 2026-09-24

阶段一基线迁移：
- sys_user      系统用户（软删除 is_deleted）
- sys_role      角色字典（内置 ADMIN / USER）
- sys_user_role 用户-角色多对多关联
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# 迁移版本标识
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 统一建表参数，保证中文与 emoji 正常存储
MYSQL_ARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_unicode_ci",
}


def upgrade() -> None:
    # ---------------- sys_role ----------------
    op.create_table(
        "sys_role",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False, comment="角色ID"),
        sa.Column("code", sa.String(length=50), nullable=False, comment="角色编码：ADMIN/USER"),
        sa.Column("name", sa.String(length=50), nullable=False, comment="角色名称"),
        sa.Column("description", sa.String(length=255), server_default=sa.text("''"), nullable=False, comment="角色描述"),
        sa.Column("sort", sa.Integer(), server_default=sa.text("0"), nullable=False, comment="排序值"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False, comment="更新时间"),
        sa.Column("status", sa.SmallInteger(), server_default="1", nullable=False, comment="状态：1=启用，0=禁用"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_role")),
        **MYSQL_ARGS,
    )
    op.create_index(op.f("ix_sys_role_code"), "sys_role", ["code"], unique=True)

    # ---------------- sys_user ----------------
    op.create_table(
        "sys_user",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False, comment="用户ID"),
        sa.Column("username", sa.String(length=50), nullable=False, comment="登录账号"),
        sa.Column("password", sa.String(length=255), nullable=False, comment="密码（bcrypt 哈希）"),
        sa.Column("real_name", sa.String(length=50), server_default=sa.text("''"), nullable=False, comment="真实姓名"),
        sa.Column("email", sa.String(length=120), server_default=sa.text("''"), nullable=False, comment="邮箱（到期提醒用）"),
        sa.Column("phone", sa.String(length=20), server_default=sa.text("''"), nullable=False, comment="手机号"),
        sa.Column("department", sa.String(length=100), server_default=sa.text("''"), nullable=False, comment="所属部门"),
        sa.Column("remark", sa.String(length=255), server_default=sa.text("''"), nullable=False, comment="备注"),
        sa.Column("last_login_at", sa.DateTime(), nullable=True, comment="最后登录时间"),
        sa.Column("is_deleted", sa.SmallInteger(), server_default=sa.text("0"), nullable=False, comment="软删除：1=已删除"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False, comment="更新时间"),
        sa.Column("status", sa.SmallInteger(), server_default="1", nullable=False, comment="状态：1=启用，0=禁用"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_user")),
        **MYSQL_ARGS,
    )
    op.create_index(op.f("ix_sys_user_username"), "sys_user", ["username"], unique=True)

    # ---------------- sys_user_role ----------------
    op.create_table(
        "sys_user_role",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("user_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False, comment="用户ID"),
        sa.Column("role_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False, comment="角色ID"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False, comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False, comment="更新时间"),
        sa.ForeignKeyConstraint(["role_id"], ["sys_role.id"], name=op.f("fk_sys_user_role_role_id_sys_role"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["sys_user.id"], name=op.f("fk_sys_user_role_user_id_sys_user"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sys_user_role")),
        sa.UniqueConstraint("user_id", "role_id", name="uq_sys_user_role_user_role"),
        **MYSQL_ARGS,
    )
    op.create_index(op.f("ix_sys_user_role_role_id"), "sys_user_role", ["role_id"], unique=False)
    op.create_index(op.f("ix_sys_user_role_user_id"), "sys_user_role", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_sys_user_role_user_id"), table_name="sys_user_role")
    op.drop_index(op.f("ix_sys_user_role_role_id"), table_name="sys_user_role")
    op.drop_table("sys_user_role")
    op.drop_index(op.f("ix_sys_user_username"), table_name="sys_user")
    op.drop_table("sys_user")
    op.drop_index(op.f("ix_sys_role_code"), table_name="sys_role")
    op.drop_table("sys_role")
