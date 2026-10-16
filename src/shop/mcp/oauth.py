"""积分商城 MCP 的 OAuth 2.1 浏览器登录授权服务。

基于 FastMCP 的 OAuthProvider 实现授权服务器：MCP 客户端只需配置服务器 URL，
首次连接时自动打开浏览器完成商城账号登录，之后由 refresh token 自动续期。

签发的 access token 直接复用商城 HMAC 令牌（create_token），因此对 OAuth
客户端与手工配置的 Bearer 令牌使用同一套验证逻辑，实现双轨兼容。
"""

from __future__ import annotations

import html
import os
import secrets
import time

from fastmcp.server.auth.auth import (
    AccessToken,
    ClientRegistrationOptions,
    OAuthProvider,
    RevocationOptions,
)
from fastmcp.server.dependencies import get_http_request
from fastmcp.utilities.ui import (
    INFO_BOX_STYLES,
    create_page,
    create_secure_html_response,
)
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.routing import Route

from shop.api.errors import ShopError
from shop.api.security import create_token, decode_token_details, token_expire_seconds
from shop.api.service import ShopService

_LOGIN_PATH = "/login"
_AUTH_CODE_TTL_SECONDS = 300
_PENDING_TTL_SECONDS = 600


def _login_form_html(pending_id: str, error: str | None = None) -> str:
    error_html = (
        f'<div class="info-box error"><p>{html.escape(error)}</p></div>' if error else ""
    )
    content = f"""
    <div class="container">
        <h1>积分商城登录</h1>
        <p>您的 MCP 客户端正在请求访问积分商城。请使用商城账号登录以完成授权，
        登录后客户端将自动获得并续期访问凭证，无需手动配置。</p>
        {error_html}
        <form method="post" action="/login">
            <input type="hidden" name="pending_id" value="{html.escape(pending_id)}">
            <label>用户名
                <input type="text" name="username" required autofocus>
            </label>
            <label>密码
                <input type="password" name="password" required>
            </label>
            <button type="submit">登录并授权</button>
        </form>
    </div>
    """
    styles = INFO_BOX_STYLES + """
        form { display: flex; flex-direction: column; gap: 14px; max-width: 320px;
               margin: 16px auto 0; }
        label { display: flex; flex-direction: column; gap: 6px; text-align: left; }
        input { padding: 8px 10px; border: 1px solid #d1d5db; border-radius: 6px;
                font-size: 15px; }
        button { padding: 10px; background: #1f2937; color: #fff; border: none;
                 border-radius: 6px; cursor: pointer; font-size: 15px; }
    """
    return create_page(content, title="积分商城登录", additional_styles=styles)


def _error_page(message: str) -> str:
    content = f"""
    <div class="container">
        <h1>无法完成授权</h1>
        <div class="info-box error"><p>{html.escape(message)}</p></div>
    </div>
    """
    return create_page(content, title="授权失败", additional_styles=INFO_BOX_STYLES)


class ShopOAuthProvider(OAuthProvider):
    """基于商城账号体系（ShopService.authenticate）的 OAuth 2.1 授权服务器。"""

    def __init__(self, service: ShopService, base_url: str | None = None) -> None:
        resolved = base_url or os.getenv("SHOP_BASE_URL", "http://127.0.0.1:8000")
        super().__init__(
            base_url=resolved,
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        )
        self._service = service
        # 与 service 共享同一 dict，persist() 即可随快照落盘。
        self._clients: dict[str, dict] = service.oauth_clients
        self._refresh_tokens: dict[str, dict] = service.oauth_refresh_tokens
        # 授权码与待登录请求为短时一次性数据，进程内即可。
        self._auth_codes: dict[str, dict] = {}
        self._pending: dict[str, dict] = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        record = self._clients.get(client_id)
        if record is None:
            return None
        return OAuthClientInformationFull.model_validate(record)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id is None:
            raise ValueError("client_id 不能为空")
        self._clients[client_info.client_id] = client_info.model_dump(mode="json")
        self._service.persist()

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """先重定向到商城登录页，登录成功后再带授权码回跳客户端。

        登录页地址优先取当前请求的 scheme/host（本地开发换端口也能工作），
        取不到时回退到配置的 base_url。
        """
        pending_id = secrets.token_urlsafe(16)
        self._pending[pending_id] = {
            "expires_at": time.time() + _PENDING_TTL_SECONDS,
            "client": client.model_dump(mode="json"),
            "params": params.model_dump(mode="json"),
        }
        base = self._request_base_url() or str(self.base_url).rstrip("/")
        return f"{base}{_LOGIN_PATH}?pending_id={pending_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        record = self._auth_codes.get(authorization_code)
        if record is None:
            return None
        if record["expires_at"] < time.time():
            del self._auth_codes[authorization_code]
            return None
        if record["client_id"] != client.client_id:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=record["scopes"],
            expires_at=record["expires_at"],
            client_id=record["client_id"],
            code_challenge=record["code_challenge"],
            redirect_uri=record["redirect_uri"],
            redirect_uri_provided_explicitly=record["redirect_uri_provided_explicitly"],
            resource=record.get("resource"),
            subject=record["user_id"],
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self._auth_codes.pop(authorization_code.code, None)
        if not authorization_code.subject:
            raise TokenError("invalid_grant", "授权码缺少用户信息")
        return self._issue_tokens(authorization_code.subject, client.client_id)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        record = self._refresh_tokens.get(refresh_token)
        if record is None or record["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=record["client_id"],
            scopes=[],
            subject=record["user_id"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if not refresh_token.subject:
            raise TokenError("invalid_grant", "刷新令牌缺少用户信息")
        # 轮换：旧刷新令牌立即作废，客户端改用新令牌对。
        self._refresh_tokens.pop(refresh_token.token, None)
        return self._issue_tokens(refresh_token.subject, client.client_id)

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, RefreshToken) and self._refresh_tokens.pop(token.token, None) is not None:
            self._service.persist()

    async def verify_token(self, token: str) -> AccessToken | None:
        """统一验证入口：OAuth 签发令牌与手工配置的 Bearer HMAC 令牌同源同构。"""
        try:
            user_id, expires_at = decode_token_details(token)
        except ShopError:
            return None
        return AccessToken(
            token=token,
            client_id="bearer",
            scopes=[],
            expires_at=expires_at,
            claims={"sub": user_id},
        )

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        routes = list(super().get_routes(mcp_path))
        routes.append(Route(_LOGIN_PATH, self._login_page, methods=["GET"]))
        routes.append(Route(_LOGIN_PATH, self._login_submit, methods=["POST"]))
        return routes

    async def _login_page(self, request: Request) -> Response:
        pending_id = request.query_params.get("pending_id")
        if self._get_valid_pending(pending_id) is None:
            return create_secure_html_response(
                _error_page("登录链接无效或已过期，请在 MCP 客户端中重新发起连接。"),
                status_code=400,
            )
        return create_secure_html_response(_login_form_html(pending_id or ""))

    async def _login_submit(self, request: Request) -> Response:
        form = await request.form()
        pending_id = str(form.get("pending_id", ""))
        pending = self._get_valid_pending(pending_id)
        if pending is None:
            return create_secure_html_response(
                _error_page("登录链接无效或已过期，请在 MCP 客户端中重新发起连接。"),
                status_code=400,
            )
        try:
            user = self._service.authenticate(
                str(form.get("username", "")), str(form.get("password", ""))
            )
        except ShopError:
            return create_secure_html_response(
                _login_form_html(pending_id, error="用户名或密码错误"), status_code=401
            )
        # 登录成功才消费 pending，失败时允许用户直接重试。
        del self._pending[pending_id]
        params = pending["params"]
        code = secrets.token_urlsafe(32)
        self._auth_codes[code] = {
            "user_id": user.id,
            "client_id": pending["client"]["client_id"],
            "scopes": params.get("scopes") or [],
            "expires_at": time.time() + _AUTH_CODE_TTL_SECONDS,
            "code_challenge": params["code_challenge"],
            "redirect_uri": params["redirect_uri"],
            "redirect_uri_provided_explicitly": params["redirect_uri_provided_explicitly"],
            "resource": params.get("resource"),
        }
        redirect = construct_redirect_uri(
            params["redirect_uri"], code=code, state=params.get("state")
        )
        return RedirectResponse(redirect, status_code=302)

    def _request_base_url(self) -> str | None:
        """从当前 /authorize 请求推导 scheme://host；无请求上下文时返回 None。"""
        try:
            request = get_http_request()
        except Exception:
            return None
        host = request.headers.get("host") or request.url.netloc
        if not host:
            return None
        return f"{request.url.scheme or 'http'}://{host}"

    def _get_valid_pending(self, pending_id: str | None) -> dict | None:
        if not pending_id:
            return None
        pending = self._pending.get(pending_id)
        if pending is None:
            return None
        if pending["expires_at"] < time.time():
            del self._pending[pending_id]
            return None
        return pending

    def _issue_tokens(self, user_id: str, client_id: str) -> OAuthToken:
        refresh = f"rt_{secrets.token_urlsafe(32)}"
        self._refresh_tokens[refresh] = {
            "user_id": user_id,
            "client_id": client_id,
            "created_at": time.time(),
        }
        self._service.persist()
        return OAuthToken(
            access_token=create_token(user_id),
            token_type="Bearer",
            expires_in=token_expire_seconds(),
            refresh_token=refresh,
            scope="",
        )
