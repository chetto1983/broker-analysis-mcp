"""Expected failures and sanitized application diagnostics, without numerical imports."""

from urllib.error import HTTPError

from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from pydantic import ValidationError


class Failure(ValueError):
    def __init__(self, message, *, code="DATA_UNAVAILABLE", field=None, retryable=False, details=None):
        super().__init__(message)
        self.code, self.field, self.retryable = code, field, retryable
        self.details = details or []


class DataError(Failure):
    """The source returned nothing usable for the request."""


class SpecError(Failure):
    """An indicator, expression or condition the user supplied is not valid."""

    def __init__(self, message, *, public_message=None, **kwargs):
        super().__init__(message, **({"code": "INVALID_INPUT"} | kwargs))
        self.public_message = public_message or "invalid analysis specification; check the indicator catalog and allowed expression syntax"


def validation_issues(error: ValidationError) -> list[dict]:
    issues = []
    for item in error.errors(include_input=False, include_url=False):
        constraints = {key: value for key, value in (item.get("ctx") or {}).items()
                       if key in {"gt", "ge", "lt", "le", "min_length", "max_length", "expected", "pattern"}
                       and isinstance(value, (str, int, float))}
        issues.append({"field": ".".join(map(str, item["loc"])) or None, "type": item["type"],
                       "message": item["msg"] if item["type"] != "value_error" else "invalid value",
                       "constraints": constraints})
    return issues


def error_payload(error: Exception) -> dict:
    current, seen = error, set()
    failure = None
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, HTTPError):
            return {"code": "DELIVERY_FAILED", "field": None, "message": f"webhook answered HTTP {current.code}",
                    "retryable": current.code == 429 or current.code >= 500, "details": []}
        if isinstance(current, ValidationError):
            issues = validation_issues(current)
            return {"code": "INVALID_INPUT", "field": issues[0]["field"] if len(issues) == 1 else None,
                    "message": "request violates input constraints", "retryable": False, "details": issues}
        if isinstance(current, Failure):
            failure = current
        current = current.__cause__
    if failure is not None:
        return {"code": failure.code, "field": failure.field,
                "message": failure.public_message if isinstance(failure, SpecError) else str(failure),
                "retryable": failure.retryable, "details": failure.details}
    if isinstance(error, ToolError) and not isinstance(error, UnexpectedToolError):
        return {"code": "INVALID_INPUT", "field": None, "message": "tool or arguments are not supported",
                "retryable": False, "details": []}
    return {"code": "INTERNAL_ERROR", "field": None, "message": "analysis failed; check the request identifier in server logs",
            "retryable": False, "details": []}
