from .system_health import _store_issue


def record_client_error(*, user, source_path, error_name, message, stack, correlation_id=""):
    safe_name = (error_name or "JavaScriptError")[:120]
    safe_message = (message or safe_name)[:500]
    safe_path = (source_path or "unknown")[:220]
    return _store_issue(
        exception_type=f"Frontend:{safe_name}",
        summary=safe_message,
        source=f"frontend:{safe_path}",
        traceback_text=(stack or "")[-8000:],
        correlation_id=(correlation_id or "")[:160],
        user_id=str(user.id),
    )
