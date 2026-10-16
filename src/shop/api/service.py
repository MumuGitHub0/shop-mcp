"""商城业务规则层：所有业务逻辑收敛于此，HTTP 与 MCP 层只做协议适配。

存储采用 SQLAlchemy（SQLite/MySQL），数据库是唯一事实来源；Redis 仅为可选加速层。
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from threading import RLock
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from shop.storage import repository as repo
from shop.storage.cache import CacheClient, build_cache_from_env
from shop.storage.db import create_engine_factory
from shop.storage.models import (
    Base,
    CartItemModel,
    CategoryModel,
    OrderItemModel,
    OrderModel,
    ProductModel,
    UserModel,
    utc_now,
)

from .errors import ShopError
from .security import (
    create_confirmation_token,
    decode_confirmation_token,
    hash_password,
    verify_password,
)

PRODUCT_CACHE_TTL = 300
CATEGORY_CACHE_TTL = 300
PRODUCT_LIST_CACHE_TTL = 60
IDEMPOTENCY_TTL = 86400
CONFIRMATION_TTL = 900
ORDER_LOCK_TTL = 10


@dataclass
class User:
    id: str
    username: str
    password_hash: str
    points_balance: int
    role: str = "customer"


@dataclass
class Category:
    id: str
    name: str
    is_active: bool = True


@dataclass
class Product:
    id: str
    name: str
    description: str
    points_price: int
    stock: int
    category_id: str
    is_active: bool = True
    image_url: str | None = None


@dataclass
class CartItem:
    id: str
    user_id: str
    product_id: str
    quantity: int


def _to_user(model: UserModel) -> User:
    return User(model.id, model.username, model.password_hash, model.points_balance, model.role)


def _to_category(model: CategoryModel) -> Category:
    return Category(model.id, model.name, model.is_active)


def _to_product(model: ProductModel) -> Product:
    return Product(
        model.id,
        model.name,
        model.description,
        model.points_price,
        model.stock,
        model.category_id,
        model.is_active,
        model.image_url,
    )


def _to_cart_item(model: CartItemModel) -> CartItem:
    return CartItem(model.id, model.user_id, model.product_id, model.quantity)


def _iso(value: datetime) -> str:
    """统一 UTC ISO 序列化；SQLite 回读可能丢失 tzinfo，按 UTC 补齐。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _order_to_dict(order: OrderModel) -> dict:
    return {
        "id": order.id,
        "user_id": order.user_id,
        "status": order.status,
        "points_total": order.points_total,
        "shipping_address": order.shipping_address,
        "items": [
            {
                "product_id": item.product_id,
                "name": item.product_name,
                "points_price": item.points_price,
                "quantity": item.quantity,
                "subtotal": item.subtotal,
            }
            for item in order.items
        ],
        "created_at": _iso(order.created_at),
    }


def _ledger_to_dict(model) -> dict:
    return {
        "id": model.id,
        "user_id": model.user_id,
        "reason": model.reason,
        "change": model.change,
        "order_id": model.order_id,
        "balance_after": model.balance_after,
        "note": model.note,
        "created_at": _iso(model.created_at),
    }


@dataclass
class ShopService:
    database_url: str = field(
        default_factory=lambda: os.getenv("SHOP_DATABASE_URL", "sqlite:///./shop.db")
    )
    cache: CacheClient | None = None
    lock: RLock = field(default_factory=RLock)
    engine: object = field(default=None, init=False, repr=False)
    session_factory: sessionmaker | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.engine, self.session_factory = create_engine_factory(self.database_url)
        # 独立构造（测试/脚本）时兜底建表；生产路径由 Alembic ensure_schema 负责
        Base.metadata.create_all(self.engine)
        if self.cache is None:
            self.cache = build_cache_from_env()
        self._seed_if_empty()

    def _seed_if_empty(self) -> None:
        """表为空时写入种子数据（含管理员账号），保证新库可用。"""
        with self._txn() as session:
            if repo.get_user_by_name(session, "demo") is not None:
                return
            repo.insert_user(
                session,
                user_id="user-demo",
                username="demo",
                password_hash=hash_password("demo-password"),
                points_balance=1000,
                role="customer",
            )
            repo.insert_user(
                session,
                user_id="user-admin",
                username="admin",
                password_hash=hash_password("admin-password"),
                points_balance=0,
                role="admin",
            )
            session.add(CategoryModel(id="cat-gifts", name="礼品", is_active=True))
            session.flush()
            session.add(
                ProductModel(
                    id="product-mug",
                    name="商城马克杯",
                    description="实用的陶瓷马克杯",
                    points_price=100,
                    stock=20,
                    category_id="cat-gifts",
                    is_active=True,
                    image_url=None,
                )
            )

    @contextmanager
    def _txn(self) -> Iterator[Session]:
        """事务作用域：正常退出提交，异常自动回滚。"""
        with self.session_factory.begin() as session:
            yield session

    # ---------- 缓存辅助 ----------

    def _invalidate_product_cache(self, product_ids: list[str] | None = None) -> None:
        if product_ids:
            self.cache.delete(*[f"shop:cache:product:{pid}" for pid in product_ids])
        self.cache.delete_prefix("shop:cache:product:list:")

    def _invalidate_category_cache(self) -> None:
        self.cache.delete("shop:cache:categories")

    # ---------- 认证与用户 ----------

    def user_by_name(self, username: str) -> User:
        with self.session_factory() as session:
            model = repo.get_user_by_name(session, username)
            if model is None:
                raise ShopError(401, "invalid_credentials", "用户名或密码错误")
            return _to_user(model)

    def authenticate(self, username: str, password: str) -> User:
        user = self.user_by_name(username)
        if not verify_password(password, user.password_hash):
            raise ShopError(401, "invalid_credentials", "用户名或密码错误")
        return user

    def get_user(self, user_id: str) -> User:
        with self.session_factory() as session:
            model = repo.get_user(session, user_id)
            if model is None:
                raise ShopError(401, "invalid_token", "无效的访问令牌")
            return _to_user(model)

    def create_user(
        self, username: str, password: str, points_balance: int = 0, role: str = "customer"
    ) -> User:
        with self._txn() as session:
            if repo.get_user_by_name(session, username) is not None:
                raise ShopError(409, "username_taken", "用户名已存在")
            model = repo.insert_user(
                session,
                user_id=f"user-{uuid4()}",
                username=username,
                password_hash=hash_password(password),
                points_balance=points_balance,
                role=role,
            )
            return _to_user(model)

    def list_users(
        self, q: str | None = None, page: int = 1, page_size: int = 20
    ) -> tuple[list[User], int]:
        with self.session_factory() as session:
            models, total = repo.list_users(session, q, page, page_size)
            return [_to_user(model) for model in models], total

    def adjust_points(self, user_id: str, change: int, note: str | None = None) -> User:
        """管理端调整积分：事务内更新余额并写流水，保证可追溯。"""
        with self._txn() as session:
            model = repo.get_user_for_update(session, user_id)
            if model is None:
                raise ShopError(404, "user_not_found", "用户不存在")
            balance = model.points_balance + change
            if balance < 0:
                raise ShopError(422, "validation_error", "调整后积分余额不能为负")
            model.points_balance = balance
            repo.insert_ledger(
                session,
                user_id=user_id,
                reason="admin_adjust",
                change=change,
                order_id=None,
                balance_after=balance,
                note=note,
            )
            return _to_user(model)

    # ---------- 目录（分类/商品） ----------

    def list_categories(self, active_only: bool = True) -> list[Category]:
        key = "shop:cache:categories"
        cached = self.cache.get_json(key) if active_only else None
        if isinstance(cached, list):
            return [Category(**value) for value in cached]
        with self.session_factory() as session:
            models = repo.list_categories(session, active_only)
            categories = [_to_category(model) for model in models]
        if active_only:
            self.cache.set_json(key, [asdict(value) for value in categories], CATEGORY_CACHE_TTL)
        return categories

    def get_category(self, category_id: str) -> Category:
        with self.session_factory() as session:
            model = repo.get_category(session, category_id)
            if model is None:
                raise ShopError(404, "category_not_found", "分类不存在")
            return _to_category(model)

    def create_category(self, name: str) -> Category:
        with self._txn() as session:
            model = repo.insert_category(session, category_id=f"cat-{uuid4()}", name=name)
            self._invalidate_category_cache()
            return _to_category(model)

    def update_category(
        self, category_id: str, name: str | None = None, is_active: bool | None = None
    ) -> Category:
        with self._txn() as session:
            model = repo.get_category(session, category_id)
            if model is None:
                raise ShopError(404, "category_not_found", "分类不存在")
            if name is not None:
                model.name = name
            if is_active is not None:
                model.is_active = is_active
            self._invalidate_category_cache()
            return _to_category(model)

    def list_products(
        self, q: str | None, category_id: str | None, active_only: bool = True
    ) -> list[Product]:
        cache_key = None
        if active_only:
            digest = hashlib.sha1(
                json.dumps([q, category_id], ensure_ascii=False).encode()
            ).hexdigest()
            cache_key = f"shop:cache:product:list:{digest}"
            cached = self.cache.get_json(cache_key)
            if isinstance(cached, list):
                return [Product(**value) for value in cached]
        with self.session_factory() as session:
            models = repo.list_products(session, q, category_id, active_only)
            products = [_to_product(model) for model in models]
        if cache_key is not None:
            self.cache.set_json(
                cache_key, [asdict(value) for value in products], PRODUCT_LIST_CACHE_TTL
            )
        return products

    def get_product(self, product_id: str) -> Product:
        key = f"shop:cache:product:{product_id}"
        cached = self.cache.get_json(key)
        if isinstance(cached, dict):
            return Product(**cached)
        with self.session_factory() as session:
            model = repo.get_product(session, product_id)
            if model is None:
                raise ShopError(404, "product_not_found", "商品不存在")
            product = _to_product(model)
        self.cache.set_json(key, asdict(product), PRODUCT_CACHE_TTL)
        return product

    def create_product(
        self,
        name: str,
        description: str,
        points_price: int,
        stock: int,
        category_id: str,
        image_url: str | None = None,
    ) -> Product:
        self.get_category(category_id)
        with self._txn() as session:
            model = repo.insert_product(
                session,
                product_id=f"product-{uuid4()}",
                name=name,
                description=description,
                points_price=points_price,
                stock=stock,
                category_id=category_id,
                image_url=image_url,
            )
            self._invalidate_product_cache()
            return _to_product(model)

    def update_product(self, product_id: str, changes: dict) -> Product:
        allowed = {"name", "description", "points_price", "category_id", "image_url"}
        filtered = {
            key: value for key, value in changes.items() if key in allowed and value is not None
        }
        if "category_id" in filtered:
            self.get_category(filtered["category_id"])
        with self._txn() as session:
            if repo.get_product(session, product_id) is None:
                raise ShopError(404, "product_not_found", "商品不存在")
            repo.update_product(session, product_id, filtered)
        self._invalidate_product_cache([product_id])
        return self.get_product(product_id)

    def set_product_status(self, product_id: str, is_active: bool) -> Product:
        with self._txn() as session:
            if repo.get_product(session, product_id) is None:
                raise ShopError(404, "product_not_found", "商品不存在")
            repo.update_product(session, product_id, {"is_active": is_active})
        self._invalidate_product_cache([product_id])
        return self.get_product(product_id)

    def adjust_stock(self, product_id: str, delta: int) -> Product:
        with self._txn() as session:
            model = repo.get_product(session, product_id)
            if model is None:
                raise ShopError(404, "product_not_found", "商品不存在")
            if model.stock + delta < 0:
                raise ShopError(422, "validation_error", "库存调整后不能为负")
            model.stock = model.stock + delta
        self._invalidate_product_cache([product_id])
        return self.get_product(product_id)

    # ---------- 购物车 ----------

    def cart(self, user_id: str) -> list[CartItem]:
        with self.session_factory() as session:
            models = repo.get_cart_items(session, user_id)
            return [_to_cart_item(model) for model in models]

    def add_cart_item(self, user_id: str, product_id: str, quantity: int) -> CartItem:
        product = self.get_product(product_id)
        if not product.is_active:
            raise ShopError(409, "product_unavailable", "商品不可售")
        with self._txn() as session:
            model = repo.upsert_cart_item(
                session,
                user_id=user_id,
                product_id=product_id,
                quantity=quantity,
                dialect_name=self.engine.dialect.name,
            )
            return _to_cart_item(model)

    def get_cart_item(self, user_id: str, item_id: str) -> CartItem:
        with self.session_factory() as session:
            model = repo.get_cart_item(session, item_id)
            if model is None:
                raise ShopError(404, "cart_item_not_found", "购物车商品不存在")
            if model.user_id != user_id:
                raise ShopError(403, "forbidden", "无权访问该资源")
            return _to_cart_item(model)

    def update_cart_item(self, user_id: str, item_id: str, quantity: int) -> CartItem:
        item = self.get_cart_item(user_id, item_id)
        with self._txn() as session:
            model = repo.get_cart_item(session, item.id)
            model.quantity = quantity
            return _to_cart_item(model)

    def delete_cart_item(self, user_id: str, item_id: str) -> None:
        item = self.get_cart_item(user_id, item_id)
        with self._txn() as session:
            model = repo.get_cart_item(session, item.id)
            session.delete(model)

    # ---------- 订单 ----------

    def _order_lines(
        self, session: Session, items: list[CartItemModel]
    ) -> tuple[list[dict], int, list[ProductModel]]:
        lines: list[dict] = []
        total = 0
        products: list[ProductModel] = []
        for item in items:
            product = repo.get_product(session, item.product_id)
            if product is None:
                raise ShopError(404, "product_not_found", "商品不存在")
            if not product.is_active:
                raise ShopError(409, "product_unavailable", "商品不可售")
            line = {
                "product_id": product.id,
                "name": product.name,
                "points_price": product.points_price,
                "quantity": item.quantity,
                "subtotal": product.points_price * item.quantity,
            }
            lines.append(line)
            total += line["subtotal"]
            products.append(product)
        return lines, total, products

    def preview(self, user_id: str, shipping_address: str) -> dict:
        with self.session_factory() as session:
            items = repo.get_cart_items(session, user_id)
            if not items:
                raise ShopError(409, "empty_cart", "购物车为空")
            lines, total, _ = self._order_lines(session, items)
            user = repo.get_user(session, user_id)
            if user is None:
                raise ShopError(401, "invalid_token", "无效的访问令牌")
            return {
                "items": lines,
                "points_total": total,
                "points_balance": user.points_balance,
                "sufficient_points": user.points_balance >= total,
                "shipping_address": shipping_address,
            }

    def preview_checkout(self, user_id: str, shipping_address: str) -> dict:
        """生成绑定当前购物车的下单预览和一次性确认令牌。"""
        preview = self.preview(user_id, shipping_address)
        token = create_confirmation_token(
            user_id=user_id,
            cart_fingerprint=self._cart_fingerprint(user_id),
            shipping_address=shipping_address,
            points_total=preview["points_total"],
        )
        return {**preview, "confirmation_token": token}

    def create_confirmed_order(
        self, user_id: str, shipping_address: str, confirmation_token: str
    ) -> dict:
        """验证预览确认令牌后创建订单；令牌只能使用一次。"""
        payload = decode_confirmation_token(confirmation_token)
        token_id = payload["jti"]
        # Redis 命中说明已被抢占，仍以数据库为准做最终校验
        self.cache.set_if_absent(f"shop:conf:{token_id}", "1", CONFIRMATION_TTL)
        with self._order_lock(user_id):
            with self.lock:
                with self._txn() as session:
                    if repo.get_used_confirmation_token(session, token_id) is not None:
                        raise ShopError(
                            409, "confirmation_token_used", "确认令牌已使用，请重新预览"
                        )
                    if payload["sub"] != user_id:
                        raise ShopError(403, "forbidden", "确认令牌不属于当前用户")
                    if payload["shipping_address"] != shipping_address:
                        raise ShopError(409, "preview_changed", "收货信息已变化，请重新预览")
                    if payload["cart_fingerprint"] != self._cart_fingerprint(user_id):
                        raise ShopError(409, "preview_changed", "购物车已变化，请重新预览")
                    preview = self.preview(user_id, shipping_address)
                    if payload["points_total"] != preview["points_total"]:
                        raise ShopError(409, "preview_changed", "订单金额已变化，请重新预览")
                    repo.insert_used_confirmation_token(session, jti=token_id, user_id=user_id)
                return self._create_order_txn(user_id, shipping_address, f"confirmation:{token_id}")

    def create_order(self, user_id: str, shipping_address: str, idempotency_key: str) -> dict:
        with self._order_lock(user_id):
            with self.lock:
                return self._create_order_txn(user_id, shipping_address, idempotency_key)

    @contextmanager
    def _order_lock(self, user_id: str) -> Iterator[None]:
        """用户级下单互斥：有 Redis 用分布式锁，否则退化为进程内 RLock（外层已持有）。"""
        key = f"shop:lock:order:{user_id}"
        if self.cache.set_if_absent(key, "1", ORDER_LOCK_TTL):
            try:
                yield
            finally:
                self.cache.delete(key)
        else:
            raise ShopError(409, "order_in_progress", "该用户已有下单请求处理中，请稍后重试")

    def _create_order_txn(self, user_id: str, shipping_address: str, idempotency_key: str) -> dict:
        with self._txn() as session:
            existing = repo.find_idempotent_order(session, user_id, idempotency_key)
            if existing is not None:
                return _order_to_dict(existing)
            # Redis 前置拦截仅加速；未命中仍走数据库，唯一约束为准
            if not self.cache.set_if_absent(
                f"shop:idem:{user_id}:{idempotency_key}", "1", IDEMPOTENCY_TTL
            ):
                repeat = repo.find_idempotent_order(session, user_id, idempotency_key)
                if repeat is not None:
                    return _order_to_dict(repeat)

            items = repo.get_cart_items(session, user_id)
            if not items:
                raise ShopError(409, "empty_cart", "购物车为空")
            lines, total, products = self._order_lines(session, items)
            user = repo.get_user_for_update(session, user_id)
            if user is None:
                raise ShopError(401, "invalid_token", "无效的访问令牌")
            if user.points_balance < total:
                raise ShopError(409, "insufficient_points", "积分不足")
            for line in lines:
                product = next(p for p in products if p.id == line["product_id"])
                if product.stock < line["quantity"]:
                    raise ShopError(409, "insufficient_stock", "库存不足")
            for line in lines:
                product = next(p for p in products if p.id == line["product_id"])
                product.stock -= line["quantity"]
            user.points_balance -= total
            order = OrderModel(
                id=str(uuid4()),
                user_id=user_id,
                status="created",
                points_total=total,
                shipping_address=shipping_address,
                created_at=utc_now(),
            )
            session.add(order)
            session.flush()
            for line in lines:
                session.add(
                    OrderItemModel(
                        id=str(uuid4()),
                        order_id=order.id,
                        product_id=line["product_id"],
                        product_name=line["name"],
                        points_price=line["points_price"],
                        quantity=line["quantity"],
                        subtotal=line["subtotal"],
                    )
                )
            repo.insert_idempotency(
                session, user_id=user_id, key=idempotency_key, order_id=order.id
            )
            repo.insert_ledger(
                session,
                user_id=user_id,
                reason="order",
                change=-total,
                order_id=order.id,
                balance_after=user.points_balance,
            )
            for item in items:
                session.delete(item)
            result = _order_to_dict(order)
        self._invalidate_product_cache([line["product_id"] for line in result["items"]])
        return result

    def _cart_fingerprint(self, user_id: str) -> str:
        items = sorted((item.product_id, item.quantity) for item in self.cart(user_id))
        encoded = json.dumps(items, separators=(",", ":"), ensure_ascii=False).encode()
        return hashlib.sha256(encoded).hexdigest()

    def list_orders(
        self, user_id: str, page: int = 1, page_size: int = 20
    ) -> tuple[list[dict], int]:
        with self.session_factory() as session:
            models, total = repo.list_orders_by_user(session, user_id, page, page_size)
            return [_order_to_dict(model) for model in models], total

    def get_order(self, user_id: str, order_id: str) -> dict:
        with self.session_factory() as session:
            order = repo.get_order(session, order_id)
            if order is None:
                raise ShopError(404, "order_not_found", "订单不存在")
            if order.user_id != user_id:
                raise ShopError(403, "forbidden", "无权访问该资源")
            return _order_to_dict(order)

    def list_orders_admin(
        self, page: int = 1, page_size: int = 20, user_id: str | None = None
    ) -> tuple[list[dict], int]:
        with self.session_factory() as session:
            models, total = repo.list_orders(session, page, page_size, user_id)
            return [_order_to_dict(model) for model in models], total

    def get_order_admin(self, order_id: str) -> dict:
        with self.session_factory() as session:
            order = repo.get_order(session, order_id)
            if order is None:
                raise ShopError(404, "order_not_found", "订单不存在")
            return _order_to_dict(order)

    def list_ledger(
        self, user_id: str | None = None, page: int = 1, page_size: int = 20
    ) -> tuple[list[dict], int]:
        with self.session_factory() as session:
            models, total = repo.list_ledger(session, user_id, page, page_size)
            return [_ledger_to_dict(model) for model in models], total

    # ---------- OAuth 持久化 ----------

    def get_oauth_client(self, client_id: str) -> dict | None:
        with self.session_factory() as session:
            model = repo.get_oauth_client(session, client_id)
            return model.payload if model is not None else None

    def save_oauth_client(self, client_id: str, payload: dict) -> None:
        with self._txn() as session:
            repo.upsert_oauth_client(session, client_id=client_id, payload=payload)

    def get_refresh_token(self, token: str) -> dict | None:
        with self.session_factory() as session:
            model = repo.get_refresh_token(session, token)
            if model is None:
                return None
            return {
                "user_id": model.user_id,
                "client_id": model.client_id,
                "created_at": model.created_at.timestamp(),
            }

    def save_refresh_token(self, token: str, user_id: str, client_id: str) -> None:
        with self._txn() as session:
            repo.insert_refresh_token(session, token=token, user_id=user_id, client_id=client_id)

    def delete_refresh_token(self, token: str) -> dict | None:
        with self._txn() as session:
            model = repo.delete_refresh_token(session, token)
            if model is None:
                return None
            return {
                "user_id": model.user_id,
                "client_id": model.client_id,
                "created_at": model.created_at.timestamp(),
            }
