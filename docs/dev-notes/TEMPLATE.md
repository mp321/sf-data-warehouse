# YYYY-MM-DD

Copy to `YYYY-MM-DD.md`. If a file for today exists, append to it under a new
`## ` heading rather than creating a second file. Never rewrite history in an
existing note: if you were wrong, add a dated correction below.

Dev notes have no frontmatter. The filename is the date.

## Scope

(One or two lines. What this session set out to do, and what was explicitly
out of scope.)

## Changes

(Files touched and why, grouped by area rather than chronology. Enough that
`git diff` is legible to someone who was not here.)

## Findings

(The section that makes these notes worth keeping. Wrong assumptions,
misleading names, code that did not do what it said, undocumented
dependencies. Specific enough to be useful, blunt enough to be true.)

## Not done

(Deliberate omissions and why, so the next session does not read them as
oversights.)

## Verification

(What was actually run, against what versions, with the results.)

## Follow-ups

(What a future session should pick up. If it is architectural, write an ADR
and link it. If it is a body of work, write a plan and link that.)
