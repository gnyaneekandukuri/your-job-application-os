"""Memoize shared read-only inputs for one listing response, never across requests."""
from contextvars import ContextVar
from functools import wraps

_snapshot = ContextVar('read_snapshot', default=None)


def read_snapshot(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if _snapshot.get() is not None:
            return fn(*args, **kwargs)
        token = _snapshot.set({})
        try:
            return fn(*args, **kwargs)
        finally:
            _snapshot.reset(token)
    return wrapped


def snapshot_cached(fn):
    @wraps(fn)
    def wrapped(self, *args):
        cache = _snapshot.get()
        if cache is None:
            return fn(self, *args)
        key = (id(self), fn.__name__, *(id(a) for a in args))
        if key not in cache:
            cache[key] = (args, fn(self, *args))  # Retain arguments so object IDs cannot be recycled.
        return cache[key][1]
    return wrapped


def snapshot_keyed(fn):
    """Request-only memoization for methods with immutable value arguments."""
    @wraps(fn)
    def wrapped(self, *args):
        cache = _snapshot.get()
        if cache is None:
            return fn(self, *args)
        key = (id(self), fn, args)
        if key not in cache:
            cache[key] = fn(self, *args)
        return cache[key]
    return wrapped
