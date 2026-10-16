"""积分商城的 Streamable HTTP MCP 适配层。"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request

from shop.api.errors import ShopError
from shop.api.security import decode_token
from shop.api.service import CartItem, Product, ShopService, User
from shop.mcp.oauth import ShopOAuthProvider


def create_mcp_app(service: ShopService, base_url: str | None = None) -> Any:
    """创建挂载到商城 ASGI 应用的 MCP 服务。"""
    provider = ShopOAuthProvider(service, base_url)
    mcp = FastMCP(
        "积分商城",
        instructions="通过工具查询积分商城、管理自己的购物车，并在用户明确确认后下单。",
        auth=provider,
    )

    def current_user() -> User:
        authorization = get_http_request().headers.get("authorization")
        if not authorization or not authorization.startswith("Bearer "):
            raise ShopError(401, "authentication_required", "需要访问令牌")
        return service.get_user(decode_token(authorization[7:]))

    def product_data(product: Product) -> dict[str, object]:
        return {
            "id": product.id,
            "name": product.name,
            "description": product.description,
            "points_price": product.points_price,
            "stock_status": "in_stock" if product.stock > 0 else "out_of_stock",
            "category_id": product.category_id,
            "image_url": product.image_url,
        }

    def cart_item_data(item: CartItem) -> dict[str, object]:
        product = service.get_product(item.product_id)
        return {
            "id": item.id,
            "product_id": item.product_id,
            "quantity": item.quantity,
            "product": product_data(product),
            "subtotal": product.points_price * item.quantity,
        }

    @mcp.tool(annotations={"readOnlyHint": True})
    def search_products(
        query: str | None = None, category_id: str | None = None, page: int = 1
    ) -> dict[str, object]:
        """检索可售商品；可按中文关键词、分类和页码筛选，返回积分价与库存状态。"""
        if page < 1:
            raise ShopError(422, "validation_error", "页码必须大于零")
        products = service.list_products(query, category_id)
        page_size = 20
        start = (page - 1) * page_size
        return {"items": [product_data(p) for p in products[start : start + page_size]], "page": page, "total": len(products)}

    @mcp.tool(annotations={"readOnlyHint": True})
    def get_product(product_id: str) -> dict[str, object]:
        """查看一个商品的详情。传入商品 ID，返回描述、积分价和库存状态。"""
        return product_data(service.get_product(product_id))

    @mcp.tool(annotations={"readOnlyHint": True})
    def get_my_profile() -> dict[str, object]:
        """读取当前登录用户的账户信息和可用积分，仅能查询本人。"""
        user = current_user()
        return {"id": user.id, "username": user.username, "points_balance": user.points_balance}

    @mcp.tool(annotations={"readOnlyHint": True})
    def get_cart() -> dict[str, object]:
        """查看当前用户的购物车和预估总积分，仅能查询本人。"""
        user = current_user()
        items = [cart_item_data(item) for item in service.cart(user.id)]
        return {"items": items, "points_total": sum(item["subtotal"] for item in items)}

    @mcp.tool
    def add_to_cart(product_id: str, quantity: int) -> dict[str, object]:
        """在用户明确要求加购时调用。数量必须为正，会修改当前用户购物车。"""
        if quantity <= 0:
            raise ShopError(422, "validation_error", "数量必须为正数")
        return cart_item_data(service.add_cart_item(current_user().id, product_id, quantity))

    @mcp.tool
    def update_cart_item(item_id: str, quantity: int) -> dict[str, object]:
        """更新当前用户购物车条目；数量为零时移除，正数时更新数量。"""
        user = current_user()
        if quantity < 0:
            raise ShopError(422, "validation_error", "数量不能为负数")
        if quantity == 0:
            service.delete_cart_item(user.id, item_id)
            return {"item_id": item_id, "status": "removed"}
        return cart_item_data(service.update_cart_item(user.id, item_id, quantity))

    @mcp.tool(annotations={"readOnlyHint": True})
    def preview_checkout(shipping_address: str) -> dict[str, object]:
        """下单前调用，返回商品、数量、总积分和收货信息；不会创建订单，但会签发短期确认令牌。"""
        if not shipping_address.strip():
            raise ShopError(422, "validation_error", "收货地址不能为空")
        return service.preview_checkout(current_user().id, shipping_address)

    @mcp.tool
    def place_order(shipping_address: str, confirmation_token: str) -> dict[str, object]:
        """只在已展示预览且用户明确确认后调用。使用预览返回的一次性确认令牌创建订单。"""
        if not shipping_address.strip():
            raise ShopError(422, "validation_error", "收货地址不能为空")
        order = service.create_confirmed_order(current_user().id, shipping_address, confirmation_token)
        return {"order_id": order["id"], "status": order["status"], "points_deducted": order["points_total"], "summary": f"订单已创建，共扣除 {order['points_total']} 积分"}

    @mcp.tool(annotations={"readOnlyHint": True})
    def get_my_orders(page: int = 1) -> dict[str, object]:
        """查看当前用户的订单摘要；可传页码，默认第一页，仅能查询本人。"""
        if page < 1:
            raise ShopError(422, "validation_error", "页码必须大于零")
        user = current_user()
        values = [order for order in service.orders.values() if order["user_id"] == user.id]
        values.sort(key=lambda order: order["created_at"], reverse=True)
        page_size = 20
        start = (page - 1) * page_size
        return {
            "items": [
                {"order_id": order["id"], "status": order["status"], "points_total": order["points_total"], "created_at": order["created_at"]}
                for order in values[start : start + page_size]
            ],
            "page": page,
            "total": len(values),
        }

    return mcp.http_app(path="/mcp", transport="streamable-http", stateless_http=True)
