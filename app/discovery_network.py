"""Bounded recovery for free board reads; never used for paid model calls."""
from threading import Event
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from random import uniform
from urllib.parse import urlsplit

import httpx


RETRY_DELAYS = (10, 20, 40)
MAX_RETRY_WAIT = 120


def retry_after(response):
    value = response.headers.get('retry-after') if response is not None else None
    if not value:
        return None
    try:
        return max(0, float(value))
    except ValueError:
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return MAX_RETRY_WAIT + 1


def transient(exc):
    status = getattr(getattr(exc, 'response', None), 'status_code', None) or getattr(exc, 'discovery_diagnostic', {}).get('http_status')
    kind = getattr(exc, 'discovery_diagnostic', {}).get('kind', '')
    return (isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)) or
            status == 429 or status is not None and 500 <= status <= 599 or
            kind in ('TimeoutError', 'ConnectError', 'ReadTimeout'))


class SourceResponseError(Exception):
    """Safe HTTP diagnostics crossing a browser subprocess boundary."""
    def __init__(self, status, host, path, headers=None):
        super().__init__(f'Source returned HTTP {status}.')
        self.source_state = 'blocked' if status in (401,403,999) else 'error'
        self.discovery_diagnostic = dict(kind='HTTPStatusError', http_status=status, host=host,
            path=path, attempts=1, causes=[], reason=str(self),
            retry_after_seconds=retry_after(httpx.Response(status, headers=headers or {})))


def diagnose(exc, url='', attempts=1):
    response = getattr(exc, 'response', None)
    status = response.status_code if response is not None else None
    if status == 403:
        reason = 'Access denied by the board. The response does not establish why.'
    elif status == 429:
        reason = 'The board limited requests. Bounded backoff applies; remaining coverage waits for a later scan.'
    elif isinstance(exc, httpx.TimeoutException):
        reason = 'The connection or response timed out.'
    elif isinstance(exc, httpx.NetworkError):
        reason = 'A network connection failed.'
    elif status:
        reason = f'The board returned HTTP {status}.'
    else:
        reason = 'The board response could not be processed; the collector needs review.'
    request = getattr(exc, 'request', None) if isinstance(exc, httpx.RequestError) else None
    target = urlsplit(url or (str(request.url) if request else ''))
    causes, current = [], exc
    while current is not None and len(causes) < 4:
        causes.append(type(current).__name__)
        current = current.__cause__
    # No response bodies, query strings, cookies, tokens, or raw exception messages.
    return {'reason': reason, 'kind': type(exc).__name__, 'http_status': status,
            'host': target.hostname or '', 'path': target.path, 'attempts': attempts,
            'causes': causes, 'retry_after_seconds': retry_after(response)}


class BoardClient(httpx.Client):
    def __init__(self, *, stop=None, **kwargs):
        super().__init__(**kwargs)
        self.stop = stop if stop is not None else Event()
        self.recovered_requests = 0


def recover(client, url, operation):
    from .discovery_recovery import source_metrics
    for attempt in range(1, len(RETRY_DELAYS) + 2):
        if client.stop.is_set():
            raise RuntimeError('Discovery stopped')
        try:
            metrics = source_metrics.get()
            if metrics is not None:
                metrics['attempts'] = max(metrics.get('attempts', 0), attempt)
                metrics['requests'] = metrics.get('requests', 0) + 1
            result = operation()
            if attempt > 1:
                client.recovered_requests += 1
            return result
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            exc.discovery_diagnostic = diagnose(exc, url, attempt)
            if not transient(exc) or attempt > len(RETRY_DELAYS):
                raise
            # A server-requested delay longer than our retry window is respected
            # by leaving this attempt failed rather than sending an early retry.
            requested = retry_after(getattr(exc, 'response', None))
            if requested is not None and requested > MAX_RETRY_WAIT:
                raise
            delay = max(requested or 0, RETRY_DELAYS[attempt - 1] * uniform(1, 1.25))
            if client.stop.wait(delay):
                raise
