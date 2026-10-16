"""管理后台路由：/admin/api/v1/*，全部接口要求管理员角色（登录接口除外）。

注意：不能加 `from __future__ import annotations`——端点注解里的 `require_admin`
是 create_admin_router 的闭包变量，延迟求值的字符串注解无法从模块 globals 解析，
会导致 Depends 元数据丢失。
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..api.service import ShopService
from .auth import admin_login, create_require_admin


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1)
    points_price: int = Field(gt=0)
    stock: int = Field(ge=0)
    category_id: str = Field(min_length=1)
    image_url: str | None = None


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    points_price: int | None = Field(default=None, gt=0)
    category_id: str | None = None
    image_url: str | None = None


class ProductStatusRequest(BaseModel):
    is_active: bool


class StockAdjustRequest(BaseModel):
    delta: int


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    is_active: bool | None = None


class PointsAdjustRequest(BaseModel):
    change: int
    note: str | None = Field(default=None, max_length=255)


def _product_data(product) -> dict[str, Any]:
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


def _user_data(user) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "points_balance": user.points_balance,
        "role": user.role,
    }


def create_admin_router(service: ShopService) -> APIRouter:
    router = APIRouter()
    require_admin = create_require_admin(service)

    @router.post("/auth/login")
    def login(payload: AdminLoginRequest) -> dict:
        return admin_login(service, payload.username, payload.password)

    # ---------- 商品 ----------

    @router.get("/products")
    def products(
        _: Annotated[object, Depends(require_admin)],
        q: str | None = None,
        category_id: str | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> dict:
        results = service.list_products(q, category_id, active_only=False)
        start = (page - 1) * page_size
        return {
            "items": [_product_data(p) for p in results[start : start + page_size]],
            "page": page,
            "page_size": page_size,
            "total": len(results),
        }

    @router.post("/products", status_code=201)
    def create_product(
        payload: ProductCreate, _: Annotated[object, Depends(require_admin)]
    ) -> dict:
        product = service.create_product(
            name=payload.name,
            description=payload.description,
            points_price=payload.points_price,
            stock=payload.stock,
            category_id=payload.category_id,
            image_url=payload.image_url,
        )
        return _product_data(product)

    @router.patch("/products/{product_id}")
    def update_product(
        product_id: str, payload: ProductUpdate, _: Annotated[object, Depends(require_admin)]
    ) -> dict:
        changes = payload.model_dump(exclude_unset=True)
        product = service.update_product(product_id, changes)
        return _product_data(product)

    @router.patch("/products/{product_id}/status")
    def set_product_status(
        product_id: str, payload: ProductStatusRequest, _: Annotated[object, Depends(require_admin)]
    ) -> dict:
        return _product_data(service.set_product_status(product_id, payload.is_active))

    @router.patch("/products/{product_id}/stock")
    def adjust_stock(
        product_id: str, payload: StockAdjustRequest, _: Annotated[object, Depends(require_admin)]
    ) -> dict:
        return _product_data(service.adjust_stock(product_id, payload.delta))

    # ---------- 分类 ----------

    @router.get("/categories")
    def categories(_: Annotated[object, Depends(require_admin)]) -> dict:
        return {
            "items": [
                {"id": c.id, "name": c.name, "is_active": c.is_active}
                for c in service.list_categories(active_only=False)
            ]
        }

    @router.post("/categories", status_code=201)
    def create_category(
        payload: CategoryCreate, _: Annotated[object, Depends(require_admin)]
    ) -> dict:
        category = service.create_category(payload.name)
        return {"id": category.id, "name": category.name, "is_active": category.is_active}

    @router.patch("/categories/{category_id}")
    def update_category(
        category_id: str, payload: CategoryUpdate, _: Annotated[object, Depends(require_admin)]
    ) -> dict:
        category = service.update_category(category_id, payload.name, payload.is_active)
        return {"id": category.id, "name": category.name, "is_active": category.is_active}

    # ---------- 用户 ----------

    @router.get("/users")
    def users(
        _: Annotated[object, Depends(require_admin)],
        q: str | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> dict:
        items, total = service.list_users(q, page, page_size)
        return {
            "items": [_user_data(u) for u in items],
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    @router.get("/users/{user_id}")
    def user_detail(user_id: str, _: Annotated[object, Depends(require_admin)]) -> dict:
        user = service.get_user(user_id)
        ledger, _ = service.list_ledger(user_id, page=1, page_size=10)
        return {**_user_data(user), "recent_ledger": ledger}

    @router.post("/users/{user_id}/points/adjust")
    def adjust_points(
        user_id: str, payload: PointsAdjustRequest, _: Annotated[object, Depends(require_admin)]
    ) -> dict:
        user = service.adjust_points(user_id, payload.change, payload.note)
        return _user_data(user)

    # ---------- 订单与流水 ----------

    @router.get("/orders")
    def orders(
        _: Annotated[object, Depends(require_admin)],
        user_id: str | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> dict:
        items, total = service.list_orders_admin(page, page_size, user_id)
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    @router.get("/orders/{order_id}")
    def order_detail(order_id: str, _: Annotated[object, Depends(require_admin)]) -> dict:
        return service.get_order_admin(order_id)

    @router.get("/ledger")
    def ledger(
        _: Annotated[object, Depends(require_admin)],
        user_id: str | None = None,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> dict:
        items, total = service.list_ledger(user_id, page, page_size)
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
        }

    return router
