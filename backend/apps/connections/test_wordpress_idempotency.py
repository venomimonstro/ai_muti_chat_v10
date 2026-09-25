from types import SimpleNamespace
from unittest.mock import patch

from .wordpress import create_wordpress_post


def test_create_wordpress_post_reuses_existing_slug_without_second_post():
    connection = SimpleNamespace()
    existing_payload = {
        "id": 321,
        "status": "publish",
        "link": "https://example.test/post",
        "slug": "stable-run-slug",
    }

    with patch("apps.connections.wordpress._request", return_value=[existing_payload]) as request:
        result = create_wordpress_post(
            connection,
            title="Article",
            content="Body",
            status="publish",
            slug="stable-run-slug",
        )

    assert result["post_id"] == 321
    assert result["existing"] is True
    assert request.call_count == 1
    assert request.call_args.args[1] == "GET"


def test_create_wordpress_post_posts_once_when_slug_is_new():
    connection = SimpleNamespace()
    created_payload = {
        "id": 654,
        "status": "draft",
        "link": "https://example.test/draft",
        "slug": "new-stable-slug",
    }

    with patch("apps.connections.wordpress._request", side_effect=[[], created_payload]) as request:
        result = create_wordpress_post(
            connection,
            title="Article",
            content="Body",
            status="draft",
            slug="new-stable-slug",
        )

    assert result["post_id"] == 654
    assert result["existing"] is False
    assert request.call_count == 2
    assert request.call_args_list[0].args[1] == "GET"
    assert request.call_args_list[1].args[1] == "POST"
    assert request.call_args_list[1].kwargs["json"]["slug"] == "new-stable-slug"
