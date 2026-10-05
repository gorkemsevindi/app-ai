from fastapi import HTTPException


class ApiError(HTTPException):
    """Errors carry a stable machine-readable `code` the mobile app maps to localized copy."""

    def __init__(self, status: int, code: str, message: str = "", extra: dict | None = None):
        super().__init__(status_code=status, detail={"code": code, "message": message or code, **(extra or {})})
        self.code = code


def not_found(what: str = "resource") -> ApiError:
    # Same response for "missing" and "belongs to someone else" -> no IDOR oracle.
    return ApiError(404, "not_found", f"{what} not found")
