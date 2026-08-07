---
status: active
date: 2026-08-07
related: [plan-6-context-pack, adr-13-context-pack-format, adr-1-warehouse-targets, adr-6-polygon-membership, adr-8-published-exports, adr-10-narrowed-scope, adr-12-published-export-layout]
---

# Spec. The context pack

The artifact PLAN-6 commissions: a versioned, model-agnostic description of this
warehouse that lets a capable LLM query it correctly, and that tells it what it
must refuse to answer.

This document is the contract. It was written before the generator, which is
PLAN-6 step 2, and the generator is verified against it rather than the other
way round. It is mutable: unlike an ADR it describes a thing that is still being
built, so amend it when the artifact has to change and say in the dev note why.

**Amended 2026-08-07 by PLAN-6 step 4, in three places, all recorded in ADR-13.**
Section 9 now renders the traps block into the compact markdown and never drops
it, where the first reading of that section omitted it. Section 2's table said
CI generates two of the packs; CI checks one and generates none, and the table
says so. Section 8 gains the rule about which warehouse the check runs against.
The `date` above is what the pack records as `spec_version`, so amending this
document makes every committed pack stale until it is regenerated. That is the
mechanism working: `make context-pack-check` exits 3 until someone re-reads the
contract against the artifact.

**The refusal boundaries are the reason this artifact exists.** Everything else
here is a schema dump with provenance, and a schema dump is a README. Sections 5
and 6 are the part that is not, and they are the part to read if you read one
part.

## 1. What the pack is, and is not

**Is.** One file a consumer can put in front of a model, after which the model
can write correct SQL against this warehouse, knows what the numbers mean, knows
how stale they are, and knows which questions to decline.

**Is not.** A consumer. PLAN-6 puts every reader of the pack out of scope on
purpose: building the artifact and building something that reads it are
different projects, and doing both at once means neither constrains the other
honestly. Nothing in this spec may be justified by "the consumer will need it".

**Is not.** A substitute for the repo. `CLAUDE.md` and the ADRs remain the
account of why this warehouse is the way it is. The pack states what is true and
cites where the reasoning lives; it does not reproduce it.

## 2. Targets, and why there are three packs

PLAN-6 asks whether the pack describes the DuckDB warehouse, the BigQuery one or
the published Parquet, and notes that a consumer reading the bucket is reading
none of the three.

**A pack describes exactly one target and names it in its identity block.**
Three targets, three self-contained artifacts, one generator, one hand-written
source. A pack that hedges across surfaces is a pack that is wrong about
whichever one the reader is holding, and the failure mode is not cosmetic: a
model told about `stg_spatial__polygon_h3` while reading six Parquet files will
write a join to a table that is not there.

| Target | Models | Freshness from | Examples verified against | Credentials | Generated | Checked in CI |
|---|---|---|---|---|---|---|
| `duckdb` | all 19 | last `make build` | the DuckDB file | none | by hand, after `make build` | yes, section 8 |
| `published` | the 6 marts in `PUBLISHED_MARTS` | `published/manifest.json` | DuckDB over `published/*.parquet` | none | by hand, after `make publish` | yes, once it exists |
| `bigquery` | all 19 | last `make build-bigquery` | BigQuery | yes | by hand | no, it has no schema hash |

**Generation is by hand and the check is CI's**, which is the 2026-08-07
amendment and is argued in ADR-13. A pack generated in CI would be generated
from the fixture warehouse, so its row counts, profiles and example results
would describe a seven-row test zone rather than the warehouse a consumer
reads.

What follows from that:

- **The published pack is not a shortened warehouse pack.** It has no staging
  models, so the three-flag trap on the H3 bridge does not apply to it and must
  not be rendered into it; several joins in the join map do not exist there; and
  questions that are answerable in the warehouse are refusals in that pack, with
  "this is in the warehouse and not in this export" as the reason. Generating it
  separately is what lets it say that.
- **Two of the three build with no credentials**, because DuckDB reads Parquet
  directly and CI already writes `published/` on every pull request (ADR-8).
  That is what keeps PLAN-6 step 4 honest: those two can be checked against a
  warehouse a fork built from fixtures, with no secrets and no bucket. Amended
  2026-08-07: they are checked there, not generated there. See section 8.
- **The BigQuery pack is the one that will rot.** It needs credentials and is
  produced by hand beside `make build-bigquery`. The spec does not pretend
  otherwise; it gives that pack a staleness guard instead. See section 8.
- **Three artifacts is three chances for the prose to drift, and the mitigation
  is structural.** The traps, refusals and disclosures have one copy, in one
  YAML (section 7). Every entry declares which targets it applies to, and the
  generator renders only the entries whose citations resolve against that
  target's model set. Copies exist at render time and never at source.

### The schema hash, and where it stops

`publish/export.py` already computes a schema hash over column names, types and
ordinal position. PLAN-6 says reuse it rather than invent a second, and this spec
does, unmodified.

It hashes `name:type` pairs rendered with **DuckDB** type names, so it carries
across the `duckdb` and `published` packs, the published Parquet having been
written from a DuckDB build by that same script. It does not carry into the
`bigquery` pack, where `VARCHAR` is `STRING` and every hash would differ for a
schema that is identical. The BigQuery pack therefore states that the hash is
deliberately absent and names `make parity-columns` as the guarantee in its
place. An absent field with a reason is a fact; an omitted one is a bug waiting
to be reported.

## 3. Artifact shape

Two files per target, written under `context-pack/`:

```
context-pack/context_pack.duckdb.json      complete
context-pack/context_pack.duckdb.md        compact, token budgeted
context-pack/context_pack.published.json
context-pack/context_pack.published.md
context-pack/context_pack.bigquery.json    when someone runs it with credentials
context-pack/context_pack.bigquery.md
```

The JSON is the artifact. The markdown is a rendering of it for direct prompt
injection and carries strictly less; nothing may appear in the markdown that is
not in the JSON.

Top-level keys, fixed here:

| Key | What it is |
|---|---|
| `spec_version` | the version of this document the pack was built against |
| `pack_version` | semver of the pack's own shape, bumped by hand on a breaking change |
| `target` | `duckdb`, `published` or `bigquery`. Never absent, never plural |
| `generated_at` | UTC ISO 8601 |
| `prose_revision` | hash of the hand-maintained YAML, identical across every pack in one generation |
| `identity` | section 4.1 |
| `build` | section 4.2 |
| `models` | section 4.3 |
| `joins` | section 4.4 |
| `freshness` | section 4.5 |
| `traps` | section 4.6 |
| `refusals` | section 5 |
| `disclosures` | section 6 |
| `examples` | section 4.7 |
| `integrity` | section 8 |

`prose_revision` is what lets a consumer holding two packs tell whether they came
from one source. It is the cheapest available answer to the drift that three
artifacts invite, and it costs one hash.

## 4. Content blocks

### 4.1 identity

Derived from the repo and the published manifest, not hand-written twice.
`name`, `description` (one sentence), `publisher`, `jurisdiction`
(San Francisco, California), `licence` (public domain; source data from DataSF
and the US Census Bureau, the string `publish/export.py` already writes),
`source_urls`, `update_cadence` per source, and `repo`.

### 4.2 build

What produced the numbers in this pack: the dbt manifest's `invocation_id` and
generated timestamp, the dbt and adapter versions, the dataset registry as read
from `vars.pipeline_sources`, the H3 resolutions in use, and for `duckdb` the
warehouse path, for `published` the `manifest_version` and `generated_at` from
`published/manifest.json`, for `bigquery` the project and dataset.

### 4.3 models

One entry per model the target holds, in dependency order.

- `name`, `layer` (staging, intermediate, mart), `materialisation`
- `grain`: the "one row per ..." sentence, taken from the model's own
  description. Every model in this project has one because CLAUDE.md requires
  it, so this field is derived and not written twice. **A model with no grain
  sentence fails generation** rather than emitting an empty field.
- `row_count`, measured at generation time
- `columns`: name, type, description from the yml, and a profile

The profile is stated as rules rather than as a schema, because the right
statistics depend on the column:

| Column shape | What the profile carries |
|---|---|
| any | null rate |
| low cardinality (at or under 50 distinct) | every distinct value with its share |
| high cardinality string | distinct count, and 5 example values |
| numeric | min, max, median |
| date or timestamp | min, max, and the count in the newest complete month |
| boolean | true share |
| H3 cell BIGINT | distinct count only. Example values are noise: a BIGINT cell id tells a reader nothing and costs tokens |

Enum meanings, unit statements and the difference between two similar columns
come from the yml descriptions, which is where they already are. Where a
description does not say what a null means and the null rate is above zero, the
generator emits a warning naming the column. That warning is a to-do list for
the yml, not a field in the pack.

### 4.4 joins

One entry per join a consumer might write: `from`, `to`, `on`, `cardinality`,
and `safe` with a reason where it is not. This block is where the bridge's three
flags are explained for the targets that have a bridge, because the flags are a
property of a join rather than of a column.

Dirty joins are named as such. `mart_film_locations.upstream_analysis_neighborhood`
against `dim_neighborhood.analysis_neighborhood` is the worked example: it will
mostly match and it is not the computed column, so joining on it silently
answers a different question.

### 4.5 freshness

Per source: `last_load_at`, `last_run_finished_at`, `stale_after_hours`,
`is_stale`, `row_count`, and the tier. This is `mart_pipeline_freshness`
projected into the pack, not a second calculation.

For the `published` target, freshness is the publish time from
`published/manifest.json` and not the build time, and the pack says which. They
are different questions and the gap between them has been days.

### 4.6 traps

Prose warnings that are true of the data and are not refusals, because they do
not make a question unanswerable. Same object shape as a disclosure (section 6)
without the trigger condition. Kept as a separate block from refusals so that
the refusal list stays short enough to be read.

A trap is an unconditional disclosure, and section 9 renders it for that reason.
A disclosure fires when its `when` holds; a trap has no `when` because it always
holds. Rendering the conditional warning and withholding the unconditional one
would be the wrong way round.

### 4.7 examples

A natural-language question paired with SQL that has been executed successfully.

```yaml
id: ex.reports-per-capita-by-neighborhood
question: Which neighborhoods report the most street cleaning per resident?
sql: |
  ...
demonstrates: [refuse.rank-by-raw-count]
verified:
  target: duckdb
  at: 2026-08-05T12:00:00Z
  rows: 41
  sql_sha256: ...
```

Three rules, and each one exists because the alternative is an example that
lies:

1. **An example is verified against the target whose pack it appears in, and
   nowhere else.** The same question against the published Parquet is a
   different query over a different model set. Inheriting the DuckDB SQL would be
   exactly the unverified example PLAN-6 says is worse than none.
2. **`sql_sha256` covers the SQL as executed.** Editing the SQL without
   re-executing changes the hash and fails the build. This is the only mechanism
   that stops an example rotting quietly, since a wrong query usually still runs.
3. **Every class 3 refusal (section 5) has at least one example demonstrating
   its substitute.** That is what makes the examples a consequence of the
   refusals rather than a wishlist beside them.

## 5. Refusal boundaries

An explicit list of question shapes this data cannot answer, with the reason.

### 5.1 Why the list is sorted

Three kinds of refusal fail in different ways, and a flat list conflates them. A
model reading a flat list treats the weak entries as licence to discount the
strong ones, because "no budget data here" and "311 does not measure what you
think" cannot both be worth the same weight. Each refusal declares its class.

| Class | The situation | What is refused |
|---|---|---|
| `absent` | the data is not here | the question |
| `mismeasured` | a query returns a number and the number does not mean what the question assumes | the question |
| `misnormalised` | the measure is right and the arithmetic invites a wrong conclusion | the form of the answer, not the question |

A fourth thing is not a refusal at all: an answerable question carrying a
bounded, measured error the consumer must be told about. Those are section 6.
Filing them here would be crying wolf, and a refusal list that cries wolf is
ignored whole.

### 5.2 The object

```yaml
id: refuse.311-measures-reporting-not-incidence
class: mismeasured
applies_to: [duckdb, bigquery, published]
question_shapes:
  - which neighborhood has the most problems
  - where are the worst conditions in the city
  - which part of San Francisco is dirtiest
rule: >
  Do not answer questions about where conditions are worst using 311 volume.
  Report what was reported, and say so in the answer.
why: >
  311 counts reports, not conditions, and reporting propensity varies with who
  lives somewhere.
evidence:
  - {kind: adr, ref: adr-10}
  - {kind: model, ref: mart_activity_by_neighborhood}
instead:
  answer: >
    Which neighborhoods file the most 311 reports of a given category per
    resident or per business, labelled as reports rather than as conditions.
  example: ex.reports-per-capita-by-neighborhood
```

Four things about this shape are load bearing:

- **`question_shapes` are literal paraphrases, several of them.** Matching a
  question against a list is what a model actually does, and one canonical
  phrasing will not match the phrasing a user chose.
- **`rule` is an imperative addressed to the answering model.** Not prose about
  the data. A declarative caveat is the first thing summarised away when context
  gets tight; an instruction survives, because it reads as something to comply
  with rather than something to know.
- **`instead` is mandatory.** A refusal with no substitute is a dead end, and a
  model that wants to be useful will route around a dead end. Naming the
  legitimate adjacent answer is what makes the refusal cheap to obey. Every
  refusal in this pack names one, even where the substitute is only "say what
  this warehouse does hold".
- **`evidence` must resolve.** Every citation names a model, a column, an ADR or
  a dated measurement that exists in the target being generated, and generation
  fails if one does not. This is what keeps the section from becoming filler
  under maintenance: an entry about `h3_r9` or `street_trees` would fail the
  build rather than sit there reading plausibly. It also makes `applies_to`
  self-checking, since an entry claiming the published target while citing a
  staging model cannot resolve.

**Evidence is measured or it says it is not.** A citation of the form
"measured 2026-07-31, ADR-6" and a citation of the form "not measured in this
project" are both acceptable. A confident number with no source is not. The
undercount entry in 5.5 is the current example of the second kind, and it is
better as an honest unmeasured warning than as a fabricated percentage.

### 5.3 The one sentence most of class 2 comes from

ADR-10 cut this project to seven datasets and every one is spatial. It also, as
a side effect nobody wrote down at the time, made one sentence true of the whole
warehouse:

> Every dataset here is an administrative record of an interaction with the
> city: a service request, a permit application, a business tax registration, a
> film permit. Not one of them is a survey, a census or a measurement of a
> condition. **This warehouse contains no ground-truth measure of the underlying
> state of anything.**

The pack states that sentence once, at the top of the refusal block, and the
class 2 entries cite it. It is worth its own place because it generalises past
the enumerated cases: a question about what is happening in the city, as opposed
to what was reported to the city, is out of scope whether or not it appears in
the list below.

The exception, and it must be stated beside the sentence or the sentence is
wrong: `census_block_groups` is a census, of population and housing units, in
April 2020. It is a denominator and never a subject, and section 6 covers what
it costs.

### 5.4 Class 1, absent

Cheap and uninteresting, and they must be enumerated anyway, because they are
the commonest question shapes a city warehouse gets asked.

| Asked about | Status |
|---|---|
| City spending, budgets, department costs | Ingested once and cut with `city_budget` (ADR-10). The join anyone wants, spending against 311 demand, needs a crosswalk between budget department codes and 311 `agency_responsible`, two independently maintained taxonomies with no reason to agree |
| Crime, police incidents, arrests | Never here |
| Housing prices, rents, evictions, homelessness counts | Never here |
| Transit, traffic, collisions | Never here |
| Street trees | Cut with ADR-10 |
| Distance, travel time, routing, "within 500 m of", buffers, nearest | No geometry at query time (ADR-6). Boundary membership is a precomputed column and cell coverage is an integer join; there is no geometry engine in this warehouse to ask, on either engine, and ADR-2 said so first |
| Rates per parcel or per street mile | Neither dataset is in scope. This is a recorded gap, not an oversight (ADR-7) |
| Anything before 2024 from 311 or permits | `start_date` in the registry. See 5.5 |

The `absent` entries carry the same `instead` obligation as every other class:
name what the warehouse does hold that is nearest. "No crime data" plus "311
service requests by category and neighborhood exist" is a usable answer. "No
crime data" alone invites the substitution 5.5 exists to prevent.

### 5.5 Class 2, mismeasured

These are the ones an LLM will answer happily and wrongly, so they get the most
care.

**`refuse.311-measures-reporting-not-incidence`.** "Which neighborhood has the
most problems." 311 volume measures where people report, not where problems are.
Reporting propensity varies with who lives somewhere, whether they know the
service exists, language, housing tenure, and whether one prolific reporter is
active in a block. The warehouse holds no independent measure of incidence to
calibrate against, so the direction and size of that bias cannot be estimated
here, only named. Instead: which neighborhood reports the most of a category,
per capita or per business, labelled as reports.

**`refuse.311-is-not-a-safety-measure`.** "Which neighborhood is most
dangerous." This one fails twice and both failures must be stated. There is no
crime data (class 1), and 311 is not a proxy for it (class 2). A pack that
states only the first invites the model to substitute the second, which is the
exact failure mode the two-part statement exists to close.

**`refuse.permits-are-filings-not-construction`.** "How much construction is
happening in X." `int_point_activity` dates a permit at `filed_at`, because
filing is the demand signal and issuing is the city's response to it, and mixing
them makes a permitting backlog look like a drop in construction. A filed permit
may never be issued and an issued one may never be built. Separately,
`permit_record_id` is not `permit_number`: revisions and addenda file as
separate records under one permit, up to about 100, so counting records is not
counting permits. Instead: filings per month by permit type, or query
`stg_datasf__building_permits` directly on `issued_at` and count distinct
`permit_number`, which is a different query the activity marts do not do.

**`refuse.business-registry-is-not-a-business-count`.** "How many businesses are
in the Mission." `business_locations` is a tax certificate registry. Its grain
is one row per certificate, location and ownership sequence, so a business that
moves or changes hands accumulates rows; `is_active` means "no end date" and is
roughly a third of rows; and about 18 percent of rows carry coordinates outside
San Francisco, which is correct data about businesses registered here and
located elsewhere. Instead: count distinct `certificate_number`, and say whether
you mean ever registered or currently active. `dim_neighborhood` carries
`business_count` and `active_business_count` for exactly this reason and they
differ by more than half, so which one a rate divides by changes the answer.

**`refuse.business-history-is-a-current-state-snapshot`.** A monthly series of
`business_locations` back through the twentieth century is the set of locations
the registry holds **today**, dated by when each opened. It is not a record of
what existed at the time. `location_started_at` spans 1849 to 2028 and the mart
spreads its rows over 874 distinct months; the far end of that range is thin and
selected by whatever the city still keeps. Instead: use it as a denominator and
as a recent series, and do not read a long trend off it.

**`refuse.newest-month-is-partial`.** Records arrive and are revised after the
event, so the most recent month in any monthly series is incomplete and a trend
line ending at today always slopes down. **This project has not measured the
arrival lag**, and the pack says so rather than inventing a number. Instead:
end every series at the last complete month, and read the cutoff from
`mart_pipeline_freshness.last_load_at` rather than from the calendar.

**`refuse.no-cross-dataset-series-before-2024`.** `311_cases` and
`building_permits` have `start_date` 2024-01-01 in the registry;
`business_locations` backfills fully. So a cross-dataset monthly series spanning
2024-01-01 shows one dataset before that date and three after, and the step is
an artefact of the backfill window rather than anything that happened in the
city. This one is derivable: the generator reads `start_date` per source and
asserts the boundary rather than trusting the prose to stay current. Instead:
either start the window at 2024-01-01 or hold the dataset constant.

**`refuse.supervisor-district-lines-moved-in-2022`.** Every point staging model
carries both `upstream_supervisor_district`, stamped by DataSF when the row was
published, and `supervisor_district_id`, computed here against the 2022
boundaries. They disagree on rows published before the 2022 redistricting, and
neither is wrong: they answer "which district was this in at the time" and
"which district is this in now". Instead: join on `supervisor_district_id`,
which is what `dim_supervisor_district` keys on, and say which question you are
answering when the window reaches before 2022.

### 5.6 Class 3, misnormalised

The measure is right. The arithmetic is the problem, so these refuse a form of
answer rather than a question, and each one has a verified example showing the
form that works.

**`refuse.rank-by-raw-count`.** Do not rank neighborhoods or cells by
`event_count`. A raw count per area is mostly a map of where people live, so the
ranking rediscovers the census. Rank by a rate, name the denominator, and note
that the denominators disagree with each other on purpose: per 1000 residents
and per 1000 businesses return different lists, and that disagreement is
information. The Financial District is the worked case, with almost no residents
and enormous daytime activity, so its per-capita street cleaning rate is close to
meaningless and its per-business rate is not. This constraint is not an opinion
of the pack's; CLAUDE.md requires every count mart to expose a normalised
companion for the same reason.

**`refuse.events-per-sq-km-on-the-h3-mart`.** Do not present
`mart_activity_by_h3.events_per_sq_km` as a second measure that agrees with the
count. Every cell at a fixed resolution has the same area, so it is
`event_count` times a constant and ranks identically by construction. It exists
to be comparable with the neighborhood mart, where areas genuinely differ, and
for no other reason.

**`refuse.per-capita-divides-by-april-2020`.** Population is the 2020 Decennial
count, because the ACS API now requires a key and ADR-1 keeps credentials off
the ingestion path. Every per-capita rate in this warehouse divides 2024 to 2026
events by an April 2020 denominator, so no change in a rate over time can be
attributed to population change, and neighborhoods that have grown or shrunk
since 2020 are systematically off. Instead: state the denominator's vintage in
any answer that uses a rate, and prefer per 1000 businesses where the question
is commercial.

**`refuse.null-rate-is-not-a-low-rate`.** `events_per_1000_residents` is null,
not zero, where the denominator is zero. That is correct and common: the bay,
the Presidio and the Financial District all have real activity and close to
nobody living in them. A neighborhood with no residents does not have an
infinite complaint rate, it has a question that does not apply. Instead: exclude
nulls explicitly and say you did, and never answer "which area has the lowest
rate" without saying how many were excluded.

### 5.7 The closed-world rule

A finite list of refusals against an infinite space of questions is a blacklist,
and every blacklist is escapable. The pack closes it with one rule, stated in the
markdown immediately after the list:

> If answering a question requires a column that is not in this pack, a join that
> is not in the join map, or a dataset not listed in the identity block, refuse
> and name what is missing. Do not infer a column from a table name, and do not
> assume a column exists because it usually does.

That sentence converts the enumeration from a blacklist into the edge of a
whitelist, and it is what makes the artifact a trust artifact rather than a list
of gotchas. It is also the only part of section 5 that stays correct when the
warehouse changes without the prose changing.

## 6. Mandatory disclosures

Answerable, with a bounded and measured error the consumer has to be told rather
than left to discover. Same object as a refusal, with `question_shapes` replaced
by `when` (the condition that triggers the disclosure) and `instead` replaced by
`state` (the sentence the answer must carry).

**`disclose.h3-mart-neighborhood-labels-are-approximate`.** This is the one the
pack exists to say out loud, and it is sharper than "boundary membership has
error", because it names which mart and which number.

Point-level membership is exact. ADR-6 moved refinement from query time to
precompute time, so `analysis_neighborhood` on a point staging model and
`mart_activity_by_neighborhood` built from it are exact point-in-polygon
answers, not approximations, and `assert_point_boundary_is_exact.sql` fails on a
single disagreement.

`mart_activity_by_h3` is different. It labels each cell with the neighborhood
that owns the cell (`is_primary` on the bridge), at r8, where one hexagon is
about 460 m across and 0.737 sq km. ADR-6 measured cell-based membership against
exact point-in-polygon on 2026-07-31, 10,000 sampled points per boundary set:

| Boundary set | r8 | r10 |
|---|---|---|
| analysis_neighborhood | 72.6% | 94.7% |
| supervisor_district | 83.6% | 96.8% |

So filtering or summing `mart_activity_by_h3` by `analysis_neighborhood` is not
the same query as `mart_activity_by_neighborhood`, and at r8 it disagrees with
exact membership for something over a quarter of points near neighborhood edges.
The error is not noise: it correlates with geography, so every neighborhood
comparison built that way inherits a bias. **When both marts can answer a
question, use the neighborhood mart.** Use the H3 mart for the map and for
within-cell work.

**`disclose.the-two-marts-have-different-totals`.** They also differ for a second
and unrelated reason. `mart_activity_by_neighborhood` excludes events outside
every neighborhood rather than bucketing them into an Unknown row, so its total
is lower than `int_point_activity`'s and is exactly the sum of its neighborhoods.
State which universe a total is over.

**`disclose.coordinate-drop-rates`.** Rows with no usable coordinate are kept in
`int_point_activity` with null geography, so they exist in the staging models and
vanish from any grouping by geography. Measured 2026-07-31, as the share of each
source that could not be placed on a map: 311 1.20 percent, building permits 0.12,
film locations 3.93, business locations 18.27. The last is high because the
registry records businesses located outside San Francisco, which is correct data.
Any total by neighborhood is short by roughly these amounts and the answer should
say so.

**`disclose.population-is-interpolated-twice`.** The Census publishes population
by block group, block groups do not nest inside neighborhoods, and rather than
clip polygons this project sums population over the H3 cells each neighborhood
owns. So residents are assumed uniform within a block group, and a boundary
cell's residents all go to whichever boundary owns the cell. Good to about a
percent at r10, and not a census count. In the `duckdb` and `bigquery` packs
only, because the published pack has no bridge to get wrong: use
`is_allocation_cell` and never `is_primary` to spread a measure across cells.
`is_primary` keeps one boundary per cell, and 653,000 of 874,000 San Franciscans
disappeared the once this was got wrong.

**`disclose.areas-are-spherical-and-two-are-enormous`.** Areas come from
`ingestion/geometry.py` by spherical excess, exact on a sphere and therefore off
by the Earth's flattening, about 0.3 percent, which is far below the uncertainty
in what a boundary means. The part that matters more: Supervisorial District 4
reaches the Farallon Islands 43 km offshore and covers 261 sq km, and block group
060759804011 is 248 sq km of ocean. So "which district is least dense" is
answerable, and the answer is District 4 for a reason that has nothing to do with
how many people live there.

**`disclose.freshness-tests-are-one-run-behind`.** The test columns on
`mart_pipeline_freshness` describe the previous completed dbt run, not the run
that built the table, because dbt writes run results after models finish. Any
answer about test results from this mart is one run stale, and the mart's own
description says so.

## 7. The hand-maintained source

Everything in sections 5 and 6, plus the traps block and the candidate examples,
lives in **one** YAML: `tools/context_pack/prose.yml`. Nothing that can be
derived from the dbt manifest, the catalog, the registry or the published
manifest may appear in it, and anything derivable found there is a bug rather
than a preference. PLAN-6 makes this a constraint and the reason is the one this
repo has already paid for twice: two copies of a fact with nothing checking they
agree is how a fact goes stale.

Every entry carries `applies_to`. Generation renders an entry into a target's
pack only when every citation in its `evidence` resolves against that target's
model set, and fails when an entry claims a target where they do not. That is
one rule doing two jobs: it keeps the prose honest about the warehouse, and it
keeps `applies_to` honest about the target.

## 8. Integrity and staleness

The `integrity` block carries, per model in the target: the schema hash from
`publish/export.py`, the row count, and the dbt manifest identity from
section 4.2. For the `published` target it also carries the `manifest_version`
and the per-dataset `schema_hash` copied from `published/manifest.json`, which is
the authority on the export and not something the pack recomputes.

Three rules, in increasing order of how much they matter:

1. **Generation fails if any example query errors.** PLAN-6 step 3.
2. **Generation fails if a schema hash does not match the live target.** PLAN-6
   step 3.
3. **A pack refuses on its own behalf.** The pack states, in its own text, that a
   consumer must compare its `integrity` block against the target before trusting
   it, and refuse everything if they disagree. For the published target that
   means comparing against `published/manifest.json` in the bucket. This is not
   hypothetical: the bucket currently holds the hand upload of 2026-08-01 in the
   pre-ADR-12 partitioned layout at `manifest_version` 1, so a pack generated
   today against a local `published/` describes something the bucket does not
   contain.

The BigQuery pack, being hand-generated, additionally records the dbt manifest it
was built from, and its self-refusal fires when that manifest no longer matches
the repo. It has no schema hash, for the reason in section 2.

### Which warehouse the check runs against

Added 2026-08-07 by PLAN-6 step 4 and decided in ADR-13.

**CI checks the committed pack against the fixture warehouse, and never
generates one.** The check compares four things and nothing else: the target
name, the `prose_revision`, the `spec_version` and the per-model schema hash. The
first three are file reads and are as true on a runner as anywhere. The fourth is
the only one that has to come from a warehouse, and a schema hash is over column
names, types and ordinal position, none of which depend on how many rows a
fixture holds. Measured 2026-08-07: all 19 hashes from the fixture warehouse are
identical to the ones the real warehouse produced.

Row counts, profiles, freshness and example results are not compared, and they
are the only parts a fixture zone would get wrong. That is the whole of why this
works.

**A fixture hash that disagrees is a real failure and not a false alarm.** It
means a model's shape depends on its data, and a pack cannot describe such a
model honestly for any warehouse but the one it was generated from.

## 9. Rendering the compact markdown

Order, which is a design decision and not a formatting one:

1. identity and target, in three lines
2. how to read this pack, including the closed-world rule
3. **refusals**
4. **disclosures**
5. **traps**
6. models and columns
7. join map
8. examples
9. freshness
10. integrity

Refusals come before the schema because a model that has read the schema has
already begun composing SQL, and a constraint that arrives after a draft exists
is a constraint that has to overturn something rather than shape it. Traps are
in the list for the same reason and were not in the first version of it: the
first reading of this section left them in the JSON only, on the ground that a
trap does not make a question unanswerable. Amended 2026-08-07, ADR-13. A trap
does not stop a query being written, it changes the query that gets written, and
it has to arrive before the schema to do that.

The markdown is budgeted, the budget is configurable, and the generator reports
the estimated token count. **Under budget pressure the generator drops, in this
order:** examples beyond the one required per class 3 refusal, then column
descriptions for columns with no yml description, then profile statistics, then
low-signal columns. It reports what it dropped.

**Refusals, disclosures, traps and grain sentences are never dropped.** If the
pack does not fit the budget with all four present, generation fails rather than
emitting a truncated pack. A pack missing a refusal is worse than no pack,
because it reads complete.

Traps join that list rather than the ladder because the ladder sheds detail from
the models block, and a trap is not detail: it is 585 estimated tokens against a
models block of about 16,000, and it is worth more than the profile statistics
the ladder drops two stages earlier. A rendering that has dropped every
statistic and kept every trap is the right shape for a prompt; the reverse is
not.

## 10. Left out on purpose

For the ADR that closes PLAN-6, which is asked to record specifically what was
left out.

- **No consumer, and no evaluation of whether models honour the refusals.** The
  refusal format here is designed against how models fail, not measured against
  it. Measuring it needs a consumer, and PLAN-6 puts that out of scope.
- **No arrival-lag measurement**, so `refuse.newest-month-is-partial` is the one
  entry with an unmeasured warning. It is derivable later by comparing row counts
  by month across two builds, which nothing in this project stores yet.
- **No per-dataset licence.** One licence statement covers the pack, matching
  what `publish/export.py` already writes, although DataSF and the Census are
  different publishers under the same public-domain terms.
- **No natural-language-to-SQL grammar, no semantic layer, no metric
  definitions.** The pack describes models and refusals. A metrics layer is a
  different artifact and would need its own decision.
- **No cross-target diff.** Three packs can disagree, `prose_revision` makes that
  detectable, and nothing reconciles them automatically.
- **The `bigquery` pack is not built in CI** and will therefore be stale more
  often than not. That is the cost of ADR-1's credential-free CI gate, paid here
  the same way it is paid everywhere else in this project.
