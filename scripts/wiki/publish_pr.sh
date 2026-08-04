#!/usr/bin/env bash
set -euo pipefail

managed_marker='<!-- llm-wiki:managed-pr -->'
managed_commit_marker='LLM-Wiki-Managed: true'

if [[ $# -ne 5 ]]; then
  echo "usage: publish_pr.sh <patch-path> <metadata-path> <base-branch> <wiki-branch> <source-key>" >&2
  exit 2
fi

patch_path=$1
metadata_path=$2
base_branch=$3
wiki_branch=$4
source_key=$5
python_bin=${PYTHON_BIN:-python3}
gh_bin=${GH_BIN:-gh}
validator=${WIKI_VALIDATOR:-scripts/wiki/validate_changes.py}

if [[ ! -f "$patch_path" || ! -f "$metadata_path" ]]; then
  echo "publish-pr: patch artifact is incomplete" >&2
  exit 2
fi
if ! git check-ref-format --branch "$base_branch" >/dev/null 2>&1; then
  echo "publish-pr: invalid base branch" >&2
  exit 2
fi
if ! git check-ref-format --branch "$wiki_branch" >/dev/null 2>&1; then
  echo "publish-pr: invalid wiki branch" >&2
  exit 2
fi
if [[ ! "$source_key" =~ ^github:[0-9a-f]{40}$ ]]; then
  echo "publish-pr: invalid source key" >&2
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
required = {"version", "changed", "bytes", "sha256", "base_ref"}
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
print(("true" if metadata["changed"] else "false") + "\t" + base_ref)
PY
)
IFS=$'\t' read -r artifact_changed artifact_base_ref <<< "$metadata_values"

if [[ "$artifact_changed" == "false" ]]; then
  echo "publish-pr: Wiki patch is empty; nothing to publish"
  exit 0
fi
if [[ -z ${GH_TOKEN:-} ]]; then
  echo "publish-pr: GH_TOKEN is required" >&2
  exit 2
fi
if [[ ! -x "$validator" ]]; then
  echo "publish-pr: validator is not executable" >&2
  exit 2
fi
if [[ -n $(git status --porcelain) ]]; then
  echo "publish-pr: checkout must be clean before applying the artifact" >&2
  exit 2
fi

git fetch --no-tags origin "+refs/heads/${base_branch}:refs/remotes/origin/${base_branch}"
base_sha=$(git rev-parse "refs/remotes/origin/${base_branch}^{commit}")
artifact_base_sha=$(git rev-parse "${artifact_base_ref}^{commit}" 2>/dev/null || true)
if [[ "$artifact_base_sha" != "$base_sha" ]]; then
  echo "publish-pr: main moved after ingest; a newer run must rebuild the patch" >&2
  exit 1
fi

remote_oid=$(git ls-remote --heads origin "refs/heads/${wiki_branch}" | awk 'NR == 1 {print $1}')
prs_file=$(mktemp "${TMPDIR:-/tmp}/llm-wiki-prs.XXXXXX")
report_file=$(mktemp "${TMPDIR:-/tmp}/llm-wiki-validation.XXXXXX")
body_file=$(mktemp "${TMPDIR:-/tmp}/llm-wiki-body.XXXXXX")
cleanup() {
  rm -f "$prs_file" "$report_file" "$body_file"
}
trap cleanup EXIT

"$gh_bin" pr list \
  --head "$wiki_branch" \
  --base "$base_branch" \
  --state open \
  --json number,body \
  --limit 2 > "$prs_file"

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

if [[ -n "$remote_oid" ]]; then
  git fetch --no-tags origin "+refs/heads/${wiki_branch}:refs/remotes/origin/${wiki_branch}"
  if ! git log -1 --format=%B "refs/remotes/origin/${wiki_branch}" | grep -Fq "$managed_commit_marker"; then
    echo "publish-pr: existing wiki branch is not managed by LLM Wiki" >&2
    exit 1
  fi
  if [[ "$pr_count" == "1" && "$pr_managed" != "true" ]]; then
    echo "publish-pr: existing Wiki PR is not managed by LLM Wiki" >&2
    exit 1
  fi
elif [[ "$pr_count" == "1" ]]; then
  echo "publish-pr: open Wiki PR has no matching remote branch" >&2
  exit 1
fi

git switch --force-create "$wiki_branch" "refs/remotes/origin/${base_branch}"
if ! git apply --check "$patch_path"; then
  echo "publish-pr: patch does not apply cleanly to latest main" >&2
  exit 1
fi
git apply "$patch_path"

if ! "$python_bin" "$validator" \
  --repo . \
  --base-ref "refs/remotes/origin/${base_branch}" \
  --source-key "$source_key" \
  --report "$report_file" >/dev/null; then
  echo "publish-pr: patch failed deterministic validation" >&2
  exit 1
fi

git config user.name "${WIKI_GIT_AUTHOR_NAME:-github-actions[bot]}"
git config user.email "${WIKI_GIT_AUTHOR_EMAIL:-41898282+github-actions[bot]@users.noreply.github.com}"
git add -A -- docs/wiki
if git diff --cached --quiet; then
  echo "publish-pr: patch produced no Wiki changes"
  exit 0
fi
git commit \
  -m "docs(wiki): compile ${source_key#github:}" \
  -m "$managed_commit_marker"

if [[ -n "$remote_oid" ]]; then
  git push origin "HEAD:refs/heads/${wiki_branch}" \
    "--force-with-lease=refs/heads/${wiki_branch}:${remote_oid}"
else
  git push origin "HEAD:refs/heads/${wiki_branch}"
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
  "$gh_bin" pr edit "$pr_number" \
    --title "docs(wiki): update compiled knowledge" \
    --body-file "$body_file"
else
  "$gh_bin" pr create \
    --base "$base_branch" \
    --head "$wiki_branch" \
    --title "docs(wiki): update compiled knowledge" \
    --body-file "$body_file"
fi
