# docs

## Where things stand

| Folder | What is in it |
|---|---|
| `decisions/` | 13 active ADRs, ADR-22 in draft, plus ADR-2 and ADR-7, superseded but kept here because `prose.yml` cites them and the citation resolver looks only here |
| `plans/` | the template only. All ten plans are done and archived |
| `specs/` | `context-pack.md`, the contract the generated packs are built against |
| `dev-notes/` | dated session notes from 2026-08-09, plus `ARCHIVE-2026-07.md` for everything before |
| `archive/` | superseded ADRs (3, 4, 14, 16, 17, 20), the ten closed plans, and an index of what replaced each |

| ADR | Status |
|---|---|
| ADR-1 warehouse targets | active |
| ADR-2 spatial strategy | superseded by ADR-6; stays because `prose.yml` cites it |
| ADR-5 H3 computation | active, amended by ADR-10 and ADR-11 |
| ADR-6 polygon membership | active |
| ADR-7 dataset scope, second pass | superseded by ADR-10; stays because `prose.yml` cites it |
| ADR-8 published exports | active, amended by ADR-12 |
| ADR-9 cloud raw zone | active |
| ADR-10 narrowed scope | active |
| ADR-11 derived zone code stamp | active |
| ADR-12 published export layout | active |
| ADR-13 context pack format | active |
| ADR-15 BigQuery pack declared, not generated | active, amends the context-pack spec |
| ADR-18 the raw zone | active, amended by ADR-19 and ADR-21. Supersedes ADR-4, 14, 16 and 17 |
| ADR-19 withdrawn registrations | active, amends ADR-18's first revisit clause |
| ADR-21 withdrawn keys are ledgered, then prunable | active, amends ADR-18 section 4. Replaces ADR-20, refused |
| ADR-22 311 load-stamp re-sends | draft, not accepted. Would amend ADR-18 |

## Conventions that are easy to get wrong

**Amending is not superseding.** ADR-10 changed one line of ADR-5, the H3
resolution list, and ADR-5's core decision is still a hard constraint. ADR-12
reversed one bullet of ADR-8 and left eight standing. When a new ADR changes
part of an old one, say so in the new ADR, add a note at the top of the old
one, and leave the old one active. Filing it under history would make the next
reader skip a live rule.

**A fifth amendment means consolidating instead.** ADR-18 replaced four documents that
together answered "may this file be deleted" (ADR-4, 14, 16, 17). Amendments
are counted again from ADR-18: ADR-19 and ADR-21 are two of four, and a fifth
means writing a new consolidation instead.

**ADR-9 is read beside ADR-18, not replaced by it.** It owns where the files
physically are and how BigQuery reads them. Its pointer to ADR-4 now means
ADR-18, which restates ADR-4's layout, contract, manifests and watermark.

**There is no standing handoff document.** A session that needs one writes it,
and the session that consumes it deletes it.

## Document kinds

| Folder | Filename | What it is | Mutable? |
|---|---|---|---|
| `plans/` | `plan-<n>-<slug>.md` | Intent: what we will do, in what order | Yes, until `status: done` |
| `decisions/` | `adr-<n>-<slug>.md` | One architectural decision, its alternatives and consequences | No, once accepted |
| `dev-notes/` | `YYYY-MM-DD.md` | Session log: what happened | Append only |
| `specs/` | `<slug>.md` | The contract a generated artifact is built against | Yes |
| `archive/` | as they were | Superseded ADRs and closed plans | No |

A spec and an ADR are both normative but differ in kind. An ADR records one
decision and is immutable, because the record of what we believed is the
point. A spec describes something still being built and is amended when that
thing has to change.

**Plans and dev notes stay separate.** A plan is intent and a dev note is
incident. Folding what happened into the plan makes it unexecutable: a later
reader cannot tell which lines are still instructions.

**Archiving is a move, not a rewrite.** Superseded ADRs and closed plans go to
`archive/` with their text untouched. Dev notes are folded instead:
`ARCHIVE-2026-07.md` keeps every finding verbatim under a heading per date,
and anything still true about running code went to `CLAUDE.md`.

## Numbering and referencing

Numbers are allocated in order and never reused, across the archive too. Refer
to documents in prose as `ADR-1` and `PLAN-2`, and in `related:` frontmatter by
full slug, for example `adr-1-warehouse-targets`. `TEMPLATE.md` in each folder
is the template and carries no number.

## Changing an accepted ADR

You do not. Write a new one that supersedes it:

1. Create `docs/decisions/adr-<n>-<slug>.md` with `related: [<old-slug>]`.
2. In the old ADR, change only `status:` to `superseded` and add the new ADR to
   its `related` list. Leave the decision text alone.
3. Move it to `archive/` once nobody is checking the new one against it,
   unless `prose.yml` cites it.

## Frontmatter

Plans and ADRs carry:

```yaml
---
status: draft | active | done | superseded
date: YYYY-MM-DD
related: []
---
```

Dev notes carry none. The filename is the date.

## Style

Concise. One or two sentences per point unless the nuance is load bearing.
Tables over prose for reference material. No emojis, no em or en dashes.
