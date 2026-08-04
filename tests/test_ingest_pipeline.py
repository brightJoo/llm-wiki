import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUN_COMPILER = PROJECT_ROOT / "scripts/wiki/run_compiler.sh"
CREATE_PATCH = PROJECT_ROOT / "scripts/wiki/create_patch.sh"
PUBLISH_PR = PROJECT_ROOT / "scripts/wiki/publish_pr.sh"
VALIDATOR = PROJECT_ROOT / "scripts/wiki/validate_changes.py"
CONTEXT_PREPARER = PROJECT_ROOT / "scripts/wiki/prepare_context.py"
SYNC_WIKI = PROJECT_ROOT / "scripts/wiki/sync_wiki.py"


def run(
    *args: str, cwd: Path, env: Optional[Dict[str, str]] = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args),
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def git(repo: Path, *args: str) -> str:
    result = run("git", "-C", str(repo), *args, cwd=repo)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class CompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.prompt = self.root / "prompt.md"
        self.prompt.write_text("Compile the Wiki.\n", encoding="utf-8")
        self.policy = self.root / "policy.md"
        self.policy.write_text("Only edit the Wiki.\n", encoding="utf-8")
        self.result = self.root / "result.json"
        self.capture = self.root / "capture.json"
        self.fake_claude = self.root / "fake-claude"
        self.fake_claude.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "capture = {'argv': sys.argv[1:], 'stdin': sys.stdin.read()}\n"
            "with open(os.environ['FAKE_CAPTURE'], 'w', encoding='utf-8') as fh:\n"
            "    json.dump(capture, fh)\n"
            "json.dump({'result': 'compiled', 'is_error': False}, sys.stdout)\n",
            encoding="utf-8",
        )
        self.fake_claude.chmod(0o755)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def compiler_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "CLAUDE_BIN": str(self.fake_claude),
                "CLAUDE_MAX_TURNS": "5",
                "CLAUDE_ALLOWED_TOOLS": "Read,Grep,Glob,Edit,Write",
                "CLAUDE_TOOLS": "Read,Grep,Glob,Edit,Write",
                "FAKE_CAPTURE": str(self.capture),
            }
        )
        return env

    def test_runs_claude_in_bounded_print_mode_and_writes_json(self) -> None:
        completed = run(
            str(RUN_COMPILER),
            str(self.policy),
            str(self.prompt),
            str(self.result),
            cwd=PROJECT_ROOT,
            env=self.compiler_env(),
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(self.result.read_text(encoding="utf-8"))["result"], "compiled"
        )
        capture = json.loads(self.capture.read_text(encoding="utf-8"))
        self.assertEqual(capture["stdin"], "Compile the Wiki.\n")
        self.assertEqual(
            capture["argv"],
            [
                "-p",
                "--safe-mode",
                "--disable-slash-commands",
                "--no-session-persistence",
                "--strict-mcp-config",
                "--disallowedTools",
                "mcp__*",
                "--tools",
                "Read,Grep,Glob,Edit,Write",
                "--append-system-prompt-file",
                str(self.policy),
                "--output-format",
                "json",
                "--max-turns",
                "5",
                "--allowedTools",
                "Read,Grep,Glob,Edit,Write",
            ],
        )

    def test_rejects_non_json_claude_output(self) -> None:
        self.fake_claude.write_text("#!/bin/sh\nprintf 'not-json'\n", encoding="utf-8")
        self.fake_claude.chmod(0o755)

        completed = run(
            str(RUN_COMPILER),
            str(self.policy),
            str(self.prompt),
            str(self.result),
            cwd=PROJECT_ROOT,
            env=self.compiler_env(),
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(self.result.exists())
        self.assertNotIn("not-json", completed.stderr)


class PatchArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "Test User")
        git(self.repo, "config", "user.email", "test@example.com")
        self.write("src/search.py", "def search():\n    return []\n")
        self.write("docs/wiki/index.md", "# Wiki\n")
        self.write("docs/wiki/log.md", "# Wiki log\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "baseline")
        self.base = git(self.repo, "rev-parse", "HEAD")
        self.patch = self.root / "artifact/wiki.patch"
        self.metadata = self.root / "artifact/metadata.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write(self, relative_path: str, content: str) -> None:
        path = self.repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def create_patch(self) -> subprocess.CompletedProcess:
        return run(
            str(CREATE_PATCH),
            self.base,
            str(self.patch),
            str(self.metadata),
            cwd=self.repo,
        )

    def test_patch_contains_new_wiki_files_but_not_source_changes(self) -> None:
        self.write("src/search.py", "raise RuntimeError('not part of patch')\n")
        self.write(
            "docs/wiki/index.md", "# Wiki\n\n- [Search](topics/search.md)\n"
        )
        self.write("docs/wiki/topics/search.md", "# Search\n")

        completed = self.create_patch()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        patch_text = self.patch.read_text(encoding="utf-8")
        self.assertIn("docs/wiki/topics/search.md", patch_text)
        self.assertNotIn("src/search.py", patch_text)
        fresh = self.root / "fresh"
        git(self.root, "clone", str(self.repo), str(fresh))
        applied = run("git", "apply", str(self.patch), cwd=fresh)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(
            (fresh / "docs/wiki/topics/search.md").read_text(encoding="utf-8"),
            "# Search\n",
        )

    def test_metadata_hash_and_size_match_patch_bytes(self) -> None:
        self.write("docs/wiki/index.md", "# Updated Wiki\n")

        completed = self.create_patch()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        patch_bytes = self.patch.read_bytes()
        metadata = json.loads(self.metadata.read_text(encoding="utf-8"))
        self.assertTrue(metadata["changed"])
        self.assertEqual(metadata["bytes"], len(patch_bytes))
        self.assertEqual(metadata["sha256"], hashlib.sha256(patch_bytes).hexdigest())
        self.assertEqual(metadata["base_ref"], self.base)
        self.assertEqual(
            metadata["seed_tree"], git(self.repo, "rev-parse", f"{self.base}:docs/wiki")
        )

    def test_empty_wiki_diff_produces_changed_false(self) -> None:
        completed = self.create_patch()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        metadata = json.loads(self.metadata.read_text(encoding="utf-8"))
        self.assertFalse(metadata["changed"])
        self.assertEqual(self.patch.read_bytes(), b"")


class SyncWikiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "source"
        self.target = self.root / "target"
        (self.source / "docs/wiki/topics").mkdir(parents=True)
        (self.target / "docs/wiki/topics").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def sync(self) -> subprocess.CompletedProcess:
        return run(
            "python3",
            str(SYNC_WIKI),
            "--source",
            str(self.source),
            "--target",
            str(self.target),
            cwd=PROJECT_ROOT,
        )

    def test_replaces_only_wiki_tree_with_regular_markdown_files(self) -> None:
        (self.source / "docs/wiki/index.md").write_text("# New\n", encoding="utf-8")
        (self.source / "docs/wiki/topics/search.md").write_text(
            "# Search\n", encoding="utf-8"
        )
        (self.target / "docs/wiki/topics/stale.md").write_text(
            "# Stale\n", encoding="utf-8"
        )

        completed = self.sync()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            (self.target / "docs/wiki/index.md").read_text(encoding="utf-8"),
            "# New\n",
        )
        self.assertFalse((self.target / "docs/wiki/topics/stale.md").exists())

    def test_rejects_symlink_before_changing_target(self) -> None:
        outside = self.root / "outside.md"
        outside.write_text("outside\n", encoding="utf-8")
        (self.source / "docs/wiki/index.md").symlink_to(outside)
        sentinel = self.target / "docs/wiki/index.md"
        sentinel.write_text("# Trusted\n", encoding="utf-8")

        completed = self.sync()

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "# Trusted\n")

    def test_rejects_non_markdown_file_before_changing_target(self) -> None:
        (self.source / "docs/wiki/payload.sh").write_text("exit 0\n", encoding="utf-8")
        sentinel = self.target / "docs/wiki/index.md"
        sentinel.write_text("# Trusted\n", encoding="utf-8")

        completed = self.sync()

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "# Trusted\n")

    def test_rejects_symlinked_wiki_root_before_changing_target(self) -> None:
        outside = self.root / "outside-wiki"
        outside.mkdir()
        (outside / "index.md").write_text("# Outside\n", encoding="utf-8")
        (self.source / "docs/wiki/topics").rmdir()
        (self.source / "docs/wiki").rmdir()
        (self.source / "docs/wiki").symlink_to(outside, target_is_directory=True)
        sentinel = self.target / "docs/wiki/index.md"
        sentinel.write_text("# Trusted\n", encoding="utf-8")

        completed = self.sync()

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "# Trusted\n")


class PublisherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        self.remote.mkdir()
        self.seed.mkdir()
        git(self.remote, "init", "--bare")
        git(self.seed, "init", "-b", "main")
        git(self.seed, "config", "user.name", "Test User")
        git(self.seed, "config", "user.email", "test@example.com")
        self.old_sha = "1" * 40
        self.head_sha = "2" * 40
        self.source_key = f"github:{self.head_sha}"
        self.write_seed(
            "docs/wiki/index.md",
            "# Wiki\n\n- [Search](topics/search.md): search behavior\n",
        )
        self.write_seed(
            "docs/wiki/log.md",
            f"# Wiki log\n\n## old — `github:{self.old_sha}`\n\n- Topics: search\n- Drift: None observed\n",
        )
        self.write_seed(
            "docs/wiki/topics/search.md",
            "# Search\n\n## Sources\n\n"
            f"- `src/search.py` at `{self.old_sha}`\n",
        )
        self.write_seed("src/search.py", "def search():\n    return []\n")
        self.write_seed("src/promotion.py", "PROMOTION = True\n")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "baseline")
        self.base = git(self.seed, "rev-parse", "HEAD")
        self.head_sha = self.base
        self.source_key = f"github:{self.head_sha}"
        git(self.seed, "remote", "add", "origin", str(self.remote))
        git(self.seed, "push", "-u", "origin", "main")
        run(
            "git",
            "--git-dir",
            str(self.remote),
            "symbolic-ref",
            "HEAD",
            "refs/heads/main",
            cwd=self.root,
        )
        self.artifact_dir = self.root / "artifact"
        self.patch = self.artifact_dir / "wiki.patch"
        self.metadata = self.artifact_dir / "metadata.json"
        self.fake_state = self.root / "gh-state.json"
        self.fake_state.write_text('{"prs": [], "calls": []}\n', encoding="utf-8")
        self.fake_gh = self.root / "fake-gh"
        self.fake_gh.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "path = os.environ['FAKE_GH_STATE']\n"
            "with open(path, encoding='utf-8') as fh: state = json.load(fh)\n"
            "args = sys.argv[1:]\n"
            "def value(flag): return args[args.index(flag) + 1]\n"
            "if args[:2] == ['pr', 'list']:\n"
            "    print(json.dumps(state['prs']))\n"
            "elif args[:2] == ['pr', 'create']:\n"
            "    with open(value('--body-file'), encoding='utf-8') as fh: body = fh.read()\n"
            "    pr = {'number': len(state['prs']) + 1, 'body': body}\n"
            "    state['prs'] = [pr]\n"
            "    state['calls'].append('create')\n"
            "    print('https://example.test/pull/1')\n"
            "elif args[:2] == ['pr', 'edit']:\n"
            "    with open(value('--body-file'), encoding='utf-8') as fh: body = fh.read()\n"
            "    state['prs'][0]['body'] = body\n"
            "    state['calls'].append('edit')\n"
            "else:\n"
            "    raise SystemExit('unsupported fake gh call: ' + repr(args))\n"
            "with open(path, 'w', encoding='utf-8') as fh: json.dump(state, fh)\n",
            encoding="utf-8",
        )
        self.fake_gh.chmod(0o755)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_seed(self, relative_path: str, content: str) -> None:
        path = self.seed / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def build_artifact(self, changed: bool = True) -> None:
        if changed:
            self.write_seed(
                "docs/wiki/topics/search.md",
                "# Search\n\n## Current behavior\n\nUpdated.\n\n## Sources\n\n"
                f"- `src/search.py` at `{self.head_sha}`\n",
            )
            log_path = self.seed / "docs/wiki/log.md"
            log_path.write_text(
                log_path.read_text(encoding="utf-8")
                + f"\n## 2026-08-04T00:00:00Z — `{self.source_key}`\n\n- Topics: search\n- Drift: None observed\n",
                encoding="utf-8",
            )
        completed = run(
            str(CREATE_PATCH),
            self.base,
            str(self.patch),
            str(self.metadata),
            cwd=self.seed,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        git(self.seed, "reset", "--hard", self.base)

    def clone_publisher(self, name: str = "publisher") -> Path:
        clone = self.root / name
        git(self.root, "clone", str(self.remote), str(clone))
        return clone

    def publisher_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "GH_TOKEN": "test-token",
                "GH_BIN": str(self.fake_gh),
                "FAKE_GH_STATE": str(self.fake_state),
                "WIKI_VALIDATOR": str(VALIDATOR),
                "WIKI_CONTEXT_PREPARER": str(CONTEXT_PREPARER),
                "PYTHON_BIN": "python3",
            }
        )
        return env

    def publish(self, clone: Path) -> subprocess.CompletedProcess:
        return run(
            str(PUBLISH_PR),
            str(self.patch),
            str(self.metadata),
            "main",
            "wiki/pending",
            self.source_key,
            cwd=clone,
            env=self.publisher_env(),
        )

    def test_creates_managed_branch_and_pull_request(self) -> None:
        self.build_artifact()
        clone = self.clone_publisher()

        completed = self.publish(clone)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        remote_branch_result = run(
            "git", "--git-dir", str(self.remote), "rev-parse", "refs/heads/wiki/pending", cwd=self.root
        )
        self.assertEqual(
            remote_branch_result.returncode,
            0,
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}\n{remote_branch_result.stderr}",
        )
        remote_branch = remote_branch_result.stdout.strip()
        self.assertRegex(remote_branch, r"^[0-9a-f]{40}$")
        state = json.loads(self.fake_state.read_text(encoding="utf-8"))
        self.assertEqual(state["calls"], ["create"])
        self.assertIn("<!-- llm-wiki:managed-pr -->", state["prs"][0]["body"])
        self.assertIn(self.source_key, state["prs"][0]["body"])

    def test_empty_artifact_persists_cursor_without_pull_request(self) -> None:
        self.build_artifact(changed=False)
        clone = self.clone_publisher()

        completed = self.publish(clone)

        self.assertEqual(completed.returncode, 0, completed.stderr)
        branch = run(
            "git",
            "--git-dir",
            str(self.remote),
            "show-ref",
            "--verify",
            "refs/heads/wiki/pending",
            cwd=self.root,
        )
        self.assertEqual(
            branch.returncode,
            0,
            f"publisher stdout:\n{completed.stdout}\npublisher stderr:\n{completed.stderr}\n{branch.stderr}",
        )
        message = git(
            self.root,
            "--git-dir",
            str(self.remote),
            "log",
            "-1",
            "--format=%B",
            "refs/heads/wiki/pending",
        )
        self.assertIn(f"LLM-Wiki-Source: {self.source_key}", message)
        state = json.loads(self.fake_state.read_text(encoding="utf-8"))
        self.assertEqual(state["calls"], [])

    def test_refuses_unmanaged_existing_branch(self) -> None:
        self.build_artifact()
        intruder = self.clone_publisher("intruder")
        git(intruder, "switch", "-c", "wiki/pending")
        (intruder / "README.md").write_text("user branch\n", encoding="utf-8")
        git(intruder, "add", "README.md")
        git(intruder, "config", "user.name", "Test User")
        git(intruder, "config", "user.email", "test@example.com")
        git(intruder, "commit", "-m", "user work")
        git(intruder, "push", "origin", "wiki/pending")
        clone = self.clone_publisher()

        completed = self.publish(clone)

        self.assertNotEqual(
            completed.returncode,
            0,
            f"publisher stdout:\n{completed.stdout}\npublisher stderr:\n{completed.stderr}\npatch:\n{self.patch.read_text(encoding='utf-8')}",
        )
        self.assertIn("not managed", completed.stderr)

    def test_updates_existing_managed_pull_request_with_incremental_patch(self) -> None:
        self.write_seed(
            "docs/wiki/index.md",
            "# Wiki\n\n- [Search](topics/search.md)\n- [Promotion](topics/promotion.md)\n",
        )
        self.write_seed(
            "docs/wiki/topics/promotion.md",
            "# Promotion\n\n## Sources\n\n"
            f"- `src/promotion.py` at `{self.head_sha}`\n",
        )
        self.build_artifact()
        first_clone = self.clone_publisher("first-publisher")
        first = self.publish(first_clone)
        self.assertEqual(first.returncode, 0, first.stderr)

        self.write_seed("src/next.py", "next_change = True\n")
        git(self.seed, "add", "src/next.py")
        git(self.seed, "commit", "-m", "next source change")
        git(self.seed, "push", "origin", "main")
        next_sha = git(self.seed, "rev-parse", "HEAD")
        git(self.seed, "fetch", "origin", "wiki/pending")
        git(self.seed, "checkout", "FETCH_HEAD", "--", "docs/wiki")
        git(self.seed, "add", "docs/wiki")
        git(self.seed, "commit", "-m", "snapshot pending Wiki seed")
        incremental_base = git(self.seed, "rev-parse", "HEAD")

        self.head_sha = next_sha
        self.source_key = f"github:{next_sha}"
        self.write_seed(
            "docs/wiki/topics/search.md",
            "# Search\n\n## Current behavior\n\nUpdated twice.\n\n## Sources\n\n"
            f"- `src/search.py` at `{next_sha}`\n",
        )
        log_path = self.seed / "docs/wiki/log.md"
        log_path.write_text(
            log_path.read_text(encoding="utf-8")
            + f"\n## 2026-08-04T01:00:00Z — `{self.source_key}`\n\n- Topics: search\n- Drift: None observed\n",
            encoding="utf-8",
        )
        artifact = run(
            str(CREATE_PATCH),
            incremental_base,
            str(self.patch),
            str(self.metadata),
            next_sha,
            cwd=self.seed,
        )
        self.assertEqual(artifact.returncode, 0, artifact.stderr)
        git(self.seed, "reset", "--hard", self.base)
        second_clone = self.clone_publisher("second-publisher")

        second = self.publish(second_clone)

        self.assertEqual(second.returncode, 0, second.stderr)
        state = json.loads(self.fake_state.read_text(encoding="utf-8"))
        self.assertEqual(state["calls"], ["create", "edit"])
        self.assertIn(self.source_key, state["prs"][0]["body"])

    def test_accepts_artifact_when_main_advanced(self) -> None:
        self.build_artifact()
        self.write_seed("src/later.py", "later = True\n")
        git(self.seed, "add", "src/later.py")
        git(self.seed, "commit", "-m", "later main change")
        git(self.seed, "push", "origin", "main")
        clone = self.clone_publisher()

        completed = self.publish(clone)

        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_malicious_artifact_is_rejected_before_checkout_is_mutated(self) -> None:
        self.write_seed("scripts/wiki/validate_changes.py", "print('trusted')\n")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "add trusted validator fixture")
        git(self.seed, "push", "origin", "main")
        self.base = git(self.seed, "rev-parse", "HEAD")
        self.write_seed("scripts/wiki/validate_changes.py", "print('owned')\n")
        self.write_seed(
            "docs/wiki/log.md",
            (self.seed / "docs/wiki/log.md").read_text(encoding="utf-8")
            + f"\n## 2026-08-04T00:00:00Z — `{self.source_key}`\n\n- Topics: None\n- Drift: None observed\n",
        )
        raw = run(
            "git", "diff", "--binary", self.base, cwd=self.seed
        )
        self.patch.parent.mkdir(parents=True, exist_ok=True)
        self.patch.write_text(raw.stdout, encoding="utf-8")
        patch_bytes = self.patch.read_bytes()
        self.metadata.write_text(
            json.dumps(
                {
                    "version": 1,
                    "changed": True,
                    "bytes": len(patch_bytes),
                    "sha256": hashlib.sha256(patch_bytes).hexdigest(),
                    "base_ref": self.base,
                    "seed_tree": git(self.seed, "rev-parse", f"{self.base}:docs/wiki"),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        git(self.seed, "reset", "--hard", self.base)
        clone = self.clone_publisher()

        completed = self.publish(clone)

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(
            (clone / "scripts/wiki/validate_changes.py").read_text(encoding="utf-8"),
            "print('trusted')\n",
        )

    def test_rejects_artifact_with_different_seed_tree(self) -> None:
        self.build_artifact()
        metadata = json.loads(self.metadata.read_text(encoding="utf-8"))
        metadata["seed_tree"] = "0" * 40
        self.metadata.write_text(json.dumps(metadata) + "\n", encoding="utf-8")
        clone = self.clone_publisher()

        completed = self.publish(clone)

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("seed", completed.stderr)



if __name__ == "__main__":
    unittest.main()
