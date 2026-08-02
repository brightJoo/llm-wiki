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
                "FAKE_CAPTURE": str(self.capture),
            }
        )
        return env

    def test_runs_claude_in_bounded_print_mode_and_writes_json(self) -> None:
        completed = run(
            str(RUN_COMPILER),
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

    def test_empty_wiki_diff_produces_changed_false(self) -> None:
        completed = self.create_patch()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        metadata = json.loads(self.metadata.read_text(encoding="utf-8"))
        self.assertFalse(metadata["changed"])
        self.assertEqual(self.patch.read_bytes(), b"")


if __name__ == "__main__":
    unittest.main()
