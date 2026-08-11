# docs

## Where things stand

| Folder | What is in it |
|---|---|
| `decisions/` | 11 active ADRs, plus five superseded ones that stay: ADR-14, 16 and 17 beside ADR-18 while the consolidation is new, and ADR-2 and ADR-7 because `prose.yml` cites them and the resolver only looks here |
| `plans/` | a template. All nine plans are done and archived |
| `specs/` | `context-pack.md`, the contract the generated pack is built against |
| `dev-notes/` | 2026-08-09, 2026-08-10, and `ARCHIVE-2026-07.md` for everything before them |
| `archive/` | superseded ADR-3 and ADR-4, all nine closed plans, and an index of what each was and what replaced it |

| ADR | Status |
|---|---|
| ADR-1 warehouse targets | active |
| ADR-2 spatial strategy | superseded by ADR-6. Stays here: `prose.yml` cites it |
| ADR-5 H3 computation | active, amended by ADR-10 and ADR-11 |
| ADR-7 dataset scope, second pass | superseded by ADR-10. Stays here: `prose.yml` cites it |
| ADR-6 polygon membership | active |
| ADR-8 published exports | active, amended by ADR-12 |
| ADR-9 cloud raw zone | active |
| ADR-10 narrowed scope | active |
| ADR-11 derived zone code stamp | active |
| ADR-12 published export layout | active |
| ADR-13 context pack format | active |
| ADR-14 raw zone retention | superseded by ADR-18 |
| ADR-15 bigquery pack declared, not generated | active, amends the context-pack spec |
| ADR-16 cut datasets leave the zone | superseded by ADR-18 |
| ADR-17 scheduled retention proof | superseded by ADR-18 |
| ADR-18 the raw zone | active. Supersedes ADR-4, 14, 16 and 17 |

**ADR-18 is the fifth-amendment rule firing, which is worth recording because
the rule was written here before it was needed.** ADR-4 stated the raw zone's
append-only rule, ADR-14 added the second exception, ADR-16 the third, and
ADR-17 moved half of ADR-14's answer onto a schedule. That is four documents
plus an amendment note to answer "may this file be deleted", and the note below
said the answer to a fifth was not a sixth document but one ADR that supersedes
the lot and restates the rule in one place. ADR-18 is that ADR, written
2026-08-10. **The count starts again from it**: the next change to the raw zone's
rules is an ordinary amendment of ADR-18.

**Amended rather than superseded, and the distinction is still load bearing.**
ADR-10 changed one line of ADR-5, the H3 resolution list, and ADR-11 changed
what a re-run of the spatial step recomputes; ADR-5's actual decision, that
cells are computed in Python and stored as BIGINTs because BigQuery has no H3
function, is still a hard constraint. ADR-12 reverses one bullet of ADR-8, the
month partitioning, and leaves the other eight standing. Filing either under
history would mean the next reader skips a live rule. If a future ADR changes
only part of another, say so in the new ADR, add a note at the top of the old
one pointing at it, and leave the old one active.

**ADR-9 is the one to read beside ADR-18 and is not superseded by it.** It owns
where the files physically are and how BigQuery reads them. Its own first
paragraph says ADR-4 is otherwise the description of the zone; read that as
pointing at ADR-18, which restates ADR-4's layout, all-STRING contract, run
manifests and watermark.

**Everything in `docs/` is one of the five kinds below.** The two files that
were not, the 2026-07-31 outside review and `handoff-prompt.md`, were deleted on
2026-08-07, which is the condition each of them named for its own deletion.
There is no standing handoff document: a session that needs one writes it, and
the session that consumes it deletes it.

## The five kinds of document

| Folder | Filename | What it is | Mutable? |
|---|---|---|---|
| `plans/` | `plan-<n>-<slug>.md` | Forward-looking. What we intend to do and in what order. | Yes, until `status: done` |
| `decisions/` | `adr-<n>-<slug>.md` | An ADR. One architectural decision, its tradeoffs and consequences. | No, once accepted |
| `dev-notes/` | `YYYY-MM-DD.md` | Append-only session log. What actually happened. | Append only |
| `specs/` | `<slug>.md` | The contract a generated artifact is built against. | Yes |
| `archive/` | as they were | Superseded ADRs and closed plans, moved rather than deleted. | No |

A spec is not an ADR and the difference is worth stating, since both are
normative. An ADR records one decision, its alternatives and its consequences,
and it is immutable because the record of what we believed is the point. A spec
describes a thing that is still being built and is amended when the thing has to
change; the decision behind it, and what it left out, is what the ADR is for.

**Archiving is a move and not a rewrite.** A superseded ADR and a closed plan go
to `archive/` with their text untouched, because git history is not a browsing
interface. Dev notes are the exception in form only: `ARCHIVE-2026-07.md` folds
nine notes into one file, preserving every finding verbatim under a heading per
date and dropping the chronology around it. Anything in a folded note that was
still true about running code went to `CLAUDE.md` instead.

## Why plans and dev notes are separate

A plan is intent, a dev note is incident. Folding what happened into the plan
makes it unexecutable: a reader six months later cannot tell which lines are
still instructions and which are history.

## Numbering and referencing

Numbers are allocated in order and never reused, including across the archive:
ADR-4 is in `archive/` and no future ADR may be number 4. Refer to documents in
prose as `ADR-1` and `PLAN-2`, and in `related:` frontmatter by full slug, for
example `adr-1-warehouse-targets`. The prefix is what keeps ADR-1 and PLAN-1
from being confused.

`TEMPLATE.md` in each folder is the template and carries no number.

## Changing an accepted ADR

You do not. Write a new ADR that supersedes it:

1. Create `docs/decisions/adr-<n>-<slug>.md` with `related: [<old-slug>]`.
2. In the old ADR, change only `status:` to `superseded` and add the new ADR to
   its `related` list. Leave the decision text alone.
3. Move it to `archive/` once nobody is checking the new one against it.

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
