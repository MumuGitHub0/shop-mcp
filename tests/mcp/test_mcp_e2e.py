import asyncio

import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError


def _login(base_url: str, username: str = "demo", password: str = "demo-password") -> str:
    response = httpx.post(
        f"{base_url}/api/v1/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _mcp_client(base_url: str, token: str | None = None) -> Client:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    return Client(StreamableHttpTransport(url=f"{base_url}/mcp", headers=headers))


def test_mcp_rejects_missing_and_invalid_tokens(base_url: str) -> None:
    # 认证中间件在 HTTP 层拒绝：未携带或伪造令牌一律 401 并携带发现元数据。
    assert httpx.post(f"{base_url}/mcp").status_code == 401
    forged = httpx.post(
        f"{base_url}/mcp",
        headers={"Authorization": "Bearer forged.value"},
        json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
    )
    assert forged.status_code == 401
    assert "resource_metadata=" in forged.headers.get("WWW-Authenticate", "")

    async def scenario() -> None:
        # 初始化握手阶段即被认证中间件拒绝，异常在进入客户端上下文时抛出。
        with pytest.raises(MCPError):
            async with _mcp_client(base_url) as client:
                await client.list_tools()

        with pytest.raises(MCPError):
            async with _mcp_client(base_url, token="forged.value") as client:
                await client.list_tools()

    asyncio.run(scenario())


def test_mcp_end_to_end_shopping_flow(base_url: str) -> None:
    async def scenario() -> None:
        token = _login(base_url)
        async with _mcp_client(base_url, token) as client:
            search = await client.call_tool("search_products", {"query": "马克杯"})
            assert search.data["total"] == 1
            assert search.data["items"][0]["stock_status"] == "in_stock"

            profile = await client.call_tool("get_my_profile", {})
            assert profile.data["points_balance"] == 1000

            added = await client.call_tool(
                "add_to_cart", {"product_id": "product-mug", "quantity": 2}
            )
            assert added.data["quantity"] == 2

            cart = await client.call_tool("get_cart", {})
            assert cart.data["points_total"] == 200

            with pytest.raises(ToolError) as bad_quantity:
                await client.call_tool("add_to_cart", {"product_id": "product-mug", "quantity": 0})
            assert "数量必须为正数" in str(bad_quantity.value)

            preview = await client.call_tool(
                "preview_checkout", {"shipping_address": "上海市示例路 1 号"}
            )
            assert preview.data["points_total"] == 200
            confirmation = str(preview.data["confirmation_token"])

            order = await client.call_tool(
                "place_order",
                {"shipping_address": "上海市示例路 1 号", "confirmation_token": confirmation},
            )
            assert order.data["status"] == "created"
            assert order.data["points_deducted"] == 200

            with pytest.raises(ToolError) as replay:
                await client.call_tool(
                    "place_order",
                    {"shipping_address": "上海市示例路 1 号", "confirmation_token": confirmation},
                )
            assert "确认令牌已使用" in str(replay.value)

            orders = await client.call_tool("get_my_orders", {})
            assert orders.data["total"] == 1
            assert orders.data["items"][0]["points_total"] == 200

        fresh = _login(base_url)
        async with _mcp_client(base_url, fresh) as client:
            profile = await client.call_tool("get_my_profile", {})
            assert profile.data["points_balance"] == 800

    asyncio.run(scenario())
