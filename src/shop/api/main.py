from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

# E402：必须先加载 .env 再导入业务模块（模块导入时会读取环境变量）
from shop.admin import create_admin_router  # noqa: E402
from shop.mcp.server import create_mcp_app  # noqa: E402
from shop.storage.db import ensure_schema, resolve_database_url  # noqa: E402

from .errors import ShopError, shop_error_handler, validation_error_handler  # noqa: E402
from .security import bearer_token, create_token, decode_token  # noqa: E402
from .service import CartItem, Product, ShopService, User  # noqa: E402


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class CartItemRequest(BaseModel):
    product_id: str
    quantity: int = Field(gt=0)


class QuantityRequest(BaseModel):
    quantity: int = Field(gt=0)


class OrderRequest(BaseModel):
    shipping_address: str = Field(min_length=1, max_length=500)


def product_data(product: Product) -> dict:
    return {
        "id": product.id,
        "name": product.name,
        "description": product.description,
        "points_price": product.points_price,
        "stock": product.stock,
        "category_id": product.category_id,
        "is_active": product.is_active,
        "image_url": product.image_url,
    }


def create_app(service: ShopService | None = None, base_url: str | None = None) -> FastAPI:
    """构建商城应用；支持注入独立服务实例与公开地址，便于测试隔离。"""
    if service is None:
        ensure_schema(resolve_database_url())
        service = ShopService()
    mcp_app = create_mcp_app(service, base_url)
    # FastMCP 会话管理器依赖 lifespan 初始化，必须交给父应用驱动。
    mcp_lifespan = mcp_app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp_lifespan(app):
            yield
        service.engine.dispose()

    app = FastAPI(title="积分商城 API", lifespan=lifespan)
    app.add_exception_handler(ShopError, shop_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    def cart_item_data(item: CartItem) -> dict:
        return {
            "id": item.id,
            "product_id": item.product_id,
            "quantity": item.quantity,
            "product": product_data(service.get_product(item.product_id)),
        }

    def current_user(token: Annotated[str, Depends(bearer_token)]) -> User:
        return service.get_user(decode_token(token))

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/api/v1/auth/login")
    def login(payload: LoginRequest) -> dict:
        user = service.authenticate(payload.username, payload.password)
        return {"access_token": create_token(user.id, role=user.role), "token_type": "bearer"}

    @app.get("/api/v1/me")
    def me(user: Annotated[User, Depends(current_user)]) -> dict:
        return {
            "id": user.id,
            "username": user.username,
            "points_balance": user.points_balance,
            "role": user.role,
        }

    @app.get("/api/v1/categories")
    def categories() -> dict:
        return {
            "items": [
                {"id": c.id, "name": c.name, "is_active": c.is_active}
                for c in service.list_categories()
            ]
        }

    @app.get("/api/v1/products")
    def products(
        q: str | None = None,
        category_id: str | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> dict:
        results = service.list_products(q, category_id)
        start = (page - 1) * page_size
        return {
            "items": [product_data(p) for p in results[start : start + page_size]],
            "page": page,
            "page_size": page_size,
            "total": len(results),
        }

    @app.get("/api/v1/products/{product_id}")
    def product(product_id: str) -> dict:
        product = service.get_product(product_id)
        if not product.is_active:
            raise ShopError(404, "product_not_found", "商品不存在")
        return product_data(product)

    @app.get("/api/v1/cart")
    def cart(user: Annotated[User, Depends(current_user)]) -> dict:
        items = [cart_item_data(item) for item in service.cart(user.id)]
        return {
            "items": items,
            "points_total": sum(i["product"]["points_price"] * i["quantity"] for i in items),
        }

    @app.post("/api/v1/cart/items", status_code=201)
    def add_cart_item(
        payload: CartItemRequest, user: Annotated[User, Depends(current_user)]
    ) -> dict:
        return cart_item_data(service.add_cart_item(user.id, payload.product_id, payload.quantity))

    @app.patch("/api/v1/cart/items/{item_id}")
    def update_cart_item(
        item_id: str, payload: QuantityRequest, user: Annotated[User, Depends(current_user)]
    ) -> dict:
        return cart_item_data(service.update_cart_item(user.id, item_id, payload.quantity))

    @app.delete("/api/v1/cart/items/{item_id}", status_code=204)
    def delete_cart_item(item_id: str, user: Annotated[User, Depends(current_user)]) -> Response:
        service.delete_cart_item(user.id, item_id)
        return Response(status_code=204)

    @app.post("/api/v1/orders/preview")
    def preview_order(payload: OrderRequest, user: Annotated[User, Depends(current_user)]) -> dict:
        return service.preview(user.id, payload.shipping_address)

    @app.post("/api/v1/orders", status_code=201)
    def create_order(
        payload: OrderRequest,
        user: Annotated[User, Depends(current_user)],
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict:
        if not idempotency_key:
            raise ShopError(422, "missing_idempotency_key", "必须提供 Idempotency-Key")
        return service.create_order(user.id, payload.shipping_address, idempotency_key)

    @app.get("/api/v1/orders")
    def orders(
        user: Annotated[User, Depends(current_user)],
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> dict:
        values, total = service.list_orders(user.id, page, page_size)
        return {"items": values, "page": page, "page_size": page_size, "total": total}

    @app.get("/api/v1/orders/{order_id}")
    def order(order_id: str, user: Annotated[User, Depends(current_user)]) -> dict:
        return service.get_order(user.id, order_id)

    # 管理后台 API：必须注册在静态资源与 MCP 通配挂载之前。
    app.include_router(create_admin_router(service), prefix="/admin/api/v1")

    # 管理后台前端构建产物（admin-web/dist）；未构建时跳过，不影响 API 与 MCP。
    admin_dist = Path(__file__).resolve().parents[3] / "admin-web" / "dist"
    if admin_dist.is_dir():
        # Mount("/admin") 只匹配 /admin/ 及子路径，裸 /admin 会落到下方 MCP 兜底挂载返回 404，
        # 因此显式重定向补齐斜杠。
        from fastapi.responses import RedirectResponse

        @app.get("/admin", include_in_schema=False)
        def admin_index_redirect() -> Response:
            return RedirectResponse(url="/admin/", status_code=307)

        app.mount("/admin", StaticFiles(directory=admin_dist, html=True), name="admin-web")

    # MCP 挂载必须放在业务路由之后，避免通配挂载抢占 /api 路径。
    app.mount("/", mcp_app)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
