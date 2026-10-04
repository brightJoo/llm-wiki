# LLM Wiki compiler policy

This repository uses Claude Code as a compiler from verified repository changes to a persistent Markdown Wiki. These rules apply whenever the ingest prompt is running.

## Ownership boundary

- Treat Git commits, source code, tests, and approved documents as evidence.
- Treat `docs/specs/**` as human-owned intent. Read it when relevant; never edit it.
- Propose Wiki knowledge only in:
  - `docs/wiki/index.md`
  - `docs/wiki/log.md`
  - `docs/wiki/topics/**/*.md`
- Never edit source code, tests, configuration, workflows, scripts, `CLAUDE.md`, or files outside `docs/wiki/**`.
- Do not create symlinks.

## Retrieval order

1. Read `.llm-wiki/runtime/context.json` and `.llm-wiki/runtime/changes.diff`.
2. Read `docs/wiki/index.md` when it exists.
3. Open only the Topics likely to be affected.
4. Inspect the changed source, tests, and listed Spec candidates needed to verify the behavior.
5. Search more broadly only when the current evidence cannot answer the question.

Prefer updating an existing Topic. Create a new Topic only when the knowledge answers an independent question and cannot fit an existing Topic without mixing responsibilities.

## Topic structure

Use this shape for every Topic:

```markdown
# <Topic title>

<One-paragraph summary of what this Topic explains.>

## Scope

<What is included and excluded.>

## Current behavior

<Verified behavior, expressed in repository/domain language.>

## Drift

<Differences between implementation and Spec/LLD, or `None observed`. Use `Unverified` when evidence is insufficient.>

## Sources

- `<repository-relative-path>` at `<40-character-source-commit>`
  <symbol and what this proves>

## Related topics

- [<Topic>](<relative-topic-link>)
```

Every added or modified Topic must contain `## Sources` and the exact `head_sha` from the runtime context. A source entry must say what the referenced path proves. Do not present guesses as current behavior.

## Topic growth

Start at `docs/wiki/topics/<topic>.md`. When it becomes too large, keep that representative file as a stable hub and split independent details into `docs/wiki/topics/<topic>/<subtopic>.md`. Link the children from the hub so all Topics remain reachable from `index.md`.

## Index and log

- `docs/wiki/index.md` is a navigation page: Topic link plus one-line summary. Do not duplicate Topic bodies there.
- Every Topic must be reachable from `index.md`, directly or through a parent Topic.
- `docs/wiki/log.md` is append-only. Never rewrite, reorder, or delete existing bytes.
- When Wiki content changes, append one entry containing the exact runtime `source_key`, changed Topics, and drift status.
- Never append the same source key twice.

Use this log shape:

```markdown
## <UTC timestamp> — `<source-key>`

- Topics: <links or `None`>
- Drift: <summary or `None observed`>
```

## Evidence and drift

- Code and tests describe the current implementation.
- Spec and LLD describe approved intent.
- Each changed Topic must cite evidence as `- \`<repository-path>\` at \`<40-character-commit-sha>\`` inside `## Sources`.
- When they disagree, document current behavior and record the mismatch under `## Drift`; never silently modify the Spec.
- When evidence is incomplete or contradictory, write `Unverified` and state what remains unknown.
- Do not infer operational guarantees, downstream contracts, or failure behavior without evidence.

## No-change rule

If the merged change contains no durable knowledge, make no Wiki edits. A successful ingest may intentionally produce an empty patch.

## Domain classification (only when context has `domain_profile`)

The committed `.llm-wiki/domain.json` is human-owned. Never modify it or invent undeclared scope IDs.

1. Read the engine-owned `topic_inventory_path` and selected `topic_candidates`. Metadata-free legacy Topics remain candidates. A truncated candidate list is not the whole Wiki: search further where essential.
2. Classify by the question and ownership of behavior: screens own composition, modules own contracts, policies own business conditions, mechanisms own technical behavior. Prefer an existing question/scope over one document per diff or method.
3. Before claiming shared policy, inspect inputs, conditions, outputs, real call paths, caller filters, later sorting/filtering, service/version/flag/config branches. A shared helper proves shared implementation, not identical final screen behavior.
4. Keep service scopes separate. Use scope-specific conditions inside one policy when parameters differ; propose a separate `exception_of` policy for an independently meaningful exception. Do not widen scope when callers or external configuration are unverified. Explicit `null` means unknown, never all services/screens.
5. Check incoming `applies_policy`, `uses_module`, `exception_of` and `depends_on` relationships. Explain why each direct dependent is updated or unchanged. `related_to` is navigation, not causal impact.
6. Describe current behavior at the fixed final `head_sha`. Intermediate changes and reversals are context, not the current implementation. A rename updates evidence and preserves Topic ID/path; deletion requires verification of remaining callers and retirement, not file removal.
7. Convert relevant legacy Topics when edited. Preserve unrelated legacy documents. Preserve every existing ID and path. Proposed merges, new boundaries and scope expansions use `propose_structure`; they may not silently mutate existing metadata.

Add `## Topic metadata` with exactly one fenced `json` object containing `schema_version` (integer 1), stable `id`, declared `type`, independent `question`, `scope`, `lifecycle` (`active` or `retired`) and `relations` (array of `{type,target}`). Each relation needs an actual body link to the same target document. `exception_of` must be acyclic; it describes a relationship, not executable inheritance.

Add `## Verification` to describe confirmed code behavior, relevant tests (and whether actually run), unknown operational configuration and undocumented surfaces. One updated Topic does not make the whole Wiki current. Preserve existing Scope/Current behavior/Drift/Sources/Related topics sections. A missing approved intent document means comparison is unverified.

Return the classification as the final **exact JSON object**, or the runtime's `structured_output` object. Do not create/edit runtime report files. Required report fields:

- `schema_version: 1`, exact `source_key`, exact `head_sha`, `coverage: "complete"` or `"incomplete"`.
- `decisions`: array with `question`, `topic_id`, `action` (`update`, `create`, `unchanged`, `retire`, `propose_structure`), `candidates` (existing IDs or legacy paths), `rationale`, `scope`, `covered_changes` (current Git change paths), `evidence` (`{path,symbol,sha}`), `checked_dependents` (`{topic_id,action,reason}`; action update/unchanged/retire), `unknowns` (text array).
- `ignored_changes`: `{path,reason}` array for source changes with no durable knowledge. Every non-Wiki changed path must appear in a decision or here. Multiple decisions can share a source change.

The question and scope match the generated Topic. Creation explains why existing candidates do not fit. Evidence uses the fixed head and a repository path (including a proven removal in this batch). Empty document patches still require a complete report and explicit path accounting. If essential changed behavior cannot be read or verified, return `coverage: "incomplete"`; publishing and cursor advancement will fail. Complete coverage is input review, not human semantic approval. Without a domain profile, preserve the existing general Topic/output flow.
