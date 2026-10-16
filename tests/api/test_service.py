from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from shop.api.errors import ShopError
from shop.api.service import ShopService, User


def memory_service() -> ShopService:
    return ShopService(database_url="memory://")


def test_order_is_idempotent_and_records_snapshot() -> None:
    service = memory_service()
    user = service.users["user-demo"]
    service.add_cart_item(user.id, "product-mug", 2)

    first = service.create_order(user.id, "Shanghai", "request-1")
    repeated = service.create_order(user.id, "Shanghai", "request-1")

    assert repeated is first
    assert first["points_total"] == 200
    assert first["items"][0]["name"] == "商城马克杯"
    assert user.points_balance == 800
    assert service.products["product-mug"].stock == 18
    assert len(service.ledger) == 1


def test_order_rejects_insufficient_points_without_changing_stock() -> None:
    service = memory_service()
    user = service.users["user-demo"]
    service.add_cart_item(user.id, "product-mug", 11)

    try:
        service.create_order(user.id, "Shanghai", "request-2")
    except ShopError as error:
        assert error.code == "insufficient_points"
    else:
        raise AssertionError("expected insufficient_points")

    assert user.points_balance == 1000
    assert service.products["product-mug"].stock == 20


def test_cart_item_belongs_to_its_owner() -> None:
    service = memory_service()
    item = service.add_cart_item("user-demo", "product-mug", 1)

    try:
        service.update_cart_item("another-user", item.id, 2)
    except ShopError as error:
        assert error.status_code == 403
    else:
        raise AssertionError("expected forbidden")


def test_confirmation_token_creates_order_once() -> None:
    service = memory_service()
    service.add_cart_item("user-demo", "product-mug", 2)

    preview = service.preview_checkout("user-demo", "Shanghai")
    order = service.create_confirmed_order(
        "user-demo", "Shanghai", str(preview["confirmation_token"])
    )

    assert order["points_total"] == 200
    try:
        service.create_confirmed_order(
            "user-demo", "Shanghai", str(preview["confirmation_token"])
        )
    except ShopError as error:
        assert error.code == "confirmation_token_used"
    else:
        raise AssertionError("expected confirmation_token_used")


def test_confirmation_token_rejects_changed_cart() -> None:
    service = memory_service()
    service.add_cart_item("user-demo", "product-mug", 1)
    preview = service.preview_checkout("user-demo", "Shanghai")
    service.add_cart_item("user-demo", "product-mug", 1)

    try:
        service.create_confirmed_order(
            "user-demo", "Shanghai", str(preview["confirmation_token"])
        )
    except ShopError as error:
        assert error.code == "preview_changed"
    else:
        raise AssertionError("expected preview_changed")


def test_sqlite_persists_cart_between_service_instances(tmp_path: Path) -> None:
    database_url = f"sqlite:///{tmp_path / 'shop.db'}"
    first = ShopService(database_url=database_url)
    first.add_cart_item("user-demo", "product-mug", 2)

    second = ShopService(database_url=database_url)

    assert len(second.cart("user-demo")) == 1
    assert second.cart("user-demo")[0].quantity == 2


def test_concurrent_orders_with_same_key_deduct_once() -> None:
    service = memory_service()
    user = service.users["user-demo"]
    service.add_cart_item(user.id, "product-mug", 2)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: service.create_order(user.id, "Shanghai", "race-key"),
                range(4),
            )
        )

    assert len({order["id"] for order in results}) == 1
    assert user.points_balance == 800
    assert service.products["product-mug"].stock == 18
    assert len(service.ledger) == 1


def test_concurrent_orders_do_not_oversell_stock() -> None:
    service = memory_service()
    buyer_ids = ["user-demo"]
    for index in range(4):
        buyer_id = f"user-race-{index}"
        service.users[buyer_id] = User(buyer_id, f"race-{index}", "not-a-hash", 1000)
        buyer_ids.append(buyer_id)
    for buyer_id in buyer_ids:
        service.add_cart_item(buyer_id, "product-mug", 6)

    def buy(buyer_id: str) -> dict:
        try:
            return service.create_order(buyer_id, "Shanghai", f"key-{buyer_id}")
        except ShopError as error:
            return {"error": error.code}

    with ThreadPoolExecutor(max_workers=len(buyer_ids)) as pool:
        results = list(pool.map(buy, buyer_ids))

    succeeded = [result for result in results if "error" not in result]
    failed = {result["error"] for result in results if "error" in result}

    assert len(succeeded) == 3
    assert failed == {"insufficient_stock"}
    assert service.products["product-mug"].stock == 2
    assert all(service.users[buyer_id].points_balance in (400, 1000) for buyer_id in buyer_ids)
    assert len(service.ledger) == 3
