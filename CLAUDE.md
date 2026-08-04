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

- `<repository-relative-path>` at `<40-character-source-commit>` — <what this proves>

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
- When they disagree, document current behavior and record the mismatch under `## Drift`; never silently modify the Spec.
- When evidence is incomplete or contradictory, write `Unverified` and state what remains unknown.
- Do not infer operational guarantees, downstream contracts, or failure behavior without evidence.

## No-change rule

If the merged change contains no durable knowledge, make no Wiki edits. A successful ingest may intentionally produce an empty patch.

