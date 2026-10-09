"""认证相关 Schema。"""

from pydantic import BaseModel, Field

from app.schemas.user import UserOut


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50, description="登录账号")
    password: str = Field(..., min_length=1, max_length=64, description="登录密码")


class TokenOut(BaseModel):
    """登录成功返回。"""

    access_token: str = Field(description="访问令牌")
    token_type: str = Field(default="Bearer", description="令牌类型")
    expires_in: int = Field(description="有效期（秒）")
    user: UserOut = Field(description="当前登录用户信息")


class ChangePasswordIn(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=64, description="原密码")
    new_password: str = Field(..., min_length=6, max_length=64, description="新密码，至少 6 位")


class LoginOut(TokenOut):
    """语义别名，保持与 TokenOut 一致。"""
