"""存储后端测试：Redis 降级用例始终运行；MySQL/真实 Redis 通过环境变量驱动，缺省跳过。

- SHOP_TEST_MYSQL_URL 例：mysql+pymysql://root:pass@127.0.0.1:3306/shop_test?charset=utf8mb4
- SHOP_TEST_REDIS_URL 例：redis://127.0.0.1:6379/0
"""

import os
from importlib.util import find_spec
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from shop.api.service import ShopService
from shop.storage.cache import NullCache, RedisCache, build_cache_from_env
from shop.storage.models import Base


def test_service_degrades_gracefully_when_redis_is_down(tmp_path: Path) -> None:
    """Redis 拒连（或客户端未安装）时静默降级，业务流程与正确性不受影响。"""
    os.environ["SHOP_REDIS_URL"] = "redis://127.0.0.1:59999/0"
    try:
        service = ShopService(database_url=f"sqlite:///{tmp_path / 'shop.db'}")
    finally:
        os.environ.pop("SHOP_REDIS_URL", None)

    if find_spec("redis") is not None:
        assert isinstance(service.cache, RedisCache)
        # 缓存操作全部降级：读为空、写不抛错、NX 语义放行
        assert service.cache.get_json("shop:cache:product:product-mug") is None
        service.cache.set_json("shop:cache:product:product-mug", {"x": 1}, 60)
        assert service.cache.set_if_absent("shop:lock:test", "1", 5) is True
        service.cache.delete("shop:lock:test")
        service.cache.delete_prefix("shop:cache:product:list:")
    else:
        # 未安装 redis extra：初始化失败降级为 NullCache，同样是可接受的降级路径
        assert isinstance(service.cache, NullCache)

    # 完整下单流程照常工作（Redis 锁不可用时回退进程内 RLock）
    user = service.user_by_name("demo")
    service.add_cart_item(user.id, "product-mug", 2)
    key = f"degraded-{uuid4()}"
    first = service.create_order(user.id, "Shanghai", key)
    repeated = service.create_order(user.id, "Shanghai", key)
    assert repeated == first
    assert first["points_total"] == 200
    assert service.user_by_name("demo").points_balance == 800
    assert service.get_product("product-mug").stock == 18


def test_build_cache_from_env_defaults_to_null_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHOP_REDIS_URL", raising=False)
    assert isinstance(build_cache_from_env(), NullCache)


REDIS_URL = os.getenv("SHOP_TEST_REDIS_URL", "")
MYSQL_URL = os.getenv("SHOP_TEST_MYSQL_URL", "")


@pytest.mark.skipif(not REDIS_URL, reason="需要设置 SHOP_TEST_REDIS_URL 才运行真实 Redis 集成测试")
def test_redis_cache_engages_and_invalidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHOP_REDIS_URL", REDIS_URL)
    service = ShopService(database_url=f"sqlite:///{tmp_path / 'shop.db'}")
    assert isinstance(service.cache, RedisCache)

    service.get_product("product-mug")
    client = service.cache._client
    assert client.get("shop:cache:product:product-mug") is not None

    service.update_product("product-mug", {"name": "改名马克杯"})
    assert client.get("shop:cache:product:product-mug") is None
    assert service.get_product("product-mug").name == "改名马克杯"


@pytest.mark.skipif(not MYSQL_URL, reason="需要设置 SHOP_TEST_MYSQL_URL 才运行 MySQL 集成测试")
def test_mysql_backend_full_flow() -> None:
    engine = create_engine(MYSQL_URL)
    Base.metadata.drop_all(engine)
    engine.dispose()

    service = ShopService(database_url=MYSQL_URL)

    # 种子数据
    user = service.user_by_name("demo")

    # 购物车 UPSERT 原子累加（MySQL 方言分支）
    service.add_cart_item(user.id, "product-mug", 1)
    service.add_cart_item(user.id, "product-mug", 2)
    item = service.cart(user.id)[0]
    assert item.quantity == 3

    # 幂等下单 + 价格快照
    key = f"mysql-{uuid4()}"
    first = service.create_order(user.id, "Shanghai", key)
    repeated = service.create_order(user.id, "Shanghai", key)
    assert repeated == first
    assert first["points_total"] == 200
    assert first["items"][0]["name"] == "商城马克杯"
    assert first["items"][0]["points_price"] == 100
    assert service.user_by_name("demo").points_balance == 800
    assert service.get_product("product-mug").stock == 17

    # 管理端视图
    items, total = service.list_orders_admin(1, 20)
    assert total == 1
    assert items[0]["id"] == first["id"]
