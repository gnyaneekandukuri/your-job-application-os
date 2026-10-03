"""Request attribution without storing prompts or credentials in the ledger."""
from contextlib import contextmanager
from contextvars import ContextVar

scope = ContextVar('api_usage_scope', default=(None, None))
scan_id = ContextVar('api_scan_id', default=None)
opportunity_id = ContextVar('api_opportunity_id', default=None)


@contextmanager
def opportunity_scope(value):
    token = opportunity_id.set(value)
    try:
        yield
    finally:
        opportunity_id.reset(token)


@contextmanager
def usage_scope(job_id, stage):
    token = scope.set((str(job_id) if job_id else None, stage))
    try:
        yield
    finally:
        scope.reset(token)


def token_cost(model, incoming, outgoing, details):
    # Published standard rates. Unknown models retain the prior conservative estimate.
    if model == 'text-embedding-3-small':
        return incoming * .02 / 1e6, 'standard_rates'
    if model != 'gpt-6-sol':
        return (incoming*2.5+outgoing*10)/1e6, 'unverified_model_estimate'
    read = int(details.get('cached_tokens', 0))
    write = int(details.get('cache_write_tokens', 0))
    if min(read, write) < 0 or read+write > incoming:
        return (incoming*2.5+outgoing*10)/1e6, 'conservative_estimate'
    return ((incoming-read-write)*2+read*.2+write*2.5+outgoing*10)/1e6, 'usage_details_standard_rates'
