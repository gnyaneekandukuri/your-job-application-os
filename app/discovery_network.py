"""Bounded recovery for free board reads; never used for paid model calls."""
from threading import Event
from urllib.parse import urlsplit

import httpx


RETRY_DELAYS = (10, 30, 60)


def diagnose(exc, url='', attempts=1):
    response = getattr(exc, 'response', None)
    status = response.status_code if response is not None else None
    if status == 403:
        reason = 'Access denied by the board. The response does not establish why.'
    elif status == 429:
        reason = 'The board limited requests. No immediate retry; wait for a later scan.'
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
            'causes': causes}


class BoardClient(httpx.Client):
    def __init__(self, *, stop=None, **kwargs):
        super().__init__(**kwargs)
        self.stop = stop if stop is not None else Event()
        self.recovered_requests = 0


def recover(client, url, operation):
    for attempt in range(1, len(RETRY_DELAYS) + 2):
        if client.stop.is_set():
            raise RuntimeError('Discovery stopped')
        try:
            result = operation()
            if attempt > 1:
                client.recovered_requests += 1
            return result
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            status = getattr(getattr(exc, 'response', None), 'status_code', None)
            retryable = isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)) or status in (502, 503, 504)
            exc.discovery_diagnostic = diagnose(exc, url, attempt)
            if not retryable or attempt > len(RETRY_DELAYS):
                raise
            # A server-requested delay longer than our retry window is respected
            # by leaving this attempt failed rather than sending an early retry.
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.headers.get('retry-after'):
                raise
            if client.stop.wait(RETRY_DELAYS[attempt - 1]):
                raise
