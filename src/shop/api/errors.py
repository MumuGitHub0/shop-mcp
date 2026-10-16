from __future__ import annotations

from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse


class ShopError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


async def shop_error_handler(_: Request, exc: ShopError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "request_id": str(uuid4())},
    )


async def validation_error_handler(request: Request, _: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "code": "validation_error",
            "message": "请求参数无效",
            "request_id": request.headers.get("X-Request-ID", str(uuid4())),
        },
    )
