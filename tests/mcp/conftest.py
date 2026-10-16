import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn

from shop.api.main import create_app
from shop.api.service import ShopService


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_server(service: ShopService) -> tuple[str, uvicorn.Server, threading.Thread]:
    """在空闲端口启动真实 uvicorn 服务，返回 (base_url, server, thread)。

    端口先选定再建应用，保证 OAuth 发现元数据与登录重定向使用真实地址。
    """
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    app = create_app(service, base_url=base_url)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "测试服务器未能启动"
    return base_url, server, thread


@pytest.fixture()
def base_url(tmp_path) -> Iterator[str]:
    service = ShopService(database_url=f"sqlite:///{tmp_path / 'shop.db'}")
    url, server, thread = start_server(service)
    yield url
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture()
def server_factory():
    """按需启动自定义服务的测试服务器；调用方负责停止返回的 server。"""
    return start_server
