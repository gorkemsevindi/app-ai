from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Domain error with a stable machine-readable code (clients localise by code)."""

    def __init__(self, code: str, message: str, status: int = 400, **extra):
        self.code, self.message, self.status, self.extra = code, message, status, extra
        super().__init__(message)


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message, **exc.extra}}
    )
