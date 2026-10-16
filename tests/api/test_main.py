from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from shop.api.main import create_app
from shop.api.security import create_token
from shop.api.service import ShopService


@pytest.fixture()
def service(tmp_path) -> ShopService:
    return ShopService(database_url=f"sqlite:///{tmp_path / 'shop.db'}")


@pytest.fixture()
def client(service: ShopService) -> Iterator[TestClient]:
    with TestClient(create_app(service)) as test_client:
        yield test_client


def login_token(client: TestClient, username: str = "demo", password: str = "demo-password") -> str:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    return response.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_ok(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_login_returns_token_and_me_shows_balance(client: TestClient) -> None:
    token = login_token(client)
    me = client.get("/api/v1/me", headers=auth_headers(token))
    assert me.status_code == 200
    assert me.json() == {
        "id": "user-demo",
        "username": "demo",
        "points_balance": 1000,
        "role": "customer",
    }


def test_login_rejects_wrong_password(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"username": "demo", "password": "bad"})
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_credentials"


def test_protected_endpoints_reject_missing_or_invalid_token(client: TestClient) -> None:
    for path in ("/api/v1/me", "/api/v1/cart", "/api/v1/orders"):
        assert client.get(path).status_code == 401
        response = client.get(path, headers=auth_headers("broken.token"))
        assert response.status_code == 401
        assert response.json()["code"] == "invalid_token"


def test_public_catalog_browsing_and_pagination(client: TestClient) -> None:
    categories = client.get("/api/v1/categories")
    assert categories.status_code == 200
    assert categories.json()["items"][0]["name"] == "礼品"

    products = client.get("/api/v1/products", params={"q": "马克杯"})
    assert products.status_code == 200
    body = products.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == "product-mug"

    assert client.get("/api/v1/products", params={"page": 0}).status_code == 422
    assert client.get("/api/v1/products/product-mug").status_code == 200
    missing = client.get("/api/v1/products/nope")
    assert missing.status_code == 404
    assert missing.json()["code"] == "product_not_found"


def test_cart_crud_flow(client: TestClient) -> None:
    token = login_token(client)
    headers = auth_headers(token)

    added = client.post(
        "/api/v1/cart/items", headers=headers, json={"product_id": "product-mug", "quantity": 2}
    )
    assert added.status_code == 201
    item_id = added.json()["id"]

    cart = client.get("/api/v1/cart", headers=headers)
    assert cart.json()["points_total"] == 200

    updated = client.patch(f"/api/v1/cart/items/{item_id}", headers=headers, json={"quantity": 3})
    assert updated.status_code == 200
    assert updated.json()["quantity"] == 3
    assert client.get("/api/v1/cart", headers=headers).json()["points_total"] == 300

    deleted = client.delete(f"/api/v1/cart/items/{item_id}", headers=headers)
    assert deleted.status_code == 204
    assert client.get("/api/v1/cart", headers=headers).json()["items"] == []


def test_cart_validation_and_missing_item(client: TestClient) -> None:
    token = login_token(client)
    headers = auth_headers(token)

    invalid = client.post(
        "/api/v1/cart/items", headers=headers, json={"product_id": "product-mug", "quantity": 0}
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "validation_error"
    assert invalid.json()["request_id"]

    assert (
        client.post(
            "/api/v1/cart/items", headers=headers, json={"product_id": "nope", "quantity": 1}
        ).status_code
        == 404
    )
    assert (
        client.patch("/api/v1/cart/items/ghost", headers=headers, json={"quantity": 1}).status_code
        == 404
    )


def test_order_requires_idempotency_key(client: TestClient) -> None:
    token = login_token(client)
    headers = auth_headers(token)
    client.post(
        "/api/v1/cart/items", headers=headers, json={"product_id": "product-mug", "quantity": 1}
    )

    missing = client.post(
        "/api/v1/orders", headers=headers, json={"shipping_address": "上海市示例路 1 号"}
    )
    assert missing.status_code == 422
    assert missing.json()["code"] == "missing_idempotency_key"


def test_order_creation_is_idempotent(client: TestClient) -> None:
    token = login_token(client)
    headers = {**auth_headers(token), "Idempotency-Key": "order-key-1"}
    client.post(
        "/api/v1/cart/items",
        headers=auth_headers(token),
        json={"product_id": "product-mug", "quantity": 2},
    )

    preview = client.post(
        "/api/v1/orders/preview",
        headers=auth_headers(token),
        json={"shipping_address": "上海市示例路 1 号"},
    )
    assert preview.status_code == 200
    assert preview.json()["points_total"] == 200

    first = client.post(
        "/api/v1/orders", headers=headers, json={"shipping_address": "上海市示例路 1 号"}
    )
    repeated = client.post(
        "/api/v1/orders", headers=headers, json={"shipping_address": "上海市示例路 1 号"}
    )
    assert first.status_code == 201
    assert repeated.json()["id"] == first.json()["id"]
    assert first.json()["items"][0]["name"] == "商城马克杯"

    me = client.get("/api/v1/me", headers=auth_headers(token)).json()
    assert me["points_balance"] == 800
    listing = client.get("/api/v1/orders", headers=auth_headers(token)).json()
    assert listing["total"] == 1


def test_order_rejects_empty_cart(client: TestClient) -> None:
    token = login_token(client)
    response = client.post(
        "/api/v1/orders/preview",
        headers=auth_headers(token),
        json={"shipping_address": "上海市示例路 1 号"},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "empty_cart"


def test_order_insufficient_points_keeps_stock(client: TestClient) -> None:
    token = login_token(client)
    headers = {**auth_headers(token), "Idempotency-Key": "too-expensive"}
    client.post(
        "/api/v1/cart/items",
        headers=auth_headers(token),
        json={"product_id": "product-mug", "quantity": 11},
    )

    response = client.post(
        "/api/v1/orders", headers=headers, json={"shipping_address": "上海市示例路 1 号"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "insufficient_points"

    assert client.get("/api/v1/products/product-mug").json()["stock"] == 20
    assert client.get("/api/v1/me", headers=auth_headers(token)).json()["points_balance"] == 1000


def test_order_insufficient_stock_keeps_points(client: TestClient, service: ShopService) -> None:
    service.adjust_points("user-demo", 9000, "test")
    token = login_token(client)
    headers = {**auth_headers(token), "Idempotency-Key": "too-many"}
    client.post(
        "/api/v1/cart/items",
        headers=auth_headers(token),
        json={"product_id": "product-mug", "quantity": 21},
    )

    response = client.post(
        "/api/v1/orders", headers=headers, json={"shipping_address": "上海市示例路 1 号"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "insufficient_stock"

    assert client.get("/api/v1/products/product-mug").json()["stock"] == 20
    assert client.get("/api/v1/me", headers=auth_headers(token)).json()["points_balance"] == 10000


def test_order_ownership(client: TestClient, service: ShopService) -> None:
    service.create_user("alice", "alice-password", 500)
    demo_token = login_token(client)
    client.post(
        "/api/v1/cart/items",
        headers=auth_headers(demo_token),
        json={"product_id": "product-mug", "quantity": 1},
    )
    order = client.post(
        "/api/v1/orders",
        headers={**auth_headers(demo_token), "Idempotency-Key": "demo-order"},
        json={"shipping_address": "上海市示例路 1 号"},
    ).json()
    order_id = order["id"]

    alice_token = login_token(client, "alice", "alice-password")
    assert client.get("/api/v1/orders", headers=auth_headers(alice_token)).json()["total"] == 0

    forbidden = client.get(f"/api/v1/orders/{order_id}", headers=auth_headers(alice_token))
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "forbidden"

    assert (
        client.get(f"/api/v1/orders/{order_id}", headers=auth_headers(demo_token)).json()["id"]
        == order_id
    )
    assert client.get("/api/v1/orders/ghost", headers=auth_headers(demo_token)).status_code == 404


def test_expired_token_is_rejected_then_relogin_works(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = login_token(client)
    assert client.get("/api/v1/me", headers=auth_headers(token)).status_code == 200

    monkeypatch.setenv("SHOP_TOKEN_EXPIRE_MINUTES", "-1")
    expired = create_token("user-demo")
    response = client.get("/api/v1/me", headers=auth_headers(expired))
    assert response.status_code == 401
    assert response.json()["code"] == "invalid_token"

    monkeypatch.setenv("SHOP_TOKEN_EXPIRE_MINUTES", "60")
    fresh = login_token(client)
    assert client.get("/api/v1/me", headers=auth_headers(fresh)).status_code == 200


def test_shop_error_keeps_uniform_error_body(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "x"})
    body = response.json()
    assert response.status_code == 401
    assert set(body) == {"code", "message", "request_id"}
    assert isinstance(body["request_id"], str)
