#!/usr/bin/env bash
set -euo pipefail

managed_marker='<!-- llm-wiki:managed-pr -->'
managed_commit_marker='LLM-Wiki-Managed: true'

if [[ $# -ne 5 ]]; then
  echo "usage: publish_pr.sh <patch-path> <metadata-path> <base-branch> <wiki-branch> <source-key>" >&2
  exit 2
fi

patch_path=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
metadata_path=$(cd "$(dirname "$2")" && pwd)/$(basename "$2")
base_branch=$3
wiki_branch=$4
source_key=$5
python_bin=${PYTHON_BIN:-python3}
gh_bin=${GH_BIN:-gh}
validator=${WIKI_VALIDATOR:-scripts/wiki/validate_changes.py}
context_preparer=${WIKI_CONTEXT_PREPARER:-scripts/wiki/prepare_context.py}

if [[ ! -f "$patch_path" || ! -f "$metadata_path" ]]; then
  echo "publish-pr: patch artifact is incomplete" >&2
  exit 2
fi
if ! git check-ref-format --branch "$base_branch" >/dev/null 2>&1 ||
  ! git check-ref-format --branch "$wiki_branch" >/dev/null 2>&1; then
  echo "publish-pr: invalid branch name" >&2
  exit 2
fi
if [[ ! "$source_key" =~ ^github:[0-9a-f]{40}$ ]]; then
  echo "publish-pr: invalid source key" >&2
  exit 2
fi
if [[ -z ${GH_TOKEN:-} ]]; then
  echo "publish-pr: GH_TOKEN is required" >&2
  exit 2
fi
if [[ ! -f "$validator" || ! -f "$context_preparer" ]]; then
  echo "publish-pr: trusted engine files are missing" >&2
  exit 2
fi
if [[ -n $(git status --porcelain) ]]; then
  echo "publish-pr: checkout must be clean before applying the artifact" >&2
  exit 2
fi

metadata_values=$(
  "$python_bin" - "$patch_path" "$metadata_path" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

patch = Path(sys.argv[1]).read_bytes()
metadata = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
required = {"version", "changed", "bytes", "sha256", "base_ref", "seed_tree"}
if set(metadata) != required or metadata["version"] != 1:
    raise SystemExit("invalid patch metadata schema")
if not isinstance(metadata["changed"], bool):
    raise SystemExit("invalid changed flag")
if metadata["bytes"] != len(patch):
    raise SystemExit("patch byte count does not match metadata")
if metadata["sha256"] != hashlib.sha256(patch).hexdigest():
    raise SystemExit("patch checksum does not match metadata")
if metadata["changed"] != bool(patch):
    raise SystemExit("patch changed flag does not match content")
base_ref = metadata["base_ref"]
if not isinstance(base_ref, str) or not base_ref:
    raise SystemExit("invalid patch base ref")
seed_tree = metadata["seed_tree"]
if seed_tree != "absent" and not (
    isinstance(seed_tree, str)
    and len(seed_tree) == 40
    and all(character in "0123456789abcdef" for character in seed_tree)
):
    raise SystemExit("invalid Wiki seed tree")
print(("true" if metadata["changed"] else "false") + "\t" + base_ref + "\t" + seed_tree)
PY
)
IFS=$'\t' read -r artifact_changed artifact_base_ref artifact_seed_tree <<< "$metadata_values"

git fetch --no-tags origin "+refs/heads/${base_branch}:refs/remotes/origin/${base_branch}"
base_ref="refs/remotes/origin/${base_branch}"
base_sha=$(git rev-parse "${base_ref}^{commit}")
artifact_base_sha=$(git rev-parse "${artifact_base_ref}^{commit}" 2>/dev/null || true)
if [[ -z "$artifact_base_sha" ]] ||
  ! git merge-base --is-ancestor "$artifact_base_sha" "$base_sha"; then
  echo "publish-pr: artifact base is not an ancestor of latest main" >&2
  exit 1
fi

remote_oid=$(git ls-remote --heads origin "refs/heads/${wiki_branch}" | awk 'NR == 1 {print $1}')
pending_ref=''
if [[ -n "$remote_oid" ]]; then
  git fetch --no-tags origin "+refs/heads/${wiki_branch}:refs/remotes/origin/${wiki_branch}"
  pending_ref="refs/remotes/origin/${wiki_branch}"
  if ! git log -1 --format=%B "$pending_ref" | grep -Fq "$managed_commit_marker"; then
    echo "publish-pr: existing wiki branch is not managed by LLM Wiki" >&2
    exit 1
  fi
fi

prs_file=$(mktemp "${TMPDIR:-/tmp}/llm-wiki-prs.XXXXXX")
report_file=$(mktemp "${TMPDIR:-/tmp}/llm-wiki-validation.XXXXXX")
body_file=$(mktemp "${TMPDIR:-/tmp}/llm-wiki-body.XXXXXX")
runtime_dir=$(mktemp -d "${TMPDIR:-/tmp}/llm-wiki-context.XXXXXX")
preflight_dir=$(mktemp -d "${TMPDIR:-/tmp}/llm-wiki-preflight.XXXXXX")
rmdir "$preflight_dir"
preflight_registered=false
cleanup() {
  if [[ "$preflight_registered" == "true" ]]; then
    git worktree remove --force "$preflight_dir" >/dev/null 2>&1 || true
  fi
  rm -f "$prs_file" "$report_file" "$body_file"
  rm -r "$runtime_dir"
}
trap cleanup EXIT

"$gh_bin" pr list --head "$wiki_branch" --base "$base_branch" --state open \
  --json number,body --limit 2 > "$prs_file"
pr_values=$(
  "$python_bin" - "$prs_file" "$managed_marker" <<'PY'
import json
import sys

prs = json.loads(open(sys.argv[1], encoding="utf-8").read())
if not isinstance(prs, list) or len(prs) > 1:
    raise SystemExit("expected at most one open Wiki PR")
if not prs:
    print("0\t0\tfalse")
else:
    pr = prs[0]
    number = pr.get("number")
    body = pr.get("body", "")
    if not isinstance(number, int) or not isinstance(body, str):
        raise SystemExit("invalid PR response")
    print("1\t" + str(number) + "\t" + ("true" if sys.argv[2] in body else "false"))
PY
)
IFS=$'\t' read -r pr_count pr_number pr_managed <<< "$pr_values"
if [[ "$pr_count" == "1" && "$pr_managed" != "true" ]]; then
  echo "publish-pr: existing Wiki PR is not managed by LLM Wiki" >&2
  exit 1
fi
if [[ -z "$remote_oid" && "$pr_count" == "1" ]]; then
  echo "publish-pr: open Wiki PR has no matching remote branch" >&2
  exit 1
fi

seed_wiki() {
  local target=$1
  local output=$2
  if [[ -n "$pending_ref" ]]; then
    "$python_bin" "$context_preparer" \
      --repo "$target" --base "$base_sha" --head "$base_sha" \
      --output-dir "$output" --max-files 100000 --max-diff-bytes 100000000 \
      --pending-ref "$pending_ref" >/dev/null
  else
    "$python_bin" "$context_preparer" \
      --repo "$target" --base "$base_sha" --head "$base_sha" \
      --output-dir "$output" --max-files 100000 --max-diff-bytes 100000000 \
      >/dev/null
  fi
  git -C "$target" config user.name "${WIKI_GIT_AUTHOR_NAME:-github-actions[bot]}"
  git -C "$target" config user.email "${WIKI_GIT_AUTHOR_EMAIL:-41898282+github-actions[bot]@users.noreply.github.com}"
  git -C "$target" add -A -- docs/wiki
  if ! git -C "$target" diff --cached --quiet; then
    git -C "$target" commit -m "chore: reconstruct pending Wiki seed" >/dev/null
  fi
}

# Validate in a disposable worktree before the publisher checkout is touched.
git worktree add --detach "$preflight_dir" "$base_ref" >/dev/null
preflight_registered=true
seed_wiki "$preflight_dir" "$runtime_dir/preflight"
seed_ref=$(git -C "$preflight_dir" rev-parse HEAD)
publisher_seed_tree=$(git -C "$preflight_dir" rev-parse "HEAD:docs/wiki" 2>/dev/null || printf 'absent')
if [[ "$publisher_seed_tree" != "$artifact_seed_tree" ]]; then
  echo "publish-pr: Wiki seed changed after compilation" >&2
  exit 1
fi
if [[ "$artifact_changed" == "true" ]]; then
  git -C "$preflight_dir" apply --check "$patch_path"
  git -C "$preflight_dir" apply "$patch_path"
fi
if ! "$python_bin" "$validator" \
  --repo "$preflight_dir" --base-ref "$seed_ref" --source-key "$source_key" \
  --report "$report_file" >/dev/null; then
  echo "publish-pr: patch failed deterministic validation" >&2
  "$python_bin" - "$report_file" >&2 <<'PY'
import json
import sys

for issue in json.load(open(sys.argv[1], encoding="utf-8")).get("issues", []):
    print(f"- {issue.get('code')}: {issue.get('path')}: {issue.get('message')}")
PY
  exit 1
fi

git switch --force-create "$wiki_branch" "$base_ref"
seed_wiki . "$runtime_dir/publish"
if [[ "$artifact_changed" == "true" ]]; then
  git apply --check "$patch_path"
  git apply "$patch_path"
fi
git add -A -- docs/wiki
git config user.name "${WIKI_GIT_AUTHOR_NAME:-github-actions[bot]}"
git config user.email "${WIKI_GIT_AUTHOR_EMAIL:-41898282+github-actions[bot]@users.noreply.github.com}"
if git diff --cached --quiet; then
  git commit --allow-empty \
    -m "docs(wiki): compile ${source_key#github:}" \
    -m "$managed_commit_marker
LLM-Wiki-Source: $source_key"
else
  git commit \
    -m "docs(wiki): compile ${source_key#github:}" \
    -m "$managed_commit_marker
LLM-Wiki-Source: $source_key"
fi

if [[ -n "$remote_oid" ]]; then
  git push origin "HEAD:refs/heads/${wiki_branch}" \
    "--force-with-lease=refs/heads/${wiki_branch}:${remote_oid}"
else
  git push origin "HEAD:refs/heads/${wiki_branch}"
fi

if git diff --quiet "$base_ref" HEAD -- docs/wiki; then
  echo "publish-pr: source cursor persisted; no Wiki PR is needed"
  exit 0
fi

{
  echo "$managed_marker"
  echo
  echo "## LLM Wiki update"
  echo
  echo "- Source: \`$source_key\`"
  echo "- Generated by the repository-local LLM Wiki workflow"
  echo "- Review the Wiki changes and any Drift sections before merging"
} > "$body_file"

if [[ "$pr_count" == "1" ]]; then
  "$gh_bin" pr edit "$pr_number" --title "docs(wiki): update compiled knowledge" \
    --body-file "$body_file"
else
  "$gh_bin" pr create --base "$base_branch" --head "$wiki_branch" \
    --title "docs(wiki): update compiled knowledge" --body-file "$body_file"
fi
