"""管理后台后端：/admin/api/v1 路由，业务规则一律调用 ShopService。"""

from .main import create_admin_router

__all__ = ["create_admin_router"]
