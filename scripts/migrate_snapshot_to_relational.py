"""一次性迁移脚本：将旧版单表 JSON 快照（shop_state）导入关系型表。

旧版架构将全量业务状态序列化存进 SQLite 单表 shop_state(id, payload)；
本脚本读取 payload 并整批写入 SQLAlchemy 关系表，迁移完成后可选择删除旧表。

用法：
    uv run python scripts/migrate_snapshot_to_relational.py [--database URL] [--dry-run]
    uv run python scripts/migrate_snapshot_to_relational.py --drop-legacy

- --database    目标库 URL；缺省读取 SHOP_DATABASE_URL，再缺省 sqlite:///./shop.db
- --dry-run    只统计快照内容，不写入
- --drop-legacy 成功后删除 shop_state 表（可再次运行本脚本前保留旧表以便回滚）

迁移策略：整库替换——先清空业务表（结构保留），再按快照全量写入，
因此本脚本可以重复执行且结果一致。
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

# 支持直接以 python scripts/xxx.py 方式运行（未安装项目包时）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from shop.api.security import hash_password  # noqa: E402
from shop.storage.models import (  # noqa: E402
    CartItemModel,
    CategoryModel,
    IdempotencyKeyModel,
    OAuthClientModel,
    OAuthRefreshTokenModel,
    OrderItemModel,
    OrderModel,
    PointLedgerModel,
    ProductModel,
    UsedConfirmationTokenModel,
    UserModel,
    utc_now,
)
from shop.storage.repository import clear_order_data  # noqa: E402


def load_snapshot(engine) -> dict:
    """读取旧版 shop_state 表的单行 JSON payload。"""
    inspector = inspect(engine)
    if not inspector.has_table("shop_state"):
        print("未找到旧版 shop_state 表，无需迁移。")
        raise SystemExit(0)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT payload FROM shop_state")).fetchall()
    if len(rows) != 1:
        print(f"shop_state 表应有且仅有一行快照，实际 {len(rows)} 行。")
        raise SystemExit(1)
    return json.loads(rows[0][0])


def parse_time(value) -> datetime:
    """兼容旧快照中的 ISO 字符串与 unix 时间戳两种写法。"""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return utc_now()


def snapshot_orders(raw) -> list[dict]:
    """旧版 orders 可能是 dict（id → order）或 list，统一成 list。"""
    if isinstance(raw, dict):
        return list(raw.values())
    return list(raw or [])


def snapshot_idempotency(raw, orders: list[dict]) -> list[tuple[str, str]]:
    """旧版 idempotency 可能是 list[dict] / dict[key, order_id]；返回 (user_id, key, order_id)。"""
    order_owner = {order["id"]: order["user_id"] for order in orders}
    entries: list[tuple[str, str]] = []
    if isinstance(raw, dict):
        for key, order_id in raw.items():
            user_id = order_owner.get(order_id, "")
            if "|" in key and not user_id:
                user_id, key = key.split("|", 1)
            entries.append((user_id, key, order_id))
    else:
        for item in raw or []:
            entries.append((item["user_id"], item["key"], item["order_id"]))
    return entries


def snapshot_tokens(raw) -> list[tuple[str, str]]:
    """旧版 used_confirmation_tokens 可能是 list[str] / list[dict]；返回 (jti, user_id)。"""
    entries: list[tuple[str, str]] = []
    for item in raw or []:
        if isinstance(item, str):
            entries.append((item, "unknown"))
        else:
            entries.append((item["jti"], item.get("user_id", "unknown")))
    return entries


def import_snapshot(session: Session, snapshot: dict) -> dict:
    clear_order_data(session)

    for row in snapshot.get("users", []):
        session.add(
            UserModel(
                id=row["id"],
                username=row["username"],
                password_hash=row["password_hash"],
                points_balance=int(row["points_balance"]),
                role=row.get("role", "customer"),
                created_at=parse_time(row.get("created_at")),
            )
        )

    # 旧快照没有管理员账号；为保证管理后台可用，缺省时重建默认管理员
    if not any(u.get("role") == "admin" for u in snapshot.get("users", [])):
        session.add(
            UserModel(
                id="user-admin",
                username="admin",
                password_hash=hash_password("admin-password"),
                points_balance=0,
                role="admin",
                created_at=utc_now(),
            )
        )

    for row in snapshot.get("categories", []):
        session.add(
            CategoryModel(
                id=row["id"],
                name=row["name"],
                is_active=bool(row.get("is_active", True)),
            )
        )

    for row in snapshot.get("products", []):
        session.add(
            ProductModel(
                id=row["id"],
                name=row["name"],
                description=row["description"],
                points_price=int(row["points_price"]),
                stock=int(row["stock"]),
                category_id=row["category_id"],
                is_active=bool(row.get("is_active", True)),
                image_url=row.get("image_url"),
            )
        )

    for row in snapshot.get("cart_items", []):
        session.add(
            CartItemModel(
                id=row.get("id") or f"cart-{uuid4()}",
                user_id=row["user_id"],
                product_id=row["product_id"],
                quantity=int(row["quantity"]),
            )
        )

    orders = snapshot_orders(snapshot.get("orders"))
    for order in orders:
        session.add(
            OrderModel(
                id=order["id"],
                user_id=order["user_id"],
                status=order.get("status", "created"),
                points_total=int(order["points_total"]),
                shipping_address=order["shipping_address"],
                created_at=parse_time(order.get("created_at")),
            )
        )
        for item in order.get("items", []):
            session.add(
                OrderItemModel(
                    id=f"oi-{uuid4()}",
                    order_id=order["id"],
                    product_id=item["product_id"],
                    product_name=item.get("name", item["product_id"]),
                    points_price=int(item["points_price"]),
                    quantity=int(item["quantity"]),
                    subtotal=int(item["subtotal"]),
                )
            )

    for user_id, key, order_id in snapshot_idempotency(snapshot.get("idempotency"), orders):
        session.add(
            IdempotencyKeyModel(user_id=user_id, key=key, order_id=order_id, created_at=utc_now())
        )

    for jti, user_id in snapshot_tokens(snapshot.get("used_confirmation_tokens")):
        session.add(UsedConfirmationTokenModel(jti=jti, user_id=user_id, used_at=utc_now()))

    for row in snapshot.get("ledger", []):
        session.add(
            PointLedgerModel(
                id=row.get("id") or f"ledger-{uuid4()}",
                user_id=row["user_id"],
                reason=row["reason"],
                change=int(row["change"]),
                order_id=row.get("order_id"),
                balance_after=int(row["balance_after"]),
                note=row.get("note"),
                created_at=parse_time(row.get("created_at")),
            )
        )

    for client_id, payload in (snapshot.get("oauth_clients") or {}).items():
        session.add(OAuthClientModel(client_id=client_id, payload=payload))

    for token, row in (snapshot.get("oauth_refresh_tokens") or {}).items():
        session.add(
            OAuthRefreshTokenModel(
                token=token,
                user_id=row["user_id"],
                client_id=row["client_id"],
                created_at=parse_time(row.get("created_at")),
            )
        )

    return {
        "users": len(snapshot.get("users", [])),
        "categories": len(snapshot.get("categories", [])),
        "products": len(snapshot.get("products", [])),
        "cart_items": len(snapshot.get("cart_items", [])),
        "orders": len(orders),
        "idempotency": len(snapshot_idempotency(snapshot.get("idempotency"), orders)),
        "used_confirmation_tokens": len(snapshot_tokens(snapshot.get("used_confirmation_tokens"))),
        "ledger": len(snapshot.get("ledger", [])),
        "oauth_clients": len(snapshot.get("oauth_clients") or {}),
        "oauth_refresh_tokens": len(snapshot.get("oauth_refresh_tokens") or {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="旧版 JSON 快照 → 关系型表 一次性迁移")
    parser.add_argument(
        "--database",
        default=None,
        help="目标库 URL（缺省 SHOP_DATABASE_URL 或 sqlite:///./shop.db）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计快照内容，不写入")
    parser.add_argument("--drop-legacy", action="store_true", help="迁移成功后删除 shop_state 表")
    args = parser.parse_args()

    database_url = args.database or os.getenv("SHOP_DATABASE_URL", "sqlite:///./shop.db")
    engine = create_engine(database_url)
    snapshot = load_snapshot(engine)

    if args.dry_run:
        orders = snapshot_orders(snapshot.get("orders"))
        print("快照内容统计（dry-run，不写入）：")
        print(f"  users={len(snapshot.get('users', []))}")
        print(f"  categories={len(snapshot.get('categories', []))}")
        print(f"  products={len(snapshot.get('products', []))}")
        print(f"  cart_items={len(snapshot.get('cart_items', []))}")
        print(f"  orders={len(orders)}")
        print(f"  idempotency={len(snapshot_idempotency(snapshot.get('idempotency'), orders))}")
        tokens_count = len(snapshot_tokens(snapshot.get("used_confirmation_tokens")))
        print(f"  used_confirmation_tokens={tokens_count}")
        print(f"  ledger={len(snapshot.get('ledger', []))}")
        print(f"  oauth_clients={len(snapshot.get('oauth_clients') or {})}")
        print(f"  oauth_refresh_tokens={len(snapshot.get('oauth_refresh_tokens') or {})}")
        return

    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine)
    with factory.begin() as session:
        stats = import_snapshot(session, snapshot)

    if args.drop_legacy:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS shop_state"))
        print("已删除旧表 shop_state。")

    print("迁移完成：")
    for key, count in stats.items():
        print(f"  {key}={count}")


if __name__ == "__main__":
    main()
