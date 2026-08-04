import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.wiki.validate_changes import validate


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class ValidateChangesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name) / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Test User")
        git(self.repo, "config", "user.email", "test@example.com")
        self.old_sha = "1" * 40
        self.head_sha = "2" * 40
        self.source_key = f"github:{self.head_sha}"
        self.write(
            "docs/wiki/index.md",
            "# Wiki\n\n- [Search](topics/search.md): search behavior\n",
        )
        self.write(
            "docs/wiki/log.md",
            f"# Wiki log\n\n## old — `github:{self.old_sha}`\n\n- Topics: search\n- Drift: None observed\n",
        )
        self.write(
            "docs/wiki/topics/search.md",
            "# Search\n\n## Scope\n\nSearch.\n\n## Sources\n\n"
            f"- `src/search.py` at `{self.old_sha}`\n",
        )
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "wiki baseline")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write(self, relative_path: str, content: str) -> None:
        path = self.repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def issue_codes(self) -> set[str]:
        return {
            issue.code
            for issue in validate(
                self.repo, "HEAD", self.source_key, max_files=20, max_patch_bytes=100_000
            )
        }

    def append_log(self) -> None:
        log = self.repo / "docs/wiki/log.md"
        log.write_text(
            log.read_text(encoding="utf-8")
            + f"\n## now — `{self.source_key}`\n\n- Topics: search\n- Drift: None observed\n",
            encoding="utf-8",
        )

    def update_search(self) -> None:
        self.write(
            "docs/wiki/topics/search.md",
            "# Search\n\n## Scope\n\nSearch.\n\n## Current behavior\n\nUpdated.\n\n"
            "## Sources\n\n"
            f"- `src/search.py` at `{self.head_sha}`\n",
        )

    def test_accepts_reachable_topic_with_append_only_log_and_evidence(self) -> None:
        self.update_search()
        self.append_log()

        self.assertEqual(self.issue_codes(), set())

    def test_rejects_source_change(self) -> None:
        self.write("src/search.py", "def search():\n    return []\n")
        self.append_log()

        self.assertIn("forbidden_path", self.issue_codes())

    def test_rejects_rewritten_log_prefix(self) -> None:
        self.update_search()
        self.write(
            "docs/wiki/log.md",
            f"# Rewritten log\n\n## now — `{self.source_key}`\n\n- Topics: search\n- Drift: None observed\n",
        )

        self.assertIn("log_not_append_only", self.issue_codes())

    def test_rejects_duplicate_source_key_from_base_log(self) -> None:
        duplicate_key = f"github:{self.old_sha}"
        self.update_search()
        log = self.repo / "docs/wiki/log.md"
        log.write_text(
            log.read_text(encoding="utf-8")
            + f"\n## now — `{duplicate_key}`\n\n- Topics: search\n- Drift: None observed\n",
            encoding="utf-8",
        )

        codes = {
            issue.code
            for issue in validate(
                self.repo, "HEAD", duplicate_key, max_files=20, max_patch_bytes=100_000
            )
        }

        self.assertIn("duplicate_source_key", codes)

    def test_rejects_missing_link_target(self) -> None:
        self.update_search()
        self.write(
            "docs/wiki/index.md",
            "# Wiki\n\n- [Search](topics/missing.md): search behavior\n",
        )
        self.append_log()

        self.assertIn("broken_link", self.issue_codes())

    def test_rejects_unreachable_topic(self) -> None:
        self.update_search()
        self.write(
            "docs/wiki/topics/orphan.md",
            "# Orphan\n\n## Sources\n\n"
            f"- `src/orphan.py` at `{self.head_sha}`\n",
        )
        self.append_log()

        self.assertIn("unreachable_topic", self.issue_codes())

    def test_accepts_nested_topic_reachable_through_parent(self) -> None:
        self.update_search()
        self.write(
            "docs/wiki/topics/search.md",
            "# Search\n\n## Scope\n\nSearch.\n\n"
            "## Details\n\n- [Timeout](search/timeout.md)\n\n"
            "## Sources\n\n"
            f"- `src/search.py` at `{self.head_sha}`\n",
        )
        self.write(
            "docs/wiki/topics/search/timeout.md",
            "# Search timeout\n\n## Sources\n\n"
            f"- `src/timeout.py` at `{self.head_sha}`\n",
        )
        self.append_log()

        self.assertEqual(self.issue_codes(), set())

    def test_rejects_changed_topic_without_source_heading(self) -> None:
        self.write("docs/wiki/topics/search.md", f"# Search\n\nCommit {self.head_sha}\n")
        self.append_log()

        self.assertIn("missing_sources", self.issue_codes())

    def test_rejects_changed_topic_without_head_sha(self) -> None:
        self.write(
            "docs/wiki/topics/search.md",
            "# Search\n\n## Sources\n\n- `src/search.py`\n",
        )
        self.append_log()

        self.assertIn("missing_source_commit", self.issue_codes())

    def test_rejects_sources_section_without_repository_path(self) -> None:
        self.write(
            "docs/wiki/topics/search.md",
            f"# Search\n\n## Sources\n\n- commit `{self.head_sha}`\n",
        )
        self.append_log()

        self.assertIn("missing_source_path", self.issue_codes())

    def test_rejects_changed_file_count_over_limit(self) -> None:
        self.update_search()
        self.append_log()

        issues = validate(
            self.repo, "HEAD", self.source_key, max_files=1, max_patch_bytes=100_000
        )

        self.assertIn("too_many_files", {issue.code for issue in issues})

    def test_rejects_patch_size_over_limit(self) -> None:
        self.write(
            "docs/wiki/topics/search.md",
            "# Search\n\n" + ("large content\n" * 100) + "\n## Sources\n\n"
            f"- `src/search.py` at `{self.head_sha}`\n",
        )
        self.append_log()

        issues = validate(
            self.repo, "HEAD", self.source_key, max_files=20, max_patch_bytes=100
        )

        self.assertIn("patch_too_large", {issue.code for issue in issues})

    def test_rejects_symlinked_wiki_file(self) -> None:
        self.update_search()
        log_path = self.repo / "docs/wiki/log.md"
        external_log = self.repo / "generated-log.md"
        external_log.write_text(
            log_path.read_text(encoding="utf-8")
            + f"\n## now — `{self.source_key}`\n\n- Topics: search\n- Drift: None observed\n",
            encoding="utf-8",
        )
        log_path.unlink()
        log_path.symlink_to(external_log)

        self.assertIn("symlink_not_allowed", self.issue_codes())

    def test_incremental_base_checks_evidence_only_for_newly_touched_topics(self) -> None:
        baseline = git(self.repo, "rev-parse", "HEAD")
        pending_sha = "2" * 40
        self.write(
            "docs/wiki/index.md",
            "# Wiki\n\n- [Search](topics/search.md)\n- [Promotion](topics/promotion.md)\n",
        )
        self.write(
            "docs/wiki/topics/promotion.md",
            "# Promotion\n\n## Sources\n\n"
            f"- `src/promotion.py` at `{pending_sha}`\n",
        )
        log = self.repo / "docs/wiki/log.md"
        log.write_text(
            log.read_text(encoding="utf-8")
            + f"\n## pending — `github:{pending_sha}`\n\n- Topics: promotion\n- Drift: None observed\n",
            encoding="utf-8",
        )
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "pending wiki")
        pending_ref = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "reset", "--hard", baseline)
        git(self.repo, "checkout", pending_ref, "--", "docs/wiki")
        self.head_sha = "3" * 40
        self.source_key = f"github:{self.head_sha}"
        self.update_search()
        log = self.repo / "docs/wiki/log.md"
        log.write_text(
            log.read_text(encoding="utf-8")
            + f"\n## now — `{self.source_key}`\n\n- Topics: search\n- Drift: None observed\n",
            encoding="utf-8",
        )

        issues = validate(
            self.repo,
            "HEAD",
            self.source_key,
            max_files=20,
            max_patch_bytes=100_000,
            incremental_base_ref=pending_ref,
        )

        self.assertEqual(issues, [])

    def test_rejects_source_key_outside_structured_log_heading(self) -> None:
        self.update_search()
        log = self.repo / "docs/wiki/log.md"
        log.write_text(
            log.read_text(encoding="utf-8")
            + f"\nInjected source: `{self.source_key}`\n",
            encoding="utf-8",
        )

        self.assertIn("invalid_log_entry", self.issue_codes())

    def test_rejects_extra_source_key_after_valid_log_entry(self) -> None:
        self.update_search()
        self.append_log()
        log = self.repo / "docs/wiki/log.md"
        log.write_text(
            log.read_text(encoding="utf-8")
            + f"\nInjected duplicate: `{self.source_key}`\n",
            encoding="utf-8",
        )

        self.assertIn("invalid_log_entry", self.issue_codes())

    def test_rejects_commit_only_outside_sources_section(self) -> None:
        self.write(
            "docs/wiki/topics/search.md",
            f"# Search\n\nCommit {self.head_sha}\n\n## Sources\n\n- `src/search.py`\n",
        )
        self.append_log()

        self.assertIn("missing_source_commit", self.issue_codes())

    def test_rejects_broken_reference_style_link(self) -> None:
        self.update_search()
        self.write(
            "docs/wiki/index.md",
            "# Wiki\n\n- [Search][search-topic]\n\n[search-topic]: topics/missing.md\n",
        )
        self.append_log()

        self.assertIn("broken_link", self.issue_codes())


if __name__ == "__main__":
    unittest.main()
