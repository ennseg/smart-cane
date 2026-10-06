import logging
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status_code: int, error: str, detail: Any = None) -> None:
        super().__init__(error)
        self.status_code = status_code
        self.error = error
        self.detail = detail


async def api_error_handler(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, ApiError):
        raise error
    client = request.client.host if request.client else "?"
    logger.warning(
        "Отклонён запрос %s %s от %s: %s %s (%s)",
        request.method,
        request.url.path,
        client,
        error.status_code,
        error.error,
        error.detail,
    )
    return JSONResponse(
        status_code=error.status_code,
        content={"error": error.error, "detail": error.detail},
    )
