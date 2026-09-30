import json

import pytest
from django.core.exceptions import ValidationError

from .dev_changes import parse_change_proposal
from .dev_execution import enrich_changes_with_snapshot


def _proposal(changes):
    return json.dumps({"changes": changes}, ensure_ascii=False)


def test_change_proposal_accepts_safe_delete_without_content():
    changes = parse_change_proposal(
        _proposal([{"path": "backend/obsolete.py", "operation": "delete", "reason": "unused"}])
    )
    assert changes == [
        {"path": "backend/obsolete.py", "operation": "delete", "reason": "unused"}
    ]


def test_change_proposal_rejects_delete_with_content():
    with pytest.raises(ValidationError, match="delete"):
        parse_change_proposal(
            _proposal(
                [
                    {
                        "path": "backend/obsolete.py",
                        "operation": "delete",
                        "content": "do not accept",
                    }
                ]
            )
        )


def test_change_proposal_rejects_duplicate_path_operations():
    with pytest.raises(ValidationError, match="несколько операций"):
        parse_change_proposal(
            _proposal(
                [
                    {"path": "app.py", "operation": "update", "content": "x = 1"},
                    {"path": "app.py", "operation": "delete"},
                ]
            )
        )


def test_delete_is_bound_to_snapshot_sha():
    enriched = enrich_changes_with_snapshot(
        [{"path": "backend/obsolete.py", "operation": "delete", "reason": "unused"}],
        {
            "files": [
                {
                    "path": "backend/obsolete.py",
                    "sha": "abc123",
                    "content": "OLD = True\n",
                }
            ]
        },
    )
    assert enriched[0]["expected_sha"] == "abc123"


def test_delete_rejects_file_not_seen_by_developer():
    with pytest.raises(ValidationError, match="не был прочитан"):
        enrich_changes_with_snapshot(
            [{"path": "secret.py", "operation": "delete", "reason": "remove"}],
            {"files": []},
        )
