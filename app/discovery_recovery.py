"""Free collection recovery and bounded scan lifetimes. No provider calls."""
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from threading import Event

source_window = ContextVar('source_window', default=None)
source_metrics = ContextVar('source_metrics', default=None)
SCAN_SECONDS = 2 * 3600


class SourceDeferred(Exception):
    source_state = 'deferred'

    def __init__(self, message):
        super().__init__(message)
        self.discovery_diagnostic = dict(kind='source_backoff', reason=message, attempts=0)


def lookback(board, last_success, current=None):
    current = current or datetime.now(timezone.utc)
    baseline = 7 if board in ('a16z', 'sequoia', 'lightspeed') else 1
    gap = (current - datetime.fromisoformat(last_success)).total_seconds() / 86400 if last_success else 7
    # An hour of overlap protects rounded source timestamps and fetch boundaries.
    required = max(baseline, gap + 1 / 24 if last_success else 7)
    days = next((n for n in (1, 7, 30) if n >= required), 30)
    reason = 'Normal source window' if days == baseline else 'Catch up since last successful fetch' if last_success else 'No successful fetch recorded; conservative initial catch-up'
    if board in ('yc', 'hn'):
        return dict(days=31 if board == 'hn' else None, label='current monthly thread' if board == 'hn' else 'full directory',
                    reason='Source has no daily filter', last_success=last_success, capped=False)
    return dict(days=days, label='All (bounded pagination)' if board == 'lenny' and days > 1 else f'past {days} days',
                reason=reason, last_success=last_success, capped=required > 30)


class ScanDeadline:
    """Cooperative cancellation; never release an active collector's scan lock."""
    def __init__(self, stop=None, seconds=SCAN_SECONDS, clock=None):
        self.stop = stop or Event()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.deadline = self.clock() + timedelta(seconds=seconds)

    @property
    def expired(self):
        return self.clock() >= self.deadline

    def is_set(self):
        return self.stop.is_set() or self.expired

    def wait(self, seconds):
        remaining = max(0, (self.deadline - self.clock()).total_seconds())
        return self.stop.wait(min(seconds, remaining)) or self.expired
