from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from .dev_context import build_repository_context


class DevRepositoryContextTests(SimpleTestCase):
    def setUp(self):
        self.binding = SimpleNamespace(
            full_name="acme/project",
            default_branch="main",
            write_enabled=True,
        )
        self.project = SimpleNamespace(github_repository=self.binding)

    @patch("apps.agents.dev_context.read_repository_file")
    @patch("apps.agents.dev_context.list_repository_directory")
    def test_discovers_source_files_beyond_old_fixed_manifest(self, list_directory, read_file):
        def directory_payload(_binding, path="", *, ref=None):
            if path == "":
                return {
                    "items": [
                        {"name": "README.md", "path": "README.md", "type": "file", "size": 100},
                        {"name": "backend", "path": "backend", "type": "dir", "size": 0},
                        {"name": "frontend", "path": "frontend", "type": "dir", "size": 0},
                        {"name": "node_modules", "path": "node_modules", "type": "dir", "size": 0},
                    ]
                }
            if path == "backend":
                return {
                    "items": [
                        {"name": "manage.py", "path": "backend/manage.py", "type": "file", "size": 100},
                        {"name": "apps", "path": "backend/apps", "type": "dir", "size": 0},
                    ]
                }
            if path == "backend/apps":
                return {
                    "items": [
                        {"name": "views.py", "path": "backend/apps/views.py", "type": "file", "size": 100},
                    ]
                }
            if path == "frontend":
                return {
                    "items": [
                        {"name": "package.json", "path": "frontend/package.json", "type": "file", "size": 100},
                        {"name": "app", "path": "frontend/app", "type": "dir", "size": 0},
                    ]
                }
            if path == "frontend/app":
                return {
                    "items": [
                        {"name": "page.tsx", "path": "frontend/app/page.tsx", "type": "file", "size": 100},
                    ]
                }
            raise AssertionError(f"unexpected directory {path}")

        list_directory.side_effect = directory_payload
        read_file.side_effect = lambda _binding, path, ref=None: {
            "path": path,
            "sha": f"sha-{path}",
            "content": f"CONTENT:{path}",
        }

        context = build_repository_context(self.project)

        paths = [item["path"] for item in context["files"]]
        self.assertIn("README.md", paths)
        self.assertIn("backend/manage.py", paths)
        self.assertIn("backend/apps/views.py", paths)
        self.assertIn("frontend/package.json", paths)
        self.assertIn("frontend/app/page.tsx", paths)
        self.assertFalse(any("node_modules" in path for path in paths))
        self.assertGreaterEqual(context["discovered_count"], 8)
        self.assertIn("Repository tree (bounded scan)", context["rendered"])
        profile = context["workspace_profile"]
        self.assertTrue(profile["scan_complete"])
        self.assertTrue(profile["snapshot_complete"])
        self.assertTrue(profile["python_project"])
        self.assertTrue(profile["django_project"])
        self.assertTrue(profile["node_project"])
        self.assertEqual(profile["evidence_level"], "complete_bounded_snapshot")

    @patch("apps.agents.dev_context.read_repository_file")
    @patch("apps.agents.dev_context.list_repository_directory")
    def test_skips_large_candidates_before_reading(self, list_directory, read_file):
        list_directory.return_value = {
            "items": [
                {"name": "README.md", "path": "README.md", "type": "file", "size": 100},
                {"name": "huge.py", "path": "huge.py", "type": "file", "size": 10 * 1024 * 1024},
            ]
        }
        read_file.return_value = {"path": "README.md", "sha": "sha", "content": "hello"}

        context = build_repository_context(self.project)

        self.assertEqual([item["path"] for item in context["files"]], ["README.md"])
        self.assertTrue(context["workspace_profile"]["snapshot_complete"])
        read_file.assert_called_once()

    @patch("apps.agents.dev_context.MAX_SCAN_DIRECTORIES", 1)
    @patch("apps.agents.dev_context.read_repository_file")
    @patch("apps.agents.dev_context.list_repository_directory")
    def test_marks_snapshot_bounded_when_directory_queue_is_truncated(self, list_directory, read_file):
        def directory_payload(_binding, path="", *, ref=None):
            if path == "":
                return {
                    "items": [
                        {"name": "README.md", "path": "README.md", "type": "file", "size": 10},
                        {"name": "backend", "path": "backend", "type": "dir", "size": 0},
                        {"name": "frontend", "path": "frontend", "type": "dir", "size": 0},
                    ]
                }
            if path == "backend":
                return {"items": [{"name": "app.py", "path": "backend/app.py", "type": "file", "size": 10}]}
            raise AssertionError(f"unexpected directory {path}")

        list_directory.side_effect = directory_payload
        read_file.side_effect = lambda _binding, path, ref=None: {"sha": "sha", "content": "x = 1\n"}

        context = build_repository_context(self.project)

        profile = context["workspace_profile"]
        self.assertFalse(profile["scan_complete"])
        self.assertFalse(profile["snapshot_complete"])
        self.assertEqual(profile["evidence_level"], "bounded_discovery_snapshot")
