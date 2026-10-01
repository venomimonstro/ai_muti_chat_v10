from __future__ import annotations


class AttemptScopedAdapter:
    """Resolve the final provider adapter separately for every stream attempt.

    ``streaming.run`` intentionally owns retry/fallback policy. Historically it
    resolved one adapter before entering that retry loop, which meant a retry could
    keep using the exact credential that had just been degraded. It also allowed a
    last-millisecond readiness/credential error from ``adapter_for`` to escape before
    the loop's ProviderError handler could move to a spare route.

    This lightweight proxy delays resolution until ``stream()`` is actually iterated
    (inside the retry try/except). Every subsequent call resolves again, so dispatch
    can select the newly-healthiest credential. The most recently resolved adapter is
    retained only so reliability accounting can attribute success/failure to the
    exact key that was used.
    """

    def __init__(self, factory):
        self._factory = factory
        self._current = None

    @property
    def credential(self):
        return getattr(self._current, "credential", None) if self._current is not None else None

    @property
    def api_key(self):
        return getattr(self._current, "api_key", "") if self._current is not None else ""

    @property
    def api_key_id(self):
        return getattr(self._current, "api_key_id", None) if self._current is not None else None

    def __getattr__(self, name):
        current = self.__dict__.get("_current")
        if current is None:
            raise AttributeError(name)
        return getattr(current, name)

    def stream(self, *args, **kwargs):
        current = self._factory()
        self._current = current
        yield from current.stream(*args, **kwargs)


def install(streaming_module) -> None:
    current = streaming_module.adapter_for
    if getattr(current, "_ai_workspace_attempt_scoped_adapter", False):
        return

    def attempt_scoped_adapter_for(model, *args, **kwargs):
        return AttemptScopedAdapter(lambda: current(model, *args, **kwargs))

    attempt_scoped_adapter_for._ai_workspace_attempt_scoped_adapter = True
    attempt_scoped_adapter_for._raw_adapter_for = current
    streaming_module.adapter_for = attempt_scoped_adapter_for
