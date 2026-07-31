Goal1: DONE 2026-07-31. Promoted to `docs/plans/plan-3-geography-and-marts.md`
and executed there; see `docs/dev-notes/2026-07-31.md` for what happened and
ADR-5 through ADR-8 for the decisions it forced. Kept below as written, since
the plan text is what the ADRs argue against.

Two things in it turned out not to be possible as specified. The DuckDB h3
extension could not be used, because BigQuery has no H3 support at all and
ADR-1 requires both targets to compile, so cells are computed in Python
(ADR-5). And rates per parcel or per street mile need a dataset that is not in
scope, so the marts normalise by residents, housing units, businesses and area
instead (ADR-7).

The film locations question resolved in the affirmative: it does carry usable
coordinates on 2,127 of 2,214 rows, so no geocoding decision was needed.

Read first: CLAUDE.md, docs/decisions/0002 and 0003.

Build in this order:

1. Add these DataSF sources to ingestion/datasets.py and model them through
   staging: registered business locations, analysis neighborhoods (polygons),
   supervisor districts (polygons), and street tree list. Add ACS block group
   population as a dbt seed or a small fetch script; it is the denominator and
   is not optional.

2. Geometry handling. Point datasets: parse lat/lon, drop or flag rows with
   missing or obviously bad coordinates, and record the drop rate in the
   freshness mart. Polygon datasets: store GeoJSON alongside a precomputed
   set of covering H3 cells so polygon membership becomes an integer join
   rather than a geometry operation.
   Note: verify whether the film locations dataset actually carries usable
   coordinates. If it does not, geocoding is a separate decision, so raise it
   and write an ADR rather than silently guessing.

3. H3 columns at resolutions 8, 9, and 10 on every point table, computed in
   the transform layer. Prefer the DuckDB h3 community extension; fall back to
   the Python h3 package during load if the extension complicates the
   BigQuery target. Whichever you choose, record it in an ADR.

4. Marts:
   - mart_activity_by_h3: counts by dataset, category, and month per H3 cell
   - mart_activity_by_neighborhood: same, plus rates per 1000 residents and
     per parcel or per street mile where a sensible denominator exists
   - mart_film_locations: the demo dataset, joined to neighborhood
   - one non-spatial mart from city budget if it survives ADR 0003
   Raw counts alone mostly render population density, so every count mart
   must expose a normalized companion measure.

5. Publish: a script that exports marts to partitioned Parquet under a
   published/ prefix with a manifest file (dataset, path, row count, byte
   size, schema hash, generated_at). Target Cloudflare R2 or GCS, but keep the
   local path working so nothing is blocked on a bucket existing.

Testing: an accepted-range test on latitude and longitude, a relationship test
from each point table to the neighborhood dimension, and a test asserting that
H3 cell membership agrees with exact point-in-polygon on a sampled set.

Done when: "count 311 cases inside this neighborhood" resolves through H3
integer predicates with no geometry engine at query time. Dev note plus ADR
updates.

----

Goal2: emit a versioned, model-agnostic context artifact that lets any capable
LLM query this warehouse correctly, and that tells it what it must refuse.

Read first: CLAUDE.md, the dbt manifest.json and catalog.json in dbt/target/,
and mart_pipeline_freshness.

Build in this order:

1. Write the spec first, at docs/specs/context-pack.md, before any code. A
   pack contains:
   - identity: what this warehouse is, publisher, license, jurisdiction,
     update cadence, pack version and generated_at
   - per model: grain statement, column semantics (units, enum meanings, what
     null means, where two similar columns differ), and row count
   - join map: keys, cardinality, and known dirty joins
   - freshness block: per source last load, staleness flag, expected cadence
   - known traps: prose warnings that cannot be derived from schema, for
     example late-arriving records making recent periods undercount, or
     addresses geocoded to block centroid rather than parcel
   - verified examples: natural language question paired with SQL that has
     been executed successfully against the current warehouse
   - refusal boundaries: an explicit list of question shapes this data cannot
     answer, with the reason. This is the part that makes it a trust artifact
     rather than a README, so give it real thought.
   - integrity: a schema hash so a consumer can detect pack/warehouse drift

2. Build the generator: tools/context_pack/. It reads dbt artifacts, runs
   profiling queries (cardinality, null rate, min/max, top values for low
   cardinality columns), merges a hand-maintained YAML of traps and refusal
   boundaries, executes every candidate example query to verify it, and emits
   two artifacts: context_pack.json (complete) and context_pack.md (compact,
   token-budgeted, suitable for direct prompt injection). Make the markdown
   budget configurable and report the token estimate.

3. Fail the build if any example query errors or if the schema hash does not
   match the live warehouse.

4. Wire generation into CI so the pack regenerates on every model change and
   drift shows up as a diff in the pull request.

Testing: round-trip test on the JSON, a test that the compact markdown stays
under budget, and a test that a deliberately stale pack is rejected.

Done when: `make context-pack` produces both artifacts and CI catches drift.
Write the ADR covering pack format decisions and what you chose to leave out.

---
