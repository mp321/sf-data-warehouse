# docs

Three kinds of document live here, deliberately separate.

| Folder | Filename | What it is | Mutable? |
|---|---|---|---|
| `plans/` | `plan-<n>-<slug>.md` | Forward-looking. What we intend to do and in what order. | Yes, until `status: done` |
| `decisions/` | `adr-<n>-<slug>.md` | An ADR. One architectural decision, its tradeoffs and consequences. | No, once accepted |
| `dev-notes/` | `YYYY-MM-DD.md` | Append-only session log. What actually happened. | Append only |

## Why plans and dev notes are separate

A plan is intent, a dev note is incident. Folding what happened into the plan
makes it unexecutable: a reader six months later cannot tell which lines are
still instructions and which are history.

## Numbering and referencing

Numbers are allocated in order and never reused. Refer to documents in prose
as `ADR-1` and `PLAN-2`, and in `related:` frontmatter by full slug, for
example `adr-1-warehouse-targets`. The prefix is what keeps ADR-1 and PLAN-1
from being confused.

`TEMPLATE.md` in each folder is the template and carries no number.

## Changing an accepted ADR

You do not. Write a new ADR that supersedes it:

1. Create `docs/decisions/adr-<n>-<slug>.md` with `related: [<old-slug>]`.
2. In the old ADR, change only `status:` to `superseded` and add the new ADR
   to its `related` list. Leave the decision text alone.

The record of what we believed and why is worth more than a tidy file.

## Frontmatter

Plans and ADRs carry:

```yaml
---
status: draft | active | done | superseded
date: YYYY-MM-DD
related: []
---
```

Dev notes carry no frontmatter. The filename is the date.

## Style

Concise. One or two sentences per point unless the nuance is load bearing. No
emojis, no em or en dashes.
