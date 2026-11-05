"""Per-request operation measurements; mutable state never crosses request boundaries."""

from contextvars import ContextVar
from functools import wraps
from time import perf_counter

measurements: ContextVar[dict | None] = ContextVar("measurements", default=None)


def provider_operation(fn):
    @wraps(fn)
    def measured(*args, **kwargs):
        stats = measurements.get()
        started = perf_counter()
        try:
            try:
                return fn(*args, **kwargs)
            except Exception as error:
                from curl_cffi.requests.exceptions import (
                    CertificateVerifyError,
                    ConnectionError,
                    RequestException,
                    Timeout,
                )
                from yfinance.exceptions import YFException, YFRateLimitError

                from .errors import DataError

                if isinstance(error, DataError):
                    raise
                if isinstance(error, YFRateLimitError):
                    failure = DataError("provider rate limit reached; retry later", code="PROVIDER_RATE_LIMIT", retryable=True)
                elif isinstance(error, (Timeout, TimeoutError)):
                    failure = DataError("provider request timed out", code="PROVIDER_TIMEOUT", retryable=True)
                elif isinstance(error, CertificateVerifyError):
                    failure = DataError("provider certificate verification failed", code="PROVIDER_TLS_ERROR")
                elif isinstance(error, ConnectionError):
                    failure = DataError("provider connection unavailable", code="PROVIDER_UNAVAILABLE", retryable=True)
                elif isinstance(error, (RequestException, YFException)):
                    failure = DataError("provider returned no usable response", code="DATA_UNAVAILABLE")
                else:
                    raise
                raise failure from None
        finally:
            if stats is not None:
                stats["provider_seconds"] += perf_counter() - started
                stats["provider_operations"] += 1
    return measured
