from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from threading import RLock
from uuid import uuid4

from .errors import ShopError
from .security import create_confirmation_token, decode_confirmation_token, hash_password, verify_password


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


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


@dataclass
class ShopService:
    database_url: str = field(
        default_factory=lambda: os.getenv("SHOP_DATABASE_URL", "sqlite:///./shop.db")
    )
    users: dict[str, User] = field(default_factory=dict)
    categories: dict[str, Category] = field(default_factory=dict)
    products: dict[str, Product] = field(default_factory=dict)
    cart_items: dict[str, CartItem] = field(default_factory=dict)
    orders: dict[str, dict] = field(default_factory=dict)
    idempotency: dict[tuple[str, str], str] = field(default_factory=dict)
    used_confirmation_tokens: set[str] = field(default_factory=set)
    ledger: list[dict] = field(default_factory=list)
    oauth_clients: dict[str, dict] = field(default_factory=dict)
    oauth_refresh_tokens: dict[str, dict] = field(default_factory=dict)
    lock: RLock = field(default_factory=RLock)
    connection: sqlite3.Connection | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        database_path = self._database_path()
        if database_path is not None:
            self.connection = sqlite3.connect(database_path, check_same_thread=False)
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS shop_state (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)"
            )
            if self._load_state():
                return
        if not self.users:
            self._seed()
        self._save()

    def _database_path(self) -> str | None:
        if self.database_url == "memory://":
            return None
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("SHOP_DATABASE_URL 必须为 memory:// 或 sqlite:/// 路径")
        database_path = self.database_url.removeprefix(prefix)
        if database_path != ":memory:":
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        return database_path

    def _seed(self) -> None:
        user = User("user-demo", "demo", hash_password("demo-password"), 1000)
        category = Category("cat-gifts", "礼品")
        product = Product("product-mug", "商城马克杯", "实用的陶瓷马克杯", 100, 20, category.id)
        self.users[user.id] = user
        self.categories[category.id] = category
        self.products[product.id] = product

    def _load_state(self) -> bool:
        if self.connection is None:
            return False
        row = self.connection.execute(
            "SELECT payload FROM shop_state WHERE id = 1"
        ).fetchone()
        if row is None:
            return False
        payload = json.loads(row[0])
        self.users = {value["id"]: User(**value) for value in payload["users"]}
        self.categories = {
            value["id"]: Category(**value) for value in payload["categories"]
        }
        self.products = {value["id"]: Product(**value) for value in payload["products"]}
        self.cart_items = {
            value["id"]: CartItem(**value) for value in payload["cart_items"]
        }
        self.orders = payload["orders"]
        self.idempotency = {
            (user_id, key): order_id
            for user_id, key, order_id in payload["idempotency"]
        }
        self.used_confirmation_tokens = set(payload["used_confirmation_tokens"])
        self.ledger = payload["ledger"]
        self.oauth_clients = payload.get("oauth_clients", {})
        self.oauth_refresh_tokens = payload.get("oauth_refresh_tokens", {})
        return True

    def _save(self) -> None:
        if self.connection is None:
            return
        payload = {
            "users": [asdict(value) for value in self.users.values()],
            "categories": [asdict(value) for value in self.categories.values()],
            "products": [asdict(value) for value in self.products.values()],
            "cart_items": [asdict(value) for value in self.cart_items.values()],
            "orders": self.orders,
            "idempotency": [
                [user_id, key, order_id]
                for (user_id, key), order_id in self.idempotency.items()
            ],
            "used_confirmation_tokens": list(self.used_confirmation_tokens),
            "ledger": self.ledger,
            "oauth_clients": self.oauth_clients,
            "oauth_refresh_tokens": self.oauth_refresh_tokens,
        }
        with self.connection:
            self.connection.execute(
                "INSERT INTO shop_state (id, payload) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
                (json.dumps(payload, ensure_ascii=False, separators=(",", ":")),),
            )

    def persist(self) -> None:
        """将当前状态落盘；memory:// 模式为空操作。"""
        self._save()

    def user_by_name(self, username: str) -> User:
        for user in self.users.values():
            if user.username == username:
                return user
        raise ShopError(401, "invalid_credentials", "用户名或密码错误")

    def authenticate(self, username: str, password: str) -> User:
        user = self.user_by_name(username)
        if not verify_password(password, user.password_hash):
            raise ShopError(401, "invalid_credentials", "用户名或密码错误")
        return user

    def get_user(self, user_id: str) -> User:
        try:
            return self.users[user_id]
        except KeyError:
            raise ShopError(401, "invalid_token", "无效的访问令牌") from None

    def list_products(self, q: str | None, category_id: str | None) -> list[Product]:
        products = [p for p in self.products.values() if p.is_active]
        if q:
            query = q.casefold()
            products = [p for p in products if query in p.name.casefold() or query in p.description.casefold()]
        if category_id:
            products = [p for p in products if p.category_id == category_id]
        return products

    def cart(self, user_id: str) -> list[CartItem]:
        return [item for item in self.cart_items.values() if item.user_id == user_id]

    def add_cart_item(self, user_id: str, product_id: str, quantity: int) -> CartItem:
        product = self.get_product(product_id)
        if not product.is_active:
            raise ShopError(409, "product_unavailable", "商品不可售")
        for item in self.cart(user_id):
            if item.product_id == product_id:
                item.quantity += quantity
                self._save()
                return item
        item = CartItem(str(uuid4()), user_id, product_id, quantity)
        self.cart_items[item.id] = item
        self._save()
        return item

    def get_product(self, product_id: str) -> Product:
        try:
            return self.products[product_id]
        except KeyError:
            raise ShopError(404, "product_not_found", "商品不存在") from None

    def update_cart_item(self, user_id: str, item_id: str, quantity: int) -> CartItem:
        item = self.get_cart_item(user_id, item_id)
        item.quantity = quantity
        self._save()
        return item

    def delete_cart_item(self, user_id: str, item_id: str) -> None:
        self.get_cart_item(user_id, item_id)
        del self.cart_items[item_id]
        self._save()

    def get_cart_item(self, user_id: str, item_id: str) -> CartItem:
        item = self.cart_items.get(item_id)
        if item is None:
            raise ShopError(404, "cart_item_not_found", "购物车商品不存在")
        if item.user_id != user_id:
            raise ShopError(403, "forbidden", "无权访问该资源")
        return item

    def preview(self, user_id: str, shipping_address: str) -> dict:
        items = self.cart(user_id)
        if not items:
            raise ShopError(409, "empty_cart", "购物车为空")
        lines, total = self._order_lines(items)
        user = self.get_user(user_id)
        return {"items": lines, "points_total": total, "points_balance": user.points_balance,
                "sufficient_points": user.points_balance >= total, "shipping_address": shipping_address}

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

    def create_confirmed_order(self, user_id: str, shipping_address: str, confirmation_token: str) -> dict:
        """验证预览确认令牌后创建订单；令牌只能使用一次。"""
        payload = decode_confirmation_token(confirmation_token)
        token_id = payload["jti"]
        with self.lock:
            if token_id in self.used_confirmation_tokens:
                raise ShopError(409, "confirmation_token_used", "确认令牌已使用，请重新预览")
            if payload["sub"] != user_id:
                raise ShopError(403, "forbidden", "确认令牌不属于当前用户")
            if payload["shipping_address"] != shipping_address:
                raise ShopError(409, "preview_changed", "收货信息已变化，请重新预览")
            if payload["cart_fingerprint"] != self._cart_fingerprint(user_id):
                raise ShopError(409, "preview_changed", "购物车已变化，请重新预览")
            preview = self.preview(user_id, shipping_address)
            if payload["points_total"] != preview["points_total"]:
                raise ShopError(409, "preview_changed", "订单金额已变化，请重新预览")
            self.used_confirmation_tokens.add(token_id)
            return self.create_order(user_id, shipping_address, f"confirmation:{token_id}")

    def create_order(self, user_id: str, shipping_address: str, idempotency_key: str) -> dict:
        with self.lock:
            existing = self.idempotency.get((user_id, idempotency_key))
            if existing:
                return self.orders[existing]
            items = self.cart(user_id)
            if not items:
                raise ShopError(409, "empty_cart", "购物车为空")
            lines, total = self._order_lines(items)
            user = self.get_user(user_id)
            if user.points_balance < total:
                raise ShopError(409, "insufficient_points", "积分不足")
            for line in lines:
                if self.products[line["product_id"]].stock < line["quantity"]:
                    raise ShopError(409, "insufficient_stock", "库存不足")
            for line in lines:
                self.products[line["product_id"]].stock -= line["quantity"]
            user.points_balance -= total
            order_id = str(uuid4())
            order = {"id": order_id, "user_id": user_id, "status": "created", "points_total": total,
                     "shipping_address": shipping_address, "items": lines, "created_at": utc_now()}
            self.orders[order_id] = order
            self.idempotency[(user_id, idempotency_key)] = order_id
            self.ledger.append({"id": str(uuid4()), "user_id": user_id, "reason": "order", "change": -total,
                                "order_id": order_id, "balance_after": user.points_balance, "created_at": utc_now()})
            for item in items:
                del self.cart_items[item.id]
            self._save()
            return order

    def _order_lines(self, items: list[CartItem]) -> tuple[list[dict], int]:
        lines: list[dict] = []
        total = 0
        for item in items:
            product = self.get_product(item.product_id)
            if not product.is_active:
                raise ShopError(409, "product_unavailable", "商品不可售")
            line = {"product_id": product.id, "name": product.name, "points_price": product.points_price,
                    "quantity": item.quantity, "subtotal": product.points_price * item.quantity}
            lines.append(line)
            total += line["subtotal"]
        return lines, total

    def _cart_fingerprint(self, user_id: str) -> str:
        items = sorted(
            (item.product_id, item.quantity)
            for item in self.cart(user_id)
        )
        encoded = json.dumps(items, separators=(",", ":"), ensure_ascii=False).encode()
        return hashlib.sha256(encoded).hexdigest()
