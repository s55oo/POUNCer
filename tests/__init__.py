

# Python's garbage collector runs in whatever thread happens to allocate. If a
# worker thread (run history, cluster relay, sockets) frees a leftover Tk
# object, Tcl crashes ("Illegal instruction"). In the tests: no automatic GC,
# a full collection after each test in the main thread instead.
import gc as _gc
import unittest as _unittest

_gc.disable()
_orig_call = _unittest.TestCase.__call__


def _call_then_collect(self, *args, **kwargs):
    try:
        return _orig_call(self, *args, **kwargs)
    finally:
        _gc.collect()


_unittest.TestCase.__call__ = _call_then_collect
