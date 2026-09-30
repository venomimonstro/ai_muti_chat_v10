"""Production streaming wrapper for the unified paid routing contract.

All model/provider failover must happen inside ``streaming.run`` where candidate
prices, customer wallet reservation and provider procurement reservation are known
before the external API call. The legacy managed_stream emergency path is retained
only for backwards-compatible helper functions, but production traffic no longer
executes an unpriced/free model outside the approved route.
"""

from .managed_stream import (
    _finalize_unhandled_disconnect,
    _publicize_sse_chunk,
    _rewrite_error_chunk_if_needed,
)
from .streaming import run


def managed_run(generation, *, adapter=None):
    try:
        for chunk in run(generation, adapter=adapter):
            chunk = _rewrite_error_chunk_if_needed(generation, chunk)
            yield _publicize_sse_chunk(generation, chunk)
    finally:
        _finalize_unhandled_disconnect(generation)
