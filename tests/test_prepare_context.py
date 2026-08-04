import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.wiki.prepare_context import (
    ContextError,
    last_source_commit,
    managed_source_commit,
    prepare_context,
    seed_pending_wiki,
)


ZERO_SHA = "0" * 40


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class PrepareContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Test User")
        git(self.repo, "config", "user.email", "test@example.com")
        (self.repo / "README.md").write_text("# Fixture\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "initial")
        self.base = git(self.repo, "rev-parse", "HEAD")
        self.output_dir = Path(self.temp_dir.name) / "runtime"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def commit_files(self, files: dict[str, str], message: str = "change") -> str:
        for relative_path, content in files.items():
            path = self.repo / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", message)
        return git(self.repo, "rev-parse", "HEAD")

    def test_excludes_wiki_files_but_keeps_code_and_specs(self) -> None:
        head = self.commit_files(
            {
                "src/search.py": "def search():\n    return []\n",
                "docs/specs/search/lld.md": "# Search LLD\n",
                "docs/wiki/topics/old.md": "# Old\n",
            }
        )

        context = prepare_context(
            self.repo, self.base, head, self.output_dir, 200, 1_000_000
        )

        self.assertEqual(context["status"], "ready")
        self.assertEqual(
            [item["path"] for item in context["changed_files"]],
            ["docs/specs/search/lld.md", "src/search.py"],
        )
        self.assertEqual(context["source_key"], f"github:{head}")
        self.assertEqual(context["spec_candidates"], ["docs/specs/search/lld.md"])
        diff_text = (self.output_dir / "changes.diff").read_text(encoding="utf-8")
        self.assertIn("src/search.py", diff_text)
        self.assertNotIn("docs/wiki/topics/old.md", diff_text)
        stored = json.loads(
            (self.output_dir / "context.json").read_text(encoding="utf-8")
        )
        self.assertEqual(stored, context)

    def test_docs_only_change_is_skipped(self) -> None:
        head = self.commit_files({"docs/wiki/index.md": "# Wiki\n"})

        context = prepare_context(
            self.repo, self.base, head, self.output_dir, 200, 1_000_000
        )

        self.assertEqual(context["status"], "skip")
        self.assertEqual(context["reason"], "docs_wiki_only")
        self.assertEqual(context["changed_files"], [])

    def test_zero_base_requires_explicit_bootstrap(self) -> None:
        head = git(self.repo, "rev-parse", "HEAD")

        context = prepare_context(
            self.repo, ZERO_SHA, head, self.output_dir, 200, 1_000_000
        )

        self.assertEqual(context["status"], "skip")
        self.assertEqual(context["reason"], "bootstrap_required")

    def test_rejects_non_ancestor_base(self) -> None:
        git(self.repo, "checkout", "--orphan", "other")
        (self.repo / "README.md").write_text("other\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "other root")
        unrelated = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "main")
        head = git(self.repo, "rev-parse", "HEAD")

        with self.assertRaisesRegex(ContextError, "not an ancestor"):
            prepare_context(
                self.repo, unrelated, head, self.output_dir, 200, 1_000_000
            )

    def test_rejects_file_count_over_limit(self) -> None:
        head = self.commit_files({"src/a.py": "a\n", "src/b.py": "b\n"})

        with self.assertRaisesRegex(ContextError, "file count"):
            prepare_context(self.repo, self.base, head, self.output_dir, 1, 1_000_000)

    def test_rejects_diff_over_limit(self) -> None:
        head = self.commit_files({"src/large.py": "x" * 200})

        with self.assertRaisesRegex(ContextError, "diff size"):
            prepare_context(self.repo, self.base, head, self.output_dir, 200, 20)

    def test_rename_from_wiki_to_source_is_not_discarded(self) -> None:
        self.commit_files({"docs/wiki/topics/search notes.md": "# Search\n"})
        base = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "src").mkdir()
        git(
            self.repo,
            "mv",
            "docs/wiki/topics/search notes.md",
            "src/search notes.md",
        )
        git(self.repo, "commit", "-m", "move knowledge into source")
        head = git(self.repo, "rev-parse", "HEAD")

        context = prepare_context(
            self.repo, base, head, self.output_dir, 200, 1_000_000
        )

        self.assertEqual(context["status"], "ready")
        self.assertEqual(context["changed_files"][0]["path"], "src/search notes.md")
        self.assertEqual(
            context["changed_files"][0]["previous_path"],
            "docs/wiki/topics/search notes.md",
        )

    def test_reads_last_source_commit_from_log(self) -> None:
        log = Path(self.temp_dir.name) / "log.md"
        first = "1" * 40
        second = "2" * 40
        log.write_text(
            "# Log\n\n"
            f"## 2026-08-01T00:00:00Z — `github:{first}`\n\n- Topics: None\n- Drift: None observed\n\n"
            f"## 2026-08-02T00:00:00Z — `github:{second}`\n\n- Topics: None\n- Drift: None observed\n",
            encoding="utf-8",
        )

        self.assertEqual(last_source_commit(log), second)

    def test_missing_log_has_no_source_commit(self) -> None:
        self.assertIsNone(last_source_commit(Path(self.temp_dir.name) / "missing.md"))

    def test_reads_cursor_from_managed_commit_trailer(self) -> None:
        source_commit = self.commit_files({"src/a.py": "a\n"}, "source")
        git(self.repo, "commit", "--allow-empty", "-m", "docs(wiki): cursor", "-m", f"LLM-Wiki-Managed: true\nLLM-Wiki-Source: github:{source_commit}")

        self.assertEqual(managed_source_commit(self.repo, "HEAD"), source_commit)

    def test_seeds_managed_pending_wiki_and_returns_cursor(self) -> None:
        source_commit = self.commit_files({"src/a.py": "a\n"}, "source change")
        git(self.repo, "switch", "-c", "wiki-pending")
        self.commit_files(
            {
                "docs/wiki/index.md": "# Wiki\n",
                "docs/wiki/log.md": f"# Wiki log\n\n## now — `github:{source_commit}`\n\n- Topics: None\n- Drift: None observed\n",
            },
            "docs(wiki): compile\n\nLLM-Wiki-Managed: true",
        )
        git(self.repo, "switch", "main")
        self.commit_files({"src/b.py": "b\n"}, "next source change")

        cursor = seed_pending_wiki(self.repo, "HEAD", "wiki-pending")

        self.assertEqual(cursor, source_commit)
        self.assertIn(
            source_commit,
            (self.repo / "docs/wiki/log.md").read_text(encoding="utf-8"),
        )

    def test_rejects_unmanaged_pending_branch(self) -> None:
        self.commit_files({"src/a.py": "a\n"}, "source change")
        git(self.repo, "switch", "-c", "wiki-pending")
        self.commit_files({"docs/wiki/index.md": "# Wiki\n"}, "user branch")
        git(self.repo, "switch", "main")

        with self.assertRaisesRegex(ContextError, "not managed"):
            seed_pending_wiki(self.repo, "HEAD", "wiki-pending")

    def test_rejects_overlapping_main_and_pending_wiki_changes(self) -> None:
        source_commit = self.commit_files({"src/a.py": "a\n"}, "source change")
        git(self.repo, "switch", "-c", "wiki-pending")
        self.commit_files(
            {
                "docs/wiki/index.md": "# Pending Wiki\n",
                "docs/wiki/log.md": f"# Log\n\n## now — `github:{source_commit}`\n\n- Topics: None\n- Drift: None observed\n",
            },
            "docs(wiki): compile\n\nLLM-Wiki-Managed: true",
        )
        git(self.repo, "switch", "main")
        self.commit_files({"docs/wiki/index.md": "# Human Wiki\n"}, "human wiki edit")

        with self.assertRaisesRegex(ContextError, "overlap"):
            seed_pending_wiki(self.repo, "HEAD", "wiki-pending")

    def test_ignores_stale_pending_branch_already_squash_merged(self) -> None:
        source_commit = self.commit_files({"src/a.py": "a\n"}, "source change")
        git(self.repo, "switch", "-c", "wiki-pending")
        self.commit_files(
            {
                "docs/wiki/index.md": "# Wiki\n",
                "docs/wiki/log.md": f"# Log\n\n## now — `github:{source_commit}`\n\n- Topics: None\n- Drift: None observed\n",
            },
            "docs(wiki): compile\n\nLLM-Wiki-Managed: true",
        )
        pending_tip = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "switch", "main")
        git(self.repo, "checkout", pending_tip, "--", "docs/wiki")
        git(self.repo, "commit", "-m", "squash Wiki PR")

        cursor = seed_pending_wiki(self.repo, "HEAD", "wiki-pending")

        self.assertEqual(cursor, source_commit)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_ignores_stale_merged_branch_with_unrelated_main_wiki_change(self) -> None:
        source_commit = self.commit_files({"src/a.py": "a\n"}, "source change")
        git(self.repo, "switch", "-c", "wiki-pending")
        self.commit_files(
            {
                "docs/wiki/index.md": "# Wiki\n",
                "docs/wiki/log.md": f"# Log\n\n## now — `github:{source_commit}`\n\n- Topics: None\n- Drift: None observed\n",
            },
            f"docs(wiki): compile\n\nLLM-Wiki-Managed: true\nLLM-Wiki-Source: github:{source_commit}",
        )
        pending_tip = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "switch", "main")
        git(self.repo, "checkout", pending_tip, "--", "docs/wiki")
        git(self.repo, "commit", "-m", "squash Wiki PR")
        self.commit_files(
            {"docs/wiki/topics/human.md": "# Human\n"}, "unrelated human Wiki edit"
        )

        cursor = seed_pending_wiki(self.repo, "HEAD", "wiki-pending")

        self.assertEqual(cursor, source_commit)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_ignores_stale_merged_branch_after_later_log_entry(self) -> None:
        source_commit = self.commit_files({"src/a.py": "a\n"}, "source change")
        git(self.repo, "switch", "-c", "wiki-pending")
        self.commit_files(
            {
                "docs/wiki/index.md": "# Wiki\n",
                "docs/wiki/log.md": f"# Log\n\n## first — `github:{source_commit}`\n\n- Topics: None\n- Drift: None observed\n",
            },
            f"docs(wiki): compile\n\nLLM-Wiki-Managed: true\nLLM-Wiki-Source: github:{source_commit}",
        )
        pending_tip = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "switch", "main")
        git(self.repo, "checkout", pending_tip, "--", "docs/wiki")
        git(self.repo, "commit", "-m", "squash Wiki PR")
        later_source = self.commit_files({"src/b.py": "b\n"}, "later source")
        log = self.repo / "docs/wiki/log.md"
        log.write_text(
            log.read_text(encoding="utf-8")
            + f"\n## later — `github:{later_source}`\n\n- Topics: None\n- Drift: None observed\n",
            encoding="utf-8",
        )
        git(self.repo, "add", "docs/wiki/log.md")
        git(self.repo, "commit", "-m", "later Wiki entry")

        cursor = seed_pending_wiki(self.repo, "HEAD", "wiki-pending")

        self.assertEqual(cursor, later_source)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
