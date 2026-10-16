"""OAuth 2.1 浏览器登录流程的端到端测试。

以 httpx 模拟 MCP 客户端的 OAuth 行为（发现、动态注册、浏览器登录、
令牌交换与刷新），并用 fastmcp Client 验证签发的令牌可实连 /mcp。
"""

import asyncio
import base64
import hashlib
import os
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from shop.api.security import create_token, decode_token_details
from shop.api.service import ShopService
from shop.mcp.oauth import ShopOAuthProvider

CALLBACK_URI = "http://localhost:9/oauth-callback"


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    return verifier, challenge


def _register_client(client: httpx.Client, base_url: str) -> str:
    response = client.post(
        f"{base_url}/register",
        json={
            "redirect_uris": [CALLBACK_URI],
            "client_name": "oauth-e2e",
            "grant_types": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["client_id"]


def _start_authorization(client: httpx.Client, base_url: str, client_id: str) -> tuple[str, str]:
    """发起授权请求，返回 (登录页 pending_id, PKCE verifier)。"""
    verifier, challenge = _pkce()
    authorize_url = f"{base_url}/authorize?" + urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": CALLBACK_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "e2e-state",
        }
    )
    step = client.get(authorize_url, headers={"Accept": "text/html"}, follow_redirects=False)
    assert step.status_code == 302, step.text
    location = step.headers["location"]
    assert "/login?pending_id=" in location
    page = client.get(location)
    assert page.status_code == 200, page.text
    assert "用户名" in page.text and "登录并授权" in page.text
    pending_id = parse_qs(urlparse(location).query)["pending_id"][0]
    return pending_id, verifier


def _submit_login(
    client: httpx.Client, base_url: str, pending_id: str, password: str = "demo-password"
) -> httpx.Response:
    return client.post(
        f"{base_url}/login",
        data={"username": "demo", "password": password, "pending_id": pending_id},
        follow_redirects=False,
    )


def _exchange_code(
    client: httpx.Client, base_url: str, client_id: str, code: str, verifier: str
) -> dict:
    response = client.post(
        f"{base_url}/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CALLBACK_URI,
            "code_verifier": verifier,
            "client_id": client_id,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"] and body["refresh_token"]
    return body


def _run_authorization_flow(client: httpx.Client, base_url: str, client_id: str) -> dict:
    """注册 → 浏览器登录 → 令牌交换，返回 token 响应体。"""
    pending_id, verifier = _start_authorization(client, base_url, client_id)
    submitted = _submit_login(client, base_url, pending_id)
    assert submitted.status_code == 302, submitted.text
    callback = urlparse(submitted.headers["location"])
    query = parse_qs(callback.query)
    assert query["state"] == ["e2e-state"]
    return _exchange_code(client, base_url, client_id, query["code"][0], verifier)


async def _call_profile(base_url: str, access_token: str) -> dict:
    async with Client(
        StreamableHttpTransport(
            url=f"{base_url}/mcp", headers={"Authorization": f"Bearer {access_token}"}
        )
    ) as client:
        result = await client.call_tool("get_my_profile", {})
    return result.data


def test_oauth_discovery_and_challenge(base_url: str) -> None:
    with httpx.Client() as client:
        # stateless 模式下 /mcp 仅注册 POST/DELETE，认证挑战统一走 POST。
        unauthorized = client.post(f"{base_url}/mcp", json={})
        assert unauthorized.status_code == 401
        www_auth = unauthorized.headers.get("WWW-Authenticate", "")
        assert "resource_metadata=" in www_auth

        metadata_url = www_auth.split('resource_metadata="')[1].split('"')[0]
        resource = client.get(metadata_url)
        assert resource.status_code == 200
        assert resource.json()["authorization_servers"]

        server_meta = client.get(f"{base_url}/.well-known/oauth-authorization-server")
        assert server_meta.status_code == 200
        meta = server_meta.json()
        assert meta["authorization_endpoint"].startswith(base_url)
        assert meta["token_endpoint"].startswith(base_url)
        assert meta["registration_endpoint"].startswith(base_url)


def test_full_authorization_code_flow(base_url: str) -> None:
    with httpx.Client() as client:
        client_id = _register_client(client, base_url)
        tokens = _run_authorization_flow(client, base_url, client_id)

    user_id, expires_at = decode_token_details(tokens["access_token"])
    assert user_id == "user-demo"
    assert expires_at > 0

    profile = asyncio.run(_call_profile(base_url, tokens["access_token"]))
    assert profile["points_balance"] == 1000


def test_login_wrong_password_shows_error_and_allows_retry(base_url: str) -> None:
    with httpx.Client() as client:
        client_id = _register_client(client, base_url)
        pending_id, _ = _start_authorization(client, base_url, client_id)

        failed = _submit_login(client, base_url, pending_id, password="bad")
        assert failed.status_code == 401
        assert "用户名或密码错误" in failed.text

        # 失败后 pending 未被消费，可直接重试。
        retry = _submit_login(client, base_url, pending_id)
        assert retry.status_code == 302
        assert "code=" in retry.headers["location"]


def test_refresh_token_rotation(base_url: str) -> None:
    with httpx.Client() as client:
        client_id = _register_client(client, base_url)
        tokens = _run_authorization_flow(client, base_url, client_id)

        rotated = client.post(
            f"{base_url}/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": client_id,
            },
        )
        assert rotated.status_code == 200, rotated.text
        new_tokens = rotated.json()
        assert new_tokens["refresh_token"] != tokens["refresh_token"]

        replay = client.post(
            f"{base_url}/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": client_id,
            },
        )
        assert replay.status_code != 200

    profile = asyncio.run(_call_profile(base_url, new_tokens["access_token"]))
    assert profile["username"] == "demo"


def test_oauth_state_persists_across_restart(tmp_path, server_factory) -> None:
    database_url = f"sqlite:///{tmp_path / 'shop.db'}"
    base_url, server, thread = server_factory(ShopService(database_url=database_url))
    try:
        with httpx.Client() as client:
            client_id = _register_client(client, base_url)
            tokens = _run_authorization_flow(client, base_url, client_id)
    finally:
        server.should_exit = True
        thread.join(timeout=10)

    # 重启后用同一数据库重建服务与 provider，refresh token 与客户端注册仍在。
    provider = ShopOAuthProvider(ShopService(database_url=database_url))

    async def scenario() -> None:
        restored = await provider.get_client(client_id)
        assert restored is not None
        # 与真实 /token 端点一致：先 load 再 exchange。
        loaded = await provider.load_refresh_token(restored, tokens["refresh_token"])
        assert loaded is not None
        result = await provider.exchange_refresh_token(restored, loaded, [])
        verified = await provider.verify_token(result.access_token)
        assert verified is not None
        assert verified.claims["sub"] == "user-demo"

    asyncio.run(scenario())


def test_verify_token_accepts_bearer_and_rejects_garbage(tmp_path) -> None:
    provider = ShopOAuthProvider(ShopService(database_url=f"sqlite:///{tmp_path / 'shop.db'}"))

    async def scenario() -> None:
        valid = await provider.verify_token(create_token("user-demo"))
        assert valid is not None
        assert valid.claims["sub"] == "user-demo"
        assert await provider.verify_token("forged.value") is None

    asyncio.run(scenario())


def test_login_page_rejects_unknown_pending(base_url: str) -> None:
    with httpx.Client() as client:
        page = client.get(f"{base_url}/login", params={"pending_id": "no-such-id"})
        assert page.status_code == 400
        assert "重新发起连接" in page.text
