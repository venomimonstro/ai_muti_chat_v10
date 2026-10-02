"""Bounded public HTTP transport. Connect to the validated IP, keep TLS hostname."""
import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import quote, urljoin, urlsplit


class PublicNetworkError(ValueError):
    pass


def public_target(url):
    parsed = urlsplit(str(url))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise PublicNetworkError("Укажите публичный HTTP(S) URL без логина и пароля")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    except (ValueError, OSError) as exc:
        raise PublicNetworkError("Не удалось определить адрес сервиса") from exc
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise PublicNetworkError("Доступ к локальным, служебным и частным адресам запрещён")
    return parsed, addresses[0][4][0], port


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, address, port):
        super().__init__(host, port, timeout=15, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def public_request(url, *, method="GET", headers=None, payload=None, max_bytes=262144):
    parsed, address, port = public_target(url)
    if method not in {"GET", "POST"}:
        raise PublicNetworkError("Поддерживаются GET и POST")
    connection = PinnedHTTPSConnection(parsed.hostname, address, port) if parsed.scheme == "https" else http.client.HTTPConnection(address, port, timeout=15)
    host_header = parsed.hostname.encode("idna").decode()
    if ":" in host_header:
        host_header = "[" + host_header + "]"
    if parsed.port:
        host_header += ":" + str(parsed.port)
    request_headers = {"Host": host_header, "User-Agent": "AIWorkspace-Agent/1.0", "Accept-Encoding": "identity", **(headers or {})}
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        if len(body) > 65536:
            raise PublicNetworkError("Тело запроса превышает 64 КБ")
        request_headers["Content-Type"] = "application/json"
    try:
        connection.request(method, quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~") + ("?" + quote(parsed.query, safe="%:@!$&'()*+,;=/?-._~") if parsed.query else ""), body=body, headers=request_headers)
        response = connection.getresponse()
        content = response.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise PublicNetworkError("Ответ сервиса превышает допустимый размер")
        return {"status": response.status, "content_type": response.getheader("Content-Type", ""), "location": response.getheader("Location", ""), "body": content}
    except (OSError, http.client.HTTPException) as exc:
        raise PublicNetworkError("Соединение с внешним сервисом прервалось") from exc
    finally:
        connection.close()


def public_page(url):
    url = urlsplit(url)._replace(fragment="").geturl()
    for _ in range(5):
        result = public_request(url)
        if result["status"] in {301, 302, 303, 307, 308} and result["location"]:
            url = urljoin(url, result["location"])
            continue
        if not 200 <= result["status"] < 300:
            raise PublicNetworkError(f"Страница вернула HTTP {result['status']}")
        if not any(kind in result["content_type"].lower() for kind in ("text/html", "text/plain")):
            raise PublicNetworkError("Браузерный шаг читает HTML и текстовые страницы")
        return url, result
    raise PublicNetworkError("Страница содержит слишком много перенаправлений")
