"""
安全工具：密码哈希 + JWT 签发/校验。

- 密码：passlib[bcrypt]，数据库只存哈希，永不存明文；
- 令牌：PyJWT，HS256 对称签名，payload 内含 sub(用户ID)、username、roles、exp、iat。
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import jwt
from passlib.context import CryptContext

from app.core.config import settings
from app.core.exceptions import BizException
from app.core.response import BizCode

# bcrypt 单次哈希开销因子（12 兼顾安全与登录响应速度）
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

# bcrypt 算法只处理前 72 字节，超长密码需先安全截断，否则底层会直接报错
_BCRYPT_MAX_BYTES = 72


def _truncate(raw: str) -> str:
    return raw.encode("utf-8")[:_BCRYPT_MAX_BYTES].decode("utf-8", "ignore")


def hash_password(raw_password: str) -> str:
    """生成密码哈希。"""
    return pwd_context.hash(_truncate(raw_password))


def verify_password(raw_password: str, hashed_password: str) -> bool:
    """校验明文密码与哈希是否匹配；哈希串损坏时返回 False 而不是抛异常。"""
    try:
        return pwd_context.verify(_truncate(raw_password), hashed_password)
    except (ValueError, TypeError):
        return False


def create_access_token(
    user_id: int,
    username: str,
    roles: Optional[list[str]] = None,
    expires_minutes: Optional[int] = None,
) -> tuple[str, int]:
    """
    签发访问令牌。

    :return: (token, 有效期秒数)
    """
    expire_minutes = expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=expire_minutes)
    payload: Dict[str, Any] = {
        "sub": str(user_id),  # JWT 规范要求 sub 为字符串
        "username": username,
        "roles": roles or [],
        "iat": now,
        "exp": expire,
    }
    token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return token, expire_minutes * 60


def decode_token(token: str) -> Dict[str, Any]:
    """
    解析并校验令牌，失败统一抛 BizException。

    - 过期 -> TOKEN_EXPIRED（前端据此跳登录页并提示“登录已过期”）
    - 其他 -> UNAUTHORIZED
    """
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise BizException("登录已过期，请重新登录", BizCode.TOKEN_EXPIRED)
    except jwt.PyJWTError:
        raise BizException("无效的登录凭证，请重新登录", BizCode.UNAUTHORIZED)
