"""管理端认证：复用商城账号体系，JWT claim 增加 role，接口统一校验管理员角色。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from ..api.errors import ShopError
from ..api.security import bearer_token, create_token, decode_token, decode_token_role
from ..api.service import ShopService, User


def create_require_admin(service: ShopService):
    """构建 require_admin 依赖：校验 Bearer 令牌且角色为 admin。"""

    def require_admin(token: Annotated[str, Depends(bearer_token)]) -> User:
        user_id = decode_token(token)
        if decode_token_role(token) != "admin":
            raise ShopError(403, "forbidden", "需要管理员权限")
        return service.get_user(user_id)

    return require_admin


def admin_login(service: ShopService, username: str, password: str) -> dict:
    user = service.authenticate(username, password)
    if user.role != "admin":
        raise ShopError(403, "forbidden", "该账号不是管理员")
    return {"access_token": create_token(user.id, role=user.role), "token_type": "bearer"}
