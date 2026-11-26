"""RFC 9457 problem+json errors. Raise ApiError anywhere; handlers are installed in app.main."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError, StatementError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("docintel.errors")

PROBLEM_JSON = "application/problem+json"
ERROR_BASE = "https://docs.docintel.example.com/errors/"

_TITLES = {
    400: "Bad request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not found",
    405: "Method not allowed",
    409: "Conflict",
    412: "Precondition failed",
    413: "Payload too large",
    415: "Unsupported media type",
    422: "Validation failed",
    428: "Precondition required",
    429: "Too many requests",
    500: "Internal server error",
    502: "Bad gateway",
    503: "Service unavailable",
}


class ApiError(Exception):
    """An error rendered as application/problem+json.

    `extra` is merged into the body (e.g. issues=[...], usages=[...]).
    """

    def __init__(
        self,
        status: int,
        code: str,
        detail: str | None = None,
        *,
        title: str | None = None,
        headers: dict[str, str] | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(detail or code)
        self.status = status
        self.code = code
        self.detail = detail
        self.title = title or _TITLES.get(status, "Error")
        self.headers = headers or {}
        self.extra = extra


# Convenience constructors -------------------------------------------------------------------------

def not_found(resource: str = "Resource", rid: str | None = None) -> ApiError:
    return ApiError(404, "not_found", f"{resource} {rid} was not found" if rid else f"{resource} was not found")


def conflict(code: str, detail: str, **extra: Any) -> ApiError:
    return ApiError(409, code, detail, **extra)


def forbidden(detail: str = "You do not have permission to do this") -> ApiError:
    return ApiError(403, "forbidden", detail)


def unauthorized(detail: str = "Missing or invalid credentials") -> ApiError:
    return ApiError(401, "unauthorized", detail, headers={"WWW-Authenticate": 'Bearer realm="docintel"'})


def bad_request(code: str, detail: str, **extra: Any) -> ApiError:
    return ApiError(400, code, detail, **extra)


def unprocessable(code: str, detail: str, **extra: Any) -> ApiError:
    return ApiError(422, code, detail, **extra)


# Rendering ----------------------------------------------------------------------------------------

def problem_body(status: int, code: str, title: str, detail: str | None, instance: str | None, **extra: Any) -> dict:
    body: dict[str, Any] = {
        "type": ERROR_BASE + code.replace("_", "-"),
        "title": title,
        "status": status,
        "code": code,
    }
    if detail:
        body["detail"] = detail
    if instance:
        body["instance"] = instance
    body.update({k: v for k, v in extra.items() if v is not None})
    return body


def problem_response(status: int, code: str, detail: str | None = None, *, request: Request | None = None,
                     title: str | None = None, headers: dict[str, str] | None = None, **extra: Any) -> JSONResponse:
    body = problem_body(status, code, title or _TITLES.get(status, "Error"), detail,
                        request.url.path if request else None, **extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):  # noqa: ANN202
        return problem_response(exc.status, exc.code, exc.detail, request=request, title=exc.title,
                                headers=exc.headers, **exc.extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):  # noqa: ANN202
        if any(e.get("type") == "json_invalid" for e in exc.errors()):
            # Malformed JSON is a syntax error: same 400 bad_request in every router (QA D-05).
            return problem_response(400, "bad_request", "The request body is not valid JSON", request=request)
        errors = []
        for e in exc.errors():
            loc = ".".join(str(p) for p in e.get("loc", ()) if p != "body")
            errors.append({"field": loc, "message": e.get("msg", "invalid")})
        detail = "; ".join(f"{x['field']}: {x['message']}" for x in errors[:5]) or "Invalid request"
        return problem_response(422, "validation", detail, request=request, errors=errors)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):  # noqa: ANN202
        code = {400: "bad_request", 404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return problem_response(exc.status_code, code, str(exc.detail) if exc.detail else None, request=request,
                                headers=getattr(exc, "headers", None))

    @app.exception_handler(StatementError)
    async def _db_value(request: Request, exc: StatementError):  # noqa: ANN202
        # Values the database cannot store (too long for the column, integer out of range, NUL characters on
        # PostgreSQL) are client errors, not 500s. The request's session is rolled back by get_db.
        orig = getattr(exc, "orig", None)
        if isinstance(exc, DataError) or isinstance(orig, ValueError):
            return problem_response(422, "validation", "A value is too long, out of range or contains characters "
                                    "that cannot be stored", request=request)
        log.exception("Database error on %s %s", request.method, request.url.path)
        return problem_response(500, "internal_error", "An unexpected error occurred", request=request)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):  # noqa: ANN202
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return problem_response(500, "internal_error", "An unexpected error occurred", request=request)
