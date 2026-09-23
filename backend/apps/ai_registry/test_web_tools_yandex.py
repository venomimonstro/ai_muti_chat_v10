import base64

from apps.ai_registry import web_tools


def test_parse_yandex_xml_extracts_title_url_and_passage(monkeypatch):
    monkeypatch.setattr(web_tools, "_assert_public_http_url", lambda _url: None)
    xml = """<?xml version='1.0' encoding='utf-8'?>
    <yandexsearch><response><results><grouping><group><doc>
      <url>https://example.com/page</url>
      <title>Пример <hlword>страницы</hlword></title>
      <passages><passage>Свежий фрагмент результата.</passage></passages>
    </doc></group></grouping></results></response></yandexsearch>"""
    results = web_tools._parse_yandex_xml(xml, 3)
    assert len(results) == 1
    assert results[0].url == "https://example.com/page"
    assert "Пример" in results[0].title
    assert "Свежий" in results[0].snippet


def test_yandex_search_decodes_raw_data(monkeypatch):
    xml = b"""<?xml version='1.0' encoding='utf-8'?><yandexsearch><response><results><grouping><group><doc><url>https://example.com/</url><title>Example</title><passages><passage>Result</passage></passages></doc></group></grouping></results></response></yandexsearch>"""

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"rawData": base64.b64encode(xml).decode()}

    monkeypatch.setenv("YANDEX_SEARCH_API_KEY", "secret-test-key")
    monkeypatch.setenv("YANDEX_SEARCH_FOLDER_ID", "folder-test")
    monkeypatch.setattr(web_tools, "_assert_public_http_url", lambda _url: None)
    monkeypatch.setattr(web_tools.httpx, "post", lambda *args, **kwargs: Response())

    results = web_tools.search_web("test", limit=1)
    assert len(results) == 1
    assert results[0].title == "Example"
    assert results[0].snippet == "Result"
