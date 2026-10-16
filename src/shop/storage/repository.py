"""数据访问函数：以 SQLAlchemy Session 为第一参数，服务层组合调用。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .models import (
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

# ---------- users ----------


def get_user(session: Session, user_id: str) -> UserModel | None:
    return session.get(UserModel, user_id)


def get_user_for_update(session: Session, user_id: str) -> UserModel | None:
    stmt = select(UserModel).where(UserModel.id == user_id).with_for_update()
    return session.execute(stmt).scalar_one_or_none()


def get_user_by_name(session: Session, username: str) -> UserModel | None:
    stmt = select(UserModel).where(UserModel.username == username)
    return session.execute(stmt).scalar_one_or_none()


def insert_user(
    session: Session,
    *,
    user_id: str,
    username: str,
    password_hash: str,
    points_balance: int,
    role: str,
) -> UserModel:
    model = UserModel(
        id=user_id,
        username=username,
        password_hash=password_hash,
        points_balance=points_balance,
        role=role,
        created_at=utc_now(),
    )
    session.add(model)
    return model


def list_users(
    session: Session, q: str | None, page: int, page_size: int
) -> tuple[list[UserModel], int]:
    stmt = select(UserModel).order_by(UserModel.created_at)
    if q:
        stmt = stmt.where(UserModel.username.contains(q))
    total = session.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = session.execute(stmt.limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return list(rows), int(total)


# ---------- categories ----------


def list_categories(session: Session, active_only: bool) -> list[CategoryModel]:
    stmt = select(CategoryModel).order_by(CategoryModel.id)
    if active_only:
        stmt = stmt.where(CategoryModel.is_active.is_(True))
    return list(session.execute(stmt).scalars().all())


def get_category(session: Session, category_id: str) -> CategoryModel | None:
    return session.get(CategoryModel, category_id)


def insert_category(session: Session, *, category_id: str, name: str) -> CategoryModel:
    model = CategoryModel(id=category_id, name=name, is_active=True)
    session.add(model)
    return model


# ---------- products ----------


def get_product(session: Session, product_id: str) -> ProductModel | None:
    return session.get(ProductModel, product_id)


def list_products(
    session: Session, q: str | None, category_id: str | None, active_only: bool
) -> list[ProductModel]:
    stmt = select(ProductModel).order_by(ProductModel.id)
    if active_only:
        stmt = stmt.where(ProductModel.is_active.is_(True))
    if category_id:
        stmt = stmt.where(ProductModel.category_id == category_id)
    if q:
        query = q.casefold()
        stmt = stmt.where(
            (func.lower(ProductModel.name).contains(query))
            | (func.lower(ProductModel.description).contains(query))
        )
    return list(session.execute(stmt).scalars().all())


def insert_product(
    session: Session,
    *,
    product_id: str,
    name: str,
    description: str,
    points_price: int,
    stock: int,
    category_id: str,
    image_url: str | None,
) -> ProductModel:
    model = ProductModel(
        id=product_id,
        name=name,
        description=description,
        points_price=points_price,
        stock=stock,
        category_id=category_id,
        is_active=True,
        image_url=image_url,
    )
    session.add(model)
    return model


def update_product(session: Session, product_id: str, changes: dict) -> None:
    if changes:
        session.execute(update(ProductModel).where(ProductModel.id == product_id).values(**changes))


# ---------- cart ----------


def get_cart_items(session: Session, user_id: str) -> list[CartItemModel]:
    stmt = select(CartItemModel).where(CartItemModel.user_id == user_id).order_by(CartItemModel.id)
    return list(session.execute(stmt).scalars().all())


def get_cart_item(session: Session, item_id: str) -> CartItemModel | None:
    return session.get(CartItemModel, item_id)


def upsert_cart_item(
    session: Session, *, user_id: str, product_id: str, quantity: int, dialect_name: str
) -> CartItemModel:
    """同商品重复加购时原子累加数量，消除并发竞态。"""
    if dialect_name == "mysql":
        stmt = mysql_insert(CartItemModel).values(
            id=str(uuid4()), user_id=user_id, product_id=product_id, quantity=quantity
        )
        stmt = stmt.on_duplicate_key_update(quantity=CartItemModel.quantity + quantity)
    else:
        stmt = sqlite_insert(CartItemModel).values(
            id=str(uuid4()), user_id=user_id, product_id=product_id, quantity=quantity
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "product_id"],
            set_={"quantity": CartItemModel.quantity + quantity},
        )
    session.execute(stmt)
    found = session.execute(
        select(CartItemModel).where(
            CartItemModel.user_id == user_id, CartItemModel.product_id == product_id
        )
    ).scalar_one()
    return found


def delete_cart_item_model(session: Session, item: CartItemModel) -> None:
    session.delete(item)


# ---------- orders / idempotency / ledger ----------


def find_idempotent_order(session: Session, user_id: str, key: str) -> OrderModel | None:
    stmt = (
        select(OrderModel)
        .join(IdempotencyKeyModel, IdempotencyKeyModel.order_id == OrderModel.id)
        .where(IdempotencyKeyModel.user_id == user_id, IdempotencyKeyModel.key == key)
    )
    return session.execute(stmt).scalar_one_or_none()


def get_order(session: Session, order_id: str) -> OrderModel | None:
    return session.get(OrderModel, order_id)


def list_orders_by_user(
    session: Session, user_id: str, page: int, page_size: int
) -> tuple[list[OrderModel], int]:
    base = select(OrderModel).where(OrderModel.user_id == user_id)
    return _paginate_orders(session, base, page, page_size)


def list_orders(
    session: Session, page: int, page_size: int, user_id: str | None = None
) -> tuple[list[OrderModel], int]:
    base = select(OrderModel)
    if user_id:
        base = base.where(OrderModel.user_id == user_id)
    return _paginate_orders(session, base, page, page_size)


def _paginate_orders(
    session: Session, base, page: int, page_size: int
) -> tuple[list[OrderModel], int]:
    base = base.order_by(OrderModel.created_at.desc())
    total = session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = session.execute(base.limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return list(rows), int(total)


def find_idempotency(session: Session, user_id: str, key: str) -> IdempotencyKeyModel | None:
    return session.get(IdempotencyKeyModel, (user_id, key))


def insert_idempotency(session: Session, *, user_id: str, key: str, order_id: str) -> None:
    session.add(
        IdempotencyKeyModel(user_id=user_id, key=key, order_id=order_id, created_at=utc_now())
    )


def insert_ledger(
    session: Session,
    *,
    user_id: str,
    reason: str,
    change: int,
    order_id: str | None,
    balance_after: int,
    note: str | None = None,
) -> PointLedgerModel:
    model = PointLedgerModel(
        id=str(uuid4()),
        user_id=user_id,
        reason=reason,
        change=change,
        order_id=order_id,
        balance_after=balance_after,
        note=note,
        created_at=utc_now(),
    )
    session.add(model)
    return model


def list_ledger(
    session: Session, user_id: str | None, page: int, page_size: int
) -> tuple[list[PointLedgerModel], int]:
    base = select(PointLedgerModel)
    if user_id:
        base = base.where(PointLedgerModel.user_id == user_id)
    base = base.order_by(PointLedgerModel.created_at.desc())
    total = session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    rows = session.execute(base.limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return list(rows), int(total)


def get_used_confirmation_token(session: Session, jti: str) -> UsedConfirmationTokenModel | None:
    return session.get(UsedConfirmationTokenModel, jti)


def insert_used_confirmation_token(session: Session, *, jti: str, user_id: str) -> None:
    session.add(UsedConfirmationTokenModel(jti=jti, user_id=user_id, used_at=utc_now()))


def clear_order_data(session: Session) -> None:
    """迁移脚本专用：清空业务表数据（保留结构）。"""
    for model in (
        IdempotencyKeyModel,
        UsedConfirmationTokenModel,
        PointLedgerModel,
        OrderItemModel,
        OrderModel,
        CartItemModel,
        ProductModel,
        CategoryModel,
        UserModel,
        OAuthRefreshTokenModel,
        OAuthClientModel,
    ):
        session.execute(delete(model))


# ---------- oauth ----------


def get_oauth_client(session: Session, client_id: str) -> OAuthClientModel | None:
    return session.get(OAuthClientModel, client_id)


def upsert_oauth_client(session: Session, *, client_id: str, payload: dict) -> None:
    existing = session.get(OAuthClientModel, client_id)
    if existing is None:
        session.add(OAuthClientModel(client_id=client_id, payload=payload))
    else:
        existing.payload = payload


def get_refresh_token(session: Session, token: str) -> OAuthRefreshTokenModel | None:
    return session.get(OAuthRefreshTokenModel, token)


def insert_refresh_token(session: Session, *, token: str, user_id: str, client_id: str) -> None:
    session.add(
        OAuthRefreshTokenModel(
            token=token, user_id=user_id, client_id=client_id, created_at=utc_now()
        )
    )


def delete_refresh_token(session: Session, token: str) -> OAuthRefreshTokenModel | None:
    model = session.get(OAuthRefreshTokenModel, token)
    if model is not None:
        session.delete(model)
    return model


def parse_datetime(value: str) -> datetime:
    """快照迁移脚本用：解析旧 JSON 中的 ISO 时间字符串。"""
    return datetime.fromisoformat(value).astimezone(UTC)
