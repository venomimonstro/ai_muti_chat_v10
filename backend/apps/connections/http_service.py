from urllib.parse import urljoin, urlsplit

from django.core.exceptions import ValidationError

from apps.agents.public_network import PublicNetworkError, public_request, public_target


def normalize_http_url(value):
    value = str(value or "").strip()
    try:
        parsed, _, _ = public_target(value)
        if parsed.scheme != "https" or parsed.fragment or parsed.query:
            raise PublicNetworkError("Используйте HTTPS URL без фрагмента")
    except PublicNetworkError as exc:
        raise ValidationError(str(exc)) from exc
    return value.rstrip("/")


def connection_request(connection, *, path="", method="GET", payload=None, operation_key=""):
    base = normalize_http_url(connection.base_url)
    if path.startswith(("/", "\\")) or "://" in path or ".." in path or "#" in path:
        raise ValidationError("Укажите относительный путь внутри подключённого сервиса")
    target = urljoin(base.rstrip("/") + "/", path)
    if urlsplit(target).netloc != urlsplit(base).netloc:
        raise ValidationError("Запрос не может менять адрес подключённого сервиса")
    headers = {"Accept": "application/json"}
    secret = connection.get_secret()
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    if operation_key:
        headers["Idempotency-Key"] = operation_key
    try:
        result = public_request(target, method=method, headers=headers, payload=payload, max_bytes=65536)
    except PublicNetworkError as exc:
        raise ValidationError(str(exc)) from exc
    if not 200 <= result["status"] < 300:
        raise ValidationError(f"Сервис вернул HTTP {result['status']}. Перенаправления с секретом не выполняются")
    text = result["body"].decode("utf-8", errors="replace")
    return text.replace(secret, "[secret]") if secret else text


def check_http(connection):
    connection_request(connection)
    return {"transport": "https", "authentication": "bearer" if connection.secret_encrypted else "none"}
