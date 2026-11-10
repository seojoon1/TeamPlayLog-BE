from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppError(Exception):
    """{code, message} 형식으로 돌려줄 오류. message는 앱에 그대로 보여 줄 한국어 문장."""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message


def not_found(message: str = "대상을 찾을 수 없습니다.") -> AppError:
    return AppError(404, "NOT_FOUND", message)


def forbidden(message: str = "권한이 없습니다.") -> AppError:
    return AppError(403, "FORBIDDEN", message)


def conflict(code: str, message: str) -> AppError:
    return AppError(409, code, message)


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.message})


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """입력값 오류도 {code, message} 형식으로. 어느 칸이 틀렸는지는 fields로 알려 준다."""
    fields = []
    for e in exc.errors():
        loc = [str(p) for p in e.get("loc", ()) if p not in ("body", "query", "path")]
        fields.append({"field": ".".join(loc) or None, "reason": e.get("msg", "")})
    return JSONResponse(
        status_code=422,
        content={"code": "VALIDATION_ERROR", "message": "입력한 값을 다시 확인해 주세요.", "fields": fields},
    )


async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    messages = {404: "대상을 찾을 수 없습니다.", 405: "허용되지 않는 요청 방식입니다."}
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": f"HTTP_{exc.status_code}", "message": messages.get(exc.status_code, str(exc.detail))},
    )
