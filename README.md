# 积分商城 MCP

一个基于 FastAPI 和 FastMCP 的积分商城示例，同时提供 HTTP API 与 Streamable HTTP MCP。两种协议共用同一业务服务，支持商品浏览、购物车、积分下单和订单查询。

> 默认使用项目根目录的 SQLite 数据库文件 `shop.db` 持久化商城状态。设置 `SHOP_DATABASE_URL=memory://` 可切换为进程内存储；该模式下服务重启会重置购物车、订单、库存变更和幂等记录。

## 功能

- HTTP API：认证、商品和分类浏览、购物车、订单预览与创建、订单查询。
- MCP 工具：商品搜索、个人资料和购物车、购物车管理、预览确认下单、订单查询。
- MCP 支持 OAuth 2.1 浏览器登录：客户端只需配置服务器 URL，首次连接自动打开浏览器完成登录，令牌自动续期；同时保留 Bearer Token 双轨认证。演示账号为 `demo` / `demo-password`，初始积分 1000。
- HTTP 下单使用 `Idempotency-Key` 防止重复创建订单。
- MCP 下单要求先预览，再用一次性的 `confirmation_token` 确认。

## 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

## 安装与启动

在项目根目录执行：

```powershell
uv sync
uv run test-shop-mcp
```

服务默认运行于 `http://127.0.0.1:8000`：

- 健康检查：`GET /health`
- OpenAPI/Swagger：`http://127.0.0.1:8000/docs`
- HTTP API 前缀：`/api/v1`
- MCP Streamable HTTP 端点：`http://127.0.0.1:8000/mcp`

开发时也可通过 Uvicorn 自动重载：

```powershell
uv run uvicorn shop.api.main:app --host 127.0.0.1 --port 8000 --reload
```

## 配置

复制 [`.env.example`](.env.example) 为 `.env` 并填写实际值；应用启动时会自动从项目根目录加载该文件。系统环境变量优先于 `.env` 中的同名配置。PowerShell 中也可在启动前临时设置：

```powershell
$env:SHOP_SECRET_KEY = "请替换为足够长的随机密钥"
$env:SHOP_TOKEN_EXPIRE_MINUTES = "60"
uv run test-shop-mcp
```

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SHOP_SECRET_KEY` | `development-secret-change-me` | Token 和确认令牌签名密钥；生产环境必须使用随机强密钥。 |
| `SHOP_TOKEN_EXPIRE_MINUTES` | `60` | 访问令牌有效期（分钟）。 |
| `SHOP_CONFIRMATION_EXPIRE_SECONDS` | `300` | MCP 确认令牌有效期（秒）。 |
| `SHOP_DATABASE_URL` | `sqlite:///./shop.db` | SQLite 数据库地址；设为 `memory://` 可关闭持久化。 |
| `SHOP_BASE_URL` | `http://127.0.0.1:8000` | 对外暴露的基础地址，用于 OAuth 发现元数据与登录页回跳；经反代或远程访问时设为实际地址。 |

生产环境应经 HTTPS 反向代理暴露服务，并限制 MCP 端点的可信访问来源；不要提交真实密钥。

## HTTP API 快速体验

先登录并保存访问令牌：

```powershell
$login = Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/auth/login" `
  -ContentType "application/json" `
  -Body '{"username":"demo","password":"demo-password"}'
$token = $login.access_token
$headers = @{ Authorization = "Bearer $token" }
```

查询商品并加入购物车：

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/products"

Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/cart/items" `
  -Headers $headers -ContentType "application/json" `
  -Body '{"product_id":"product-mug","quantity":1}'
```

预览并创建订单。创建 HTTP 订单必须带有 `Idempotency-Key`；同一用户再次提交同一个键，会返回原订单。

```powershell
Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/orders/preview" `
  -Headers $headers -ContentType "application/json" `
  -Body '{"shipping_address":"上海市示例路 1 号"}'

Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/api/v1/orders" `
  -Headers @{ Authorization = "Bearer $token"; "Idempotency-Key" = "order-demo-001" } `
  -ContentType "application/json" `
  -Body '{"shipping_address":"上海市示例路 1 号"}'
```

| 方法 | 路径 | 认证 | 用途 |
| --- | --- | --- | --- |
| `POST` | `/api/v1/auth/login` | 否 | 获取访问令牌。 |
| `GET` | `/api/v1/categories`、`/api/v1/products` | 否 | 查询分类与商品。 |
| `GET/POST/PATCH/DELETE` | `/api/v1/cart`、`/api/v1/cart/items` | 是 | 查询和管理购物车。 |
| `POST` | `/api/v1/orders/preview` | 是 | 校验和预览订单。 |
| `POST` | `/api/v1/orders` | 是 | 创建订单，必须包含 `Idempotency-Key`。 |
| `GET` | `/api/v1/orders`、`/api/v1/orders/{order_id}` | 是 | 查询本人的订单。 |

## MCP 使用

MCP 端点为 `http://127.0.0.1:8000/mcp`，支持两种接入方式。

### 方式一：OAuth 2.1 浏览器登录（推荐）

MCP 客户端（如 Claude Code、Cursor 等）只需添加一个远程服务器 URL：

```text
http://127.0.0.1:8000/mcp
```

首次连接时，客户端会自动打开浏览器进入积分商城登录页，使用商城账号（演示账号 `demo` / `demo-password`）登录并授权，随后客户端自动获得访问令牌；令牌过期后客户端凭刷新令牌自动续期，全程无需理解或手动管理 token。

### 方式二：Bearer Token（脚本与 HTTP 场景）

先用 HTTP API 登录获取令牌，再手工配置请求头：

```text
Authorization: Bearer <通过 /api/v1/auth/login 获取的 access_token>
```
示例：
```text
"商城-mcp": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_ACTUAL_ACCESS_TOKEN"
      }
    }
```

两种方式签发的令牌同源同构，均通过 `/mcp` 与 HTTP API 的统一验证。

可用工具：`search_products`、`get_product`、`get_my_profile`、`get_cart`、`add_to_cart`、`update_cart_item`、`preview_checkout`、`place_order`、`get_my_orders`。

下单必须遵循以下顺序：调用 `preview_checkout`，向用户展示商品、数量、积分总额和收货地址；获得用户明确确认后，再使用返回的 `confirmation_token` 调用 `place_order`。确认令牌只能使用一次；购物车、收货地址或金额变化，以及令牌过期时，都需要重新预览。

## 开发与验证

```powershell
uv run python -c "import shop"
uv run pytest
uv build
```

当前测试覆盖订单幂等、积分不足时的库存保护、购物车归属，以及 MCP 确认令牌的一次性和购物车变更校验。
