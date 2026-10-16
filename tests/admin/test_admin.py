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


def admin_login(client: TestClient) -> str:
    response = client.post(
        "/admin/api/v1/auth/login", json={"username": "admin", "password": "admin-password"}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_admin_login_rejects_customer_account(client: TestClient) -> None:
    response = client.post(
        "/admin/api/v1/auth/login", json={"username": "demo", "password": "demo-password"}
    )
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_admin_endpoints_require_admin_token(client: TestClient) -> None:
    # 匿名/customer/旧格式无 role 令牌均拒绝
    assert client.get("/admin/api/v1/products").status_code == 401
    customer_token = create_token("user-demo", role="customer")
    forbidden = client.get("/admin/api/v1/products", headers=auth(customer_token))
    assert forbidden.status_code == 403
    legacy_token = create_token("user-admin")
    assert client.get("/admin/api/v1/products", headers=auth(legacy_token)).status_code == 403


def test_product_crud_and_status_flow(client: TestClient) -> None:
    token = admin_login(client)
    headers = auth(token)

    created = client.post(
        "/admin/api/v1/products",
        headers=headers,
        json={
            "name": "新款笔记本",
            "description": "A5 硬壳笔记本",
            "points_price": 150,
            "stock": 10,
            "category_id": "cat-gifts",
        },
    )
    assert created.status_code == 201
    product_id = created.json()["id"]

    updated = client.patch(
        f"/admin/api/v1/products/{product_id}", headers=headers, json={"points_price": 120}
    )
    assert updated.status_code == 200
    assert updated.json()["points_price"] == 120

    hidden = client.patch(
        f"/admin/api/v1/products/{product_id}/status", headers=headers, json={"is_active": False}
    )
    assert hidden.json()["is_active"] is False
    assert client.get(f"/api/v1/products/{product_id}").status_code == 404

    stocked = client.patch(
        f"/admin/api/v1/products/{product_id}/stock", headers=headers, json={"delta": 5}
    )
    assert stocked.json()["stock"] == 15
    assert (
        client.patch(
            f"/admin/api/v1/products/{product_id}/stock", headers=headers, json={"delta": -100}
        ).status_code
        == 422
    )

    missing = client.patch("/admin/api/v1/products/nope", headers=headers, json={"points_price": 1})
    assert missing.status_code == 404

    # 管理端列表包含下架商品，C 端不展示
    admin_list = client.get("/admin/api/v1/products", headers=headers).json()
    assert admin_list["total"] == 2
    customer_list = client.get("/api/v1/products").json()
    assert customer_list["total"] == 1


def test_category_create_update(client: TestClient) -> None:
    token = admin_login(client)
    headers = auth(token)

    created = client.post("/admin/api/v1/categories", headers=headers, json={"name": "数码"})
    assert created.status_code == 201
    category_id = created.json()["id"]

    disabled = client.patch(
        f"/admin/api/v1/categories/{category_id}", headers=headers, json={"is_active": False}
    )
    assert disabled.json()["is_active"] is False

    visible = {item["id"] for item in client.get("/api/v1/categories").json()["items"]}
    assert category_id not in visible
    admin_visible = {
        item["id"]
        for item in client.get("/admin/api/v1/categories", headers=headers).json()["items"]
    }
    assert category_id in admin_visible


def test_adjust_points_writes_ledger(client: TestClient) -> None:
    token = admin_login(client)
    headers = auth(token)

    adjusted = client.post(
        "/admin/api/v1/users/user-demo/points/adjust",
        headers=headers,
        json={"change": 500, "note": "活动奖励"},
    )
    assert adjusted.status_code == 200
    assert adjusted.json()["points_balance"] == 1500

    negative = client.post(
        "/admin/api/v1/users/user-demo/points/adjust",
        headers=headers,
        json={"change": -99999},
    )
    assert negative.status_code == 422

    ledger = client.get(
        "/admin/api/v1/ledger", headers=headers, params={"user_id": "user-demo"}
    ).json()
    assert ledger["total"] == 1
    assert ledger["items"][0]["reason"] == "admin_adjust"
    assert ledger["items"][0]["note"] == "活动奖励"


def test_user_list_search_and_detail(client: TestClient) -> None:
    token = admin_login(client)
    headers = auth(token)

    listing = client.get("/admin/api/v1/users", headers=headers, params={"q": "dem"}).json()
    assert listing["total"] == 1
    assert listing["items"][0]["username"] == "demo"

    detail = client.get("/admin/api/v1/users/user-demo", headers=headers).json()
    assert detail["id"] == "user-demo"
    assert "recent_ledger" in detail


def test_order_admin_view(client: TestClient) -> None:
    # 先以 C 端身份下一单
    demo_token = client.post(
        "/api/v1/auth/login", json={"username": "demo", "password": "demo-password"}
    ).json()["access_token"]
    client.post(
        "/api/v1/cart/items",
        headers=auth(demo_token),
        json={"product_id": "product-mug", "quantity": 1},
    )
    order = client.post(
        "/api/v1/orders",
        headers={**auth(demo_token), "Idempotency-Key": "admin-view-1"},
        json={"shipping_address": "上海市示例路 1 号"},
    ).json()

    token = admin_login(client)
    headers = auth(token)

    listing = client.get("/admin/api/v1/orders", headers=headers).json()
    assert listing["total"] == 1
    assert listing["items"][0]["id"] == order["id"]

    detail = client.get(f"/admin/api/v1/orders/{order['id']}", headers=headers).json()
    assert detail["points_total"] == 100
    assert detail["items"][0]["name"] == "商城马克杯"

    missing = client.get("/admin/api/v1/orders/nope", headers=headers)
    assert missing.status_code == 404
