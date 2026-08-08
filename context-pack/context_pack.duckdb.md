# sf-data-warehouse context pack, target duckdb

An analytics warehouse over seven public San Francisco datasets, modelled with dbt into staging views, one intermediate model and six marts, in which every geography is precomputed rather than computed at query time.
Target `duckdb`, 19 models, generated 2026-08-08T03:09:56+00:00, prose revision `64e423921de52c85`, spec 2026-08-07, pack 1.0.0.
Publisher DataSF and the US Census Bureau, modelled here, jurisdiction San Francisco, California. Public domain. Source data from DataSF (data.sfgov.org) and the US Census Bureau.

## How to read this pack

Read the refusals and the disclosures before the schema. They are ordered first on purpose: a model that has read the schema has already begun composing SQL, and a constraint that arrives after a draft exists has to overturn something rather than shape it. The refusals are instructions to you, not notes about the data. Each one names the answer to give instead, and giving that answer is how you obey it.

> If answering a question requires a column that is not in this pack, a join that is not in the join map, or a dataset not listed in the identity block, refuse and name what is missing. Do not infer a column from a table name, and do not assume a column exists because it usually does.

## Refusals

Every dataset here is an administrative record of an interaction with the city: a service request, a permit application, a business tax registration, a film permit. Not one of them is a survey, a census or a measurement of a condition. THIS WAREHOUSE CONTAINS NO GROUND-TRUTH MEASURE OF THE UNDERLYING STATE OF ANYTHING. A question about what is happening in the city, as opposed to what was reported to the city, is out of scope whether or not it appears in the list below.

The exception is census_block_groups, which is a census, of population and housing units, in April 2020. It is a denominator and never a subject, and the disclosures say what it costs.

### refuse.no-city-spending-data (absent)

- "how much does the city spend on street cleaning"
- "which department has the biggest budget"
- "what does it cost to respond to a 311 request"
- "is spending going where the complaints are"

**Rule.** Do not answer questions about city spending, budgets or department costs. There is no budget data in this warehouse.

Why: city_budget was ingested once and cut by ADR-10. The join anyone wants, spending against 311 demand, needs a crosswalk between budget department codes and the agencies 311 routes work to, two independently maintained taxonomies with no reason to agree.
Instead: How many requests were reported, by category, neighborhood and month. That is a count of demand on the city, and it says nothing about what any of it cost.
Evidence: ADR-10; column mart_activity_by_neighborhood.category

### refuse.no-crime-data (absent)

- "where is crime highest in San Francisco"
- "how many arrests were there in the Mission"
- "which police district has the most incidents"

**Rule.** Do not answer questions about crime, police incidents or arrests. That data has never been in this warehouse. Do not substitute 311 volume for it; see refuse.311-is-not-a-safety-measure.

Why: No crime dataset is in the registry, and the closed-world rule applies: there is no column to compute one from.
Instead: 311 service requests by category and neighborhood, which is what residents reported to the city, labelled as reports.
Evidence: ADR-10; model mart_activity_by_neighborhood

### refuse.no-housing-market-data (absent)

- "what are rents in the Mission"
- "how many evictions happened last year"
- "how many people are homeless in San Francisco"
- "are housing prices rising"

**Rule.** Do not answer questions about housing prices, rents, evictions or homelessness counts. None of it is here.

Why: Never in the registry. The only housing measure in this warehouse is the 2020 Census count of housing units, which is a stock of dwellings and not a market.
Instead: Housing units per neighborhood from the 2020 Census, and building permit filings by type over time, both labelled for what they are.
Evidence: column dim_neighborhood.housing_units; ADR-10

### refuse.no-transit-or-collision-data (absent)

- "which intersections have the most collisions"
- "how many people ride Muni"
- "where is traffic worst"

**Rule.** Do not answer questions about transit, traffic or collisions. None of it is here.

Why: Never in the registry.
Instead: 311 street and sidewalk categories, which record what was reported about the street rather than what happened on it.
Evidence: ADR-10; model mart_activity_by_neighborhood

### refuse.no-street-trees (absent)

- "how many street trees are there"
- "which neighborhood has the most tree canopy"
- "where were trees planted last year"

**Rule.** Do not answer questions about street trees. The dataset was cut.

Why: street_trees was ingested and then cut by ADR-10, along with city_budget, when the project narrowed to seven datasets that are all spatial.
Instead: 311 has a Tree Maintenance service category, which is requests about trees and not an inventory of them.
Evidence: ADR-10

### refuse.no-distance-or-routing (absent)

- "how many permits are within 500 metres of this address"
- "what is the nearest neighborhood to this point"
- "how far is it from here to there"
- "draw a buffer around this location"

**Rule.** Do not answer questions requiring distance, travel time, routing, buffers or nearest-neighbour search. There is no geometry engine in this warehouse to ask, on either engine.

Why: No geometry at query time (ADR-6, and ADR-2 said it first). Boundary membership is a precomputed column and cell coverage is an integer join. No model carries a geometry type, no ST_ function exists here, and no spatial extension is loaded.
Instead: Exact membership questions, which are answerable: which neighborhood or supervisor district a row is in, since that is a precomputed column rather than a computation. An H3 cell at resolution 8 is about 460 metres across, so "in the same cell" is the nearest available thing to "nearby" and should be named as such rather than converted to metres.
Evidence: ADR-6; ADR-2; column mart_activity_by_h3.h3_cell

### refuse.no-parcel-or-street-mile-rates (absent)

- "how many 311 cases per parcel"
- "complaints per street mile"
- "requests per lane kilometre"

**Rule.** Do not answer questions normalised by parcels or street mileage. Neither dataset is in scope, and this is a recorded gap rather than an oversight.

Why: The plan that commissioned the dimension tables asked for rates per parcel and per street mile. Neither dataset was brought into scope, so what is here instead is residents, housing units, land area and registered businesses. ADR-7 records the gap.
Instead: The four denominators that do exist on dim_neighborhood: population, housing units, registered businesses and land area. Say which one the rate divides by.
Evidence: ADR-7; model dim_neighborhood

### refuse.no-311-or-permits-before-2024 (absent)

- "how did 311 volume change between 2019 and 2024"
- "what did permit filings look like a decade ago"
- "show me the ten year trend"

**Rule.** Do not answer questions about 311 cases or building permits before the backfill boundary in the dataset registry. Those rows were never ingested, so a query returns nothing rather than zero, and an empty result is not evidence of a quiet year.

Why: The registry sets a start_date per source and the two large event datasets start at the same boundary. Widening it is an ingestion run, not a query.
Instead: A window starting at the registry's start_date, stated in the answer. business_locations backfills fully and can be read further back, with the caveat in refuse.business-history-is-a-current-state-snapshot.
Evidence: registry 311_cases.start_date = 2024-01-01T00:00:00.000Z; registry building_permits.start_date = 2024-01-01T00:00:00.000Z; model mart_pipeline_freshness

### refuse.311-measures-reporting-not-incidence (mismeasured)

- "which neighborhood has the most problems"
- "where are the worst conditions in the city"
- "which part of San Francisco is dirtiest"
- "where is graffiti worst"
- "which neighborhood is most neglected"

**Rule.** Do not answer questions about where conditions are worst using 311 volume. Report what was reported, and say so in the answer.

Why: 311 counts reports, not conditions, and reporting propensity varies with who lives somewhere, whether they know the service exists, language, housing tenure, and whether one prolific reporter is active in a block. This warehouse holds no independent measure of incidence to calibrate against, so the direction and size of that bias can be named here and not estimated.
Instead: Which neighborhoods file the most 311 reports of a given category per resident or per business, labelled as reports rather than as conditions. See example `ex.reports-per-capita-by-neighborhood`.
Evidence: ADR-10; model mart_activity_by_neighborhood; the size of the reporting bias (not measured in this project)

### refuse.311-is-not-a-safety-measure (mismeasured)

- "which neighborhood is most dangerous"
- "where is it least safe to live"
- "rank neighborhoods by safety"

**Rule.** Refuse this twice and say both halves. There is no crime data in this warehouse, and 311 volume is not a proxy for it. Do not answer a safety question from any column here.

Why: This fails on two independent grounds, and a pack that states only the first invites the substitution the second exists to prevent: with no crime data available, a model looking for the nearest number reaches for 311 volume, which measures who reports rather than what happens.
Instead: Say that neither crime data nor any measure of conditions is present, and offer 311 reports by category per capita as a description of what residents reported, with the label attached. See example `ex.reports-per-capita-by-neighborhood`.
Evidence: ADR-10; model mart_activity_by_neighborhood

### refuse.permits-are-filings-not-construction (mismeasured)

- "how much construction is happening in the Mission"
- "is building activity going up"
- "how many buildings went up last year"
- "how many permits were issued in this neighborhood"

**Rule.** Do not answer questions about construction from the activity marts. They date a permit at filing, not at issue, and count records rather than permits. Answer about filings, or query the staging model directly and say which question you answered.

Why: int_point_activity dates a permit at filed_at, because filing is the demand signal and issuing is the city's response to it, and mixing them makes a permitting backlog look like a drop in construction. A filed permit may never be issued and an issued one may never be built. Separately, permit_record_id is not permit_number: revisions and addenda file as separate records under one permit, up to about 100, so counting records is not counting permits.
Instead: Filings per month by permit type, labelled as filings. For issued permits, query stg_datasf__building_permits on issued_at and count distinct permit_number, which is a different query the activity marts do not do. See example `ex.permit-filings-per-month-by-type`.
Evidence: column stg_datasf__building_permits.filed_at; column stg_datasf__building_permits.issued_at; column stg_datasf__building_permits.permit_number; model int_point_activity

### refuse.business-registry-is-not-a-business-count (mismeasured)

- "how many businesses are in the Mission"
- "which neighborhood has the most restaurants"
- "how many businesses opened last year"

**Rule.** Do not count rows of the business registry and call the result a number of businesses. Count distinct certificate numbers, and say whether you mean ever registered or currently active.

Why: business_locations is a tax certificate registry. Its grain is one row per certificate, location and ownership sequence, so a business that moves or changes hands accumulates rows; is_active means "no end date"; and a substantial share of rows carry coordinates outside San Francisco, which is correct data about businesses registered here and located elsewhere.
Instead: Count distinct certificate_number and state which universe you counted. dim_neighborhood carries business_count and active_business_count for exactly this reason and they differ by more than half, so which one a rate divides by changes the answer. See example `ex.distinct-businesses-by-neighborhood`.
Evidence: column stg_datasf__business_locations.certificate_number; column stg_datasf__business_locations.is_active; column dim_neighborhood.business_count; column dim_neighborhood.active_business_count; coordinates outside San Francisco, 18.27 percent of rows (measured 2026-07-31)

### refuse.business-history-is-a-current-state-snapshot (mismeasured)

- "how many businesses existed in San Francisco in 1950"
- "show business openings since 1900"
- "was the city more commercial before the war"

**Rule.** Do not read a long historical trend off the business registry. It is a snapshot of what the registry holds today, dated by when each location opened, and it is not a record of what existed at the time.

Why: A monthly series of business_locations back through the twentieth century is the set of locations the registry holds now. location_started_at spans 1849 to 2028 and the activity mart spreads those rows over hundreds of distinct months; the far end of that range is thin and selected by whatever the city still keeps.
Instead: Use the registry as a denominator and as a recent series. If a long window is asked for, say that the early years are survivorship rather than history.
Evidence: column stg_datasf__business_locations.location_started_at; column mart_activity_by_neighborhood.event_month; model int_point_activity

### refuse.newest-month-is-partial (mismeasured)

- "is 311 volume falling this month"
- "what is the trend over the last year"
- "how many permits were filed this month"

**Rule.** Never end a monthly series at the current month. End it at the last complete month, and read the cutoff from the freshness block rather than from the calendar.

Why: Records arrive and are revised after the event, so the most recent month in any series is incomplete and a trend line ending at today always slopes down. This project has not measured the arrival lag, so the size of the shortfall is unknown rather than small.
Instead: End the series at the last complete month. Every date column in this pack carries its newest complete month and the row count in it, which is the cutoff to use.
Evidence: column mart_pipeline_freshness.last_load_at; column mart_activity_by_neighborhood.event_month; the arrival lag (not measured in this project)

### refuse.no-cross-dataset-series-before-2024 (mismeasured)

- "plot all activity by month since 2015"
- "compare permits and business openings over the last decade"
- "show total events per month across every dataset"

**Rule.** Do not present a cross-dataset monthly series spanning the registry's backfill boundary. Either start the window at the boundary or hold the dataset constant.

Why: 311_cases and building_permits start at the boundary in the registry and business_locations backfills fully, so a combined series across it shows one dataset before that date and three after. The step is an artefact of the backfill window rather than anything that happened in the city.
Instead: Start the window at the registry's start_date for the latest-starting dataset in the query, or filter to one dataset and say which.
Evidence: registry 311_cases.start_date = 2024-01-01T00:00:00.000Z; registry business_locations.start_date = 1970-01-01T00:00:00.000Z; column mart_activity_by_neighborhood.dataset

### refuse.supervisor-district-lines-moved-in-2022 (mismeasured)

- "which supervisor district had the most 311 cases"
- "compare districts over the last ten years"
- "how has district 6 changed"

**Rule.** Join on the computed supervisor_district_id, not on the upstream column stamped by DataSF, and say which question you are answering when the window reaches before 2022.

Why: Every point staging model carries both an upstream district, stamped when the row was published, and a computed one, decided here against the 2022 boundaries. They disagree on rows published before the 2022 redistricting, and neither is wrong: they answer "which district was this in at the time" and "which district is this in now".
Instead: Join on supervisor_district_id, which is what dim_supervisor_district keys on, and state that districts are on the 2022 lines throughout.
Evidence: column stg_datasf__311_cases.upstream_supervisor_district; column stg_datasf__311_cases.supervisor_district_id; column dim_supervisor_district.supervisor_district_id

### refuse.rank-by-raw-count (misnormalised)

- "which neighborhood has the most 311 cases"
- "rank neighborhoods by number of reports"
- "where is the most activity"
- "which cell has the highest count"

**Rule.** Do not rank neighborhoods or cells by event_count. Rank by a rate, name the denominator, and note that the denominators disagree with each other on purpose.

Why: A raw count per area is mostly a map of where people live, so the ranking rediscovers the census. Per 1000 residents and per 1000 businesses return different lists, and that disagreement is information: the Financial District has almost no residents and enormous daytime activity, so its per-capita rate is close to meaningless and its per-business rate is not. This is not the pack's opinion; CLAUDE.md requires every count mart to expose a normalised companion for the same reason.
Instead: Rank by a rate, name the denominator in the answer, and where the question is commercial prefer per 1000 businesses. See example `ex.reports-per-capita-by-neighborhood`.
Evidence: column mart_activity_by_neighborhood.event_count; column mart_activity_by_neighborhood.events_per_1000_residents; column mart_activity_by_neighborhood.events_per_1000_businesses; doc CLAUDE.md

### refuse.events-per-sq-km-on-the-h3-mart (misnormalised)

- "which cells are densest in events per square kilometre"
- "does the density agree with the count"
- "show both the count and the density per cell"

**Rule.** Do not present mart_activity_by_h3.events_per_sq_km as a second measure that agrees with the count. It is the count times a constant.

Why: Every cell at a fixed resolution has the same area, so events_per_sq_km ranks identically to event_count by construction. It exists to be comparable with the neighborhood mart, where areas genuinely differ, and for no other reason.
Instead: Rank cells by events_per_1000_residents, which is the normalisation that varies per cell, and use events_per_sq_km only when comparing a cell against a neighborhood. See example `ex.h3-cells-ranked-by-rate`.
Evidence: column mart_activity_by_h3.events_per_sq_km; column mart_activity_by_h3.cell_area_sq_km; column mart_activity_by_neighborhood.events_per_sq_km

### refuse.per-capita-divides-by-april-2020 (misnormalised)

- "has the rate per resident changed since 2024"
- "which neighborhood grew fastest"
- "are complaints per capita rising"

**Rule.** State the denominator's vintage in any answer that uses a per-capita rate, and never attribute a change in such a rate to population change.

Why: Population is the 2020 Decennial count, because the ACS API now requires a key and ADR-1 keeps credentials off the ingestion path. Every per-capita rate here divides recent events by an April 2020 denominator, so neighborhoods that have grown or shrunk since then are systematically off and no change in a rate over time can be attributed to population.
Instead: Give the rate with the denominator labelled as the April 2020 Census count, and prefer per 1000 businesses where the question is commercial. See example `ex.rate-with-denominator-vintage`.
Evidence: ADR-1; column dim_neighborhood.population; column mart_activity_by_neighborhood.events_per_1000_residents

### refuse.null-rate-is-not-a-low-rate (misnormalised)

- "which area has the lowest complaint rate"
- "where are the quietest cells"
- "which neighborhood has the fewest reports per resident"

**Rule.** Never answer a lowest-rate question without excluding null rates explicitly and saying how many were excluded.

Why: events_per_1000_residents is null, not zero, where the denominator is zero. That is correct and common: the bay, the Presidio and the Financial District all have real activity and close to nobody living in them. An area with no residents does not have an infinite complaint rate; it has a question that does not apply.
Instead: Exclude the nulls in the query, report how many rows that removed, and say that those areas have no denominator rather than a low rate. See example `ex.lowest-rate-with-exclusions-counted`.
Evidence: column mart_activity_by_h3.events_per_1000_residents; column mart_activity_by_h3.cell_population

## Mandatory disclosures

Answerable, with a bounded error the answer has to carry. When the condition holds, the answer states the sentence.

### disclose.h3-mart-neighborhood-labels-are-approximate

When: Any answer that filters, groups or sums mart_activity_by_h3 by analysis_neighborhood or by supervisor_district_id.
**State.** Cell-based neighborhood labels are approximate. At resolution 8 they agree with exact point-in-polygon membership for 72.6 percent of sampled points for neighborhoods and 83.6 percent for supervisor districts, so this is not the same query as the neighborhood mart. When both marts can answer a question, use mart_activity_by_neighborhood.
Why: Point-level membership is exact: ADR-6 moved refinement from query time to precompute time, so the neighborhood on a point staging model and the mart built from it are exact answers, and a singular test fails on a single disagreement. The H3 mart is different. It labels each cell with the boundary that owns the cell, at a resolution where one hexagon is about 460 metres across, and the disagreement near edges is not noise: it correlates with geography, so every neighborhood comparison built that way inherits a bias.

| boundary_set | r8 | r10 |
|---|---|---|
| analysis_neighborhood | 72.6% | 94.7% |
| supervisor_district | 83.6% | 96.8% |

Evidence: ADR-6; model mart_activity_by_h3; model mart_activity_by_neighborhood; cell membership against exact point-in-polygon, 10,000 sampled points per boundary set (measured 2026-07-31)

### disclose.the-two-marts-have-different-totals

When: Any answer that gives a total from mart_activity_by_neighborhood, or that compares a total from it against one from the H3 mart or from the event spine the two marts are built from.
**State.** State which universe the total is over. mart_activity_by_neighborhood excludes events outside every neighborhood rather than bucketing them into an Unknown row, so its total is lower than the underlying event spine's and is exactly the sum of its neighborhoods.
Why: Two marts over the same events differ for two unrelated reasons: this one, and the cell-labelling error in the disclosure above. Reporting a difference without separating them attributes an exclusion rule to a measurement error.
Evidence: model mart_activity_by_neighborhood; model mart_activity_by_h3; column mart_activity_by_h3.analysis_neighborhood

### disclose.coordinate-drop-rates

When: Any answer that totals or ranks by geography, for any dataset.
**State.** Totals by geography are short by the rows that could not be placed on a map. Measured as the share of each source: 311 1.20 percent, building permits 0.12, film locations 3.93, business locations 18.27. The last is high because the registry records businesses located outside San Francisco, which is correct data rather than dirty data.
Why: Rows with no usable coordinate are kept with null geography upstream, so they exist in the source and vanish from any grouping by geography. A grouped total is therefore not the source's row count, and the gap is large enough on one dataset to change an answer.
Evidence: column mart_pipeline_freshness.coordinate_drop_rate_pct; column mart_pipeline_freshness.point_count; coordinate drop rate per source (measured 2026-07-31)

### disclose.population-is-interpolated-twice

When: Any answer using population, housing_units or a per-capita rate, and any query that spreads a measure across H3 cells.
**State.** Population here is interpolated twice and is not a census count: residents are assumed uniform within a block group, and a boundary cell's residents all go to whichever boundary owns the cell. Good to about a percent at resolution 10. To spread a measure across cells use is_allocation_cell and never is_primary.
Why: The Census publishes population by block group, block groups do not nest inside neighborhoods, and rather than clip polygons this project sums population over the H3 cells each neighborhood owns. is_primary keeps one boundary per cell, so using it to allocate discards the losing boundaries and their residents with them: 653,000 of 874,000 San Franciscans disappeared the once this was got wrong.
Evidence: column stg_spatial__polygon_h3.is_primary; column stg_spatial__polygon_h3.is_allocation_cell; model stg_spatial__h3_population; column dim_neighborhood.population

### disclose.areas-are-spherical-and-two-are-enormous

When: Any answer using area_sq_km, a density, or a comparison of how dense two boundaries are.
**State.** Areas are spherical, so they are off by the Earth's flattening, about 0.3 percent, which is far below the uncertainty in what a boundary means. Two boundaries are legitimately enormous and will dominate any density ranking: Supervisorial District 4 reaches the Farallon Islands 43 km offshore and covers 261 square kilometres, and one census block group is 248 square kilometres of ocean.
Why: Areas come from ingestion/geometry.py by spherical excess. The flattening error is negligible and the two outliers are not: "which district is least dense" is answerable, and the answer is District 4 for a reason that has nothing to do with how many people live there.
Evidence: column dim_supervisor_district.area_sq_km; column dim_neighborhood.area_sq_km; doc ingestion/geometry.py; spherical area error, about 0.3 percent (measured 2026-07-31)

### disclose.freshness-tests-are-one-run-behind

When: Any answer about test results or pipeline health read from mart_pipeline_freshness.
**State.** The test columns describe the previous completed dbt run, not the run that built the table, so any answer about test results from this mart is one run stale.
Why: dbt writes run results after models finish, so a run cannot report its own outcome. On a first ever build the test columns are all zero. This cannot be fixed by reordering.
Evidence: column mart_pipeline_freshness.tests_total; column mart_pipeline_freshness.last_test_run_at; doc dbt/macros/audit_run_results.sql

## Traps

True of the data and not refusals: the question is answerable and the obvious query answers a different one. These always apply, so they carry no condition.

### trap.category-means-something-different-per-dataset

**State.** Group by dataset whenever you group by category, or filter to one dataset first. Never sum a category across datasets.
Why: category is each dataset's own category dimension: service type for 311, permit type for permits, licence description for businesses. The column has one name and three vocabularies, so a group by category alone silently pools three unrelated taxonomies.
Evidence: column mart_activity_by_h3.category; column mart_activity_by_neighborhood.category; column mart_activity_by_neighborhood.dataset

### trap.h3-cells-are-bigints

**State.** H3 cells are BIGINTs here, not the 15-character hexadecimal strings the H3 documentation uses. Compare and join them as integers, and do not expect an h3 function to exist in the warehouse.
Why: Neither engine computes H3; both read precomputed BIGINTs from the derived zone (ADR-5). BigQuery has no H3 function at all, so there is nothing to dispatch to, and a cell id rendered as a string will match nothing.
Evidence: ADR-5; column mart_activity_by_h3.h3_cell; column mart_activity_by_h3.h3_resolution

### trap.null-neighborhood-is-an-answer

**State.** A null analysis_neighborhood means the row is outside every neighborhood, which is a correct answer and not a missing value. It is water, just past the city line, or a row with no usable coordinate, and disclose.coordinate-drop-rates says how many of the last kind there are per source.
Why: Treating it as missing invites an inner join that drops the rows silently, and the drop is not uniform: it falls on the bay, the edges of the city and the registered businesses located elsewhere.
Evidence: column mart_activity_by_h3.analysis_neighborhood; column mart_film_locations.analysis_neighborhood

### trap.staging-models-are-views-over-parquet

**State.** Staging and intermediate models are views, and marts are tables. A query against a staging model re-reads the raw zone every time, so prefer a mart when one answers the question.
Why: The layer materialisations are set in dbt_project.yml: views are cheap and always fresh, which is right for a renaming layer and wrong for anything a dashboard hits repeatedly.
Evidence: model stg_datasf__311_cases; model int_point_activity; doc dbt/dbt_project.yml

## Models

### stg_datasf__analysis_neighborhoods (staging, view, 41 rows)

Grain: One row per analysis neighborhood, 41 of them.

| column | type | description |
|---|---|---|
| analysis_neighborhood | VARCHAR | Neighborhood name. The grain of this model, and the join key. 41 distinct, e.g. Bayview Hunters Point, Bernal Heights, Castro/Upper Market, Chinatown, Excelsior |
| published_area_sq_mi | DOUBLE | Area as DataSF publishes it. Carried as an independent check on the spherical area computed in ingestion/geometry.py, not as an alternative to it. min 0.1207, median 0.8258, max 5.173 |
| published_area_acres | DOUBLE | (no description in the yml) min 77.24, median 528.5, max 3,311 |
| geojson | VARCHAR | The MultiPolygon as text. Nothing joins on this and by ADR-6 nothing should; it is here so a map has a shape to draw. 41 distinct |
| socrata_updated_at | TIMESTAMP | (no description in the yml) 2016-07-13T23:33:14.829000 to 2016-07-13T23:33:14.829000; newest complete month 2016-06-01: 0 rows |
| ingested_at | TIMESTAMP | (no description in the yml) 2026-07-31T09:30:36.846363 to 2026-07-31T09:30:36.846363; newest complete month 2026-06-01: 0 rows |

### stg_spatial__point_geography (staging, view, 506,632 rows)

Grain: One row per point-bearing raw row, across every point dataset.

| column | type | description |
|---|---|---|
| source_table | VARCHAR | The raw table name from the dataset registry, e.g. raw_311_cases. Half the grain, and the string every point staging model filters on. values: raw_business_locations 72.0%, raw_311_cases 20.4%, raw_building_permits 7.2%, raw_film_locations 0.4% |
| row_key | VARCHAR | The row's key in its own dataset, matching that dataset's grain_key in the registry. Half the grain. 506,632 distinct, e.g. 0000024-02-999-0000024, 0000028-02-001-0000028, 0000052-01-001-0000052, 0000071-01-001-0000071, 0000071-02-001-0000071 |
| latitude | DOUBLE | (no description in the yml) 2.4% null; min -28.1, median 37.77, max 64.81 |
| longitude | DOUBLE | (no description in the yml) 2.4% null; min -159.4, median -122.4, max 153.4 |
| coordinate_status | VARCHAR | One of ok, missing, unparseable, impossible, out_of_bounds. values: ok 86.6%, out_of_bounds 11.0%, missing 2.4% |
| is_usable_coordinate | BOOLEAN | Whether the coordinate parsed and landed inside the San Francisco box. 86.6% true; 2 distinct |
| h3_r8 | BIGINT | H3 cell at resolution 8 as a BIGINT. Null unless the coordinate parsed. 2.4% null; 15,771 distinct |
| h3_r10 | BIGINT | H3 cell at resolution 10 as a BIGINT. The membership resolution: this is the column boundary assignment was decided at. 2.4% null; 47,585 distinct |
| analysis_neighborhood | VARCHAR | Exact neighborhood membership (ADR-6). Null means outside every neighborhood, which is a real answer and not a failure. 15.6% null; values: Financial District 10.4%, Mission 8.4%, South of Market 5.2%, Sunset/Parkside 3.9%, Bayview Hunters Point 3.8%, Tenderloin 3.1%, Castro/Upper Market 2.9%, Nob Hill 2.8%, and 33 more |
| supervisor_district_id | VARCHAR | (no description in the yml) 15.5% null; values: 3.0 15.0%, 6.0 12.9%, 9.0 9.2%, 8.0 8.0%, 5.0 7.5%, 2.0 7.4%, 10.0 6.6%, 1.0 5.0%, and 3 more |
| census_block_group_geoid | VARCHAR | (no description in the yml) 15.5% null; 678 distinct, e.g. 060750117002, 060750117004, 060750117003, 060759809001, 060750180002 |
| neighborhood_assignment_method | VARCHAR | interior_cell where the cell alone settled it, exact_refinement where a point-in-polygon test was needed. The ratio between them is the diagnostic for whether the membership resolution is still fine enough: measured at 79 percent interior for neighborhoods at r10. 15.6% null; values: interior_cell 66.2%, exact_refinement 18.2% |

### stg_datasf__business_locations (staging, view, 364,731 rows)

Grain: One row per registered business location.

| column | type | description |
|---|---|---|
| business_location_id | VARCHAR | uniqueid upstream. The grain of this model. 364,731 distinct, e.g. 0000024-02-999-0000024, 0000028-02-001-0000028, 0000052-01-001-0000052, 0000071-01-001-0000071, 0000071-02-001-0000071 |
| certificate_number | VARCHAR | The business tax certificate. Deliberately not unique: one business accumulates a row per location it has held. 256,954 distinct, e.g. 1058654, 0168329, 1000652, 1028560, 0046324 |
| tax_id | VARCHAR | (no description in the yml) 364,731 distinct, e.g. 0000024-02-999, 0000028-02-001, 0000052-01-001, 0000071-01-001, 0000071-02-001 |
| ownership_name | VARCHAR | (no description in the yml) 244,792 distinct, e.g. Side Inc, Bon Appetit Management Co, Breather Products Us Inc, American Tower Corporation, Compass Group Usa Inc |
| business_name | VARCHAR | Trading name (dba_name upstream). Null on a small number of rows. 294,491 distinct, e.g. Breather, San Francisco, Uber, N/A, Sutter Pacific Medical Foundation |
| license_code | VARCHAR | (no description in the yml) 96.4% null; 1,273 distinct, e.g. H25R, H24R, J02R, POS01R, HHHR HHHR HHHR HHHR HHHR HHHR HHHR HH... |
| business_category | VARCHAR | Business activity from the licence code, e.g. "Retail Trade". The category dimension of the activity marts for this dataset. 96.4% null; 138 distinct, e.g. Multiple, RESTAURANT 1,000 - 2,000 SQFT, RESTAURANT - UNDER 1,000 SQFT, TATTOO, BODY PIERCING, PRACTITIONER, POINT OF SALE STATION |
| business_category_list | VARCHAR | (no description in the yml) 96.4% null; 1,782 distinct, e.g. RESTAURANT 1,000 - 2,000 SQFT, RESTAURANT - UNDER 1,000 SQFT, TATTOO, BODY PIERCING, PRACTITIONER, POINT OF SALE STATION, RESTAURANT - OVER 2,000 SQFT |
| naics_code | VARCHAR | (no description in the yml) 65.0% null; 4,087 distinct, e.g. 531110, 531120, 531210, 722511, 541110 |
| dba_started_at | TIMESTAMP | (no description in the yml) 1848-12-30T00:00:00 to 2026-07-30T00:00:00; newest complete month 2026-06-01: 852 rows |
| dba_ended_at | TIMESTAMP | (no description in the yml) 44.9% null; 1900-07-01T00:00:00 to 2026-09-20T00:00:00; newest complete month 2026-08-01: 0 rows |
| location_started_at | TIMESTAMP | When this location opened. The event date the activity marts count on, and not the same as dba_started_at, which is the trading name's life rather than the location's. 1849-01-01T00:00:00 to 2028-02-26T00:00:00; newest complete month 2028-01-01: 0 rows |
| location_ended_at | TIMESTAMP | (no description in the yml) 34.6% null; 1907-01-01T00:00:00 to 2205-12-30T00:00:00; newest complete month 2205-11-01: 0 rows |
| is_administratively_closed | BOOLEAN | (no description in the yml) 0.0% true; 1 distinct |
| is_active | BOOLEAN | Whether the location has no end date. Roughly a third of rows are active; the rest are closed registrations that are still worth counting for a historical rate and not for a current one. 34.6% true; 2 distinct |
| business_address | VARCHAR | (no description in the yml) <0.1% null; 226,330 distinct, e.g. 580 4th St, 2261 Market St, 548 Market St, 201 Spear St Ste 1100, 103 Horne Ave |
| business_city | VARCHAR | (no description in the yml) <0.1% null; 3,133 distinct, e.g. San Francisco, Oakland, Daly City, San Jose, Hayward |
| business_state | VARCHAR | (no description in the yml) 0.2% null; 61 distinct, e.g. CA, NY, TX, FL, IL |
| business_zip | VARCHAR | (no description in the yml) 0.2% null; 5,060 distinct, e.g. 94110, 94107, 94103, 94109, 94102 |
| business_corridor | VARCHAR | (no description in the yml) 90.8% null; values: Chinatown 1.3%, Central Market 1.2%, Market/Castro 0.9%, Union Street 0.7%, Mission Street 0.7%, North Beach 0.4%, Parkside Taraval 0.4%, 24th St 0.3%, and 19 more |
| community_benefit_district | VARCHAR | (no description in the yml) 79.0% null; values: Downtown 5.8%, Union Square Business Improvement Dis... 2.5%, SoMa West 2.1%, East Cut (Greater Rincon Hill) 2.0%, Yerba Buena 1.9%, Castro/Upper Market 1.2%, Tenderloin 1.2%, Mid Market 1.0%, and 8 more |
| upstream_analysis_neighborhood | VARCHAR | (no description in the yml) 21.2% null; values: Financial District/South Beach 12.3%, Mission 6.4%, South of Market 5.0%, Sunset/Parkside 3.8%, Bayview Hunters Point 3.7%, Marina 2.6%, Outer Richmond 2.6%, Chinatown 2.6%, and 33 more |
| upstream_supervisor_district | BIGINT | (no description in the yml) 21.2% null; values: 3 15.7%, 6 13.3%, 2 7.3%, 9 7.2%, 8 6.7%, 10 6.3%, 5 6.0%, 1 4.7%, and 3 more |
| pays_parking_tax | BOOLEAN | (no description in the yml) 0.0% true; 1 distinct |
| pays_transient_occupancy_tax | BOOLEAN | (no description in the yml) 0.0% true; 1 distinct |
| socrata_updated_at | TIMESTAMP | (no description in the yml) 2026-07-31T11:14:05.789000 to 2026-07-31T11:16:02.676000; newest complete month 2026-06-01: 0 rows |
| ingested_at | TIMESTAMP | (no description in the yml) 2026-07-31T21:48:55.053224 to 2026-07-31T21:51:06.884731; newest complete month 2026-06-01: 0 rows |
| latitude | DOUBLE | Latitude, parsed in ingestion/spatial.py. 3.0% null; min -28.1, median 37.78, max 64.81 |
| longitude | DOUBLE | Longitude, parsed in ingestion/spatial.py. 3.0% null; min -159.4, median -122.4, max 153.4 |
| coordinate_status | VARCHAR | Why a coordinate is unusable, where it is. out_of_bounds is the common one here and is not an error. values: ok 81.7%, out_of_bounds 15.3%, missing 3.0% |
| is_usable_coordinate | BOOLEAN | (no description in the yml) 81.7% true; 2 distinct |
| h3_r8 | BIGINT | (no description in the yml) 3.0% null; 15,757 distinct |
| h3_r10 | BIGINT | (no description in the yml) 3.0% null; 47,007 distinct |
| analysis_neighborhood | VARCHAR | The neighborhood this location is exactly inside (ADR-6). Null for the businesses registered here and located elsewhere. 21.2% null; values: Financial District 12.3%, Mission 6.4%, South of Market 5.0%, Sunset/Parkside 3.8%, Bayview Hunters Point 3.7%, Marina 2.6%, Outer Richmond 2.6%, Chinatown 2.6%, and 33 more |
| supervisor_district_id | VARCHAR | (no description in the yml) 21.2% null; values: 3.0 15.7%, 6.0 13.3%, 2.0 7.3%, 9.0 7.2%, 8.0 6.7%, 10.0 6.3%, 5.0 6.0%, 1.0 4.7%, and 3 more |
| census_block_group_geoid | VARCHAR | (no description in the yml) 21.2% null; 677 distinct, e.g. 060750117002, 060750117004, 060750117003, 060750180002, 060759809001 |

### stg_spatial__boundary (staging, view, 733 rows)

Grain: One row per boundary across every set: 41 neighborhoods, 11 supervisor districts and 681 block groups.

| column | type | description |
|---|---|---|
| boundary_set | VARCHAR | Which set this boundary belongs to. values: census_block_group 92.9%, analysis_neighborhood 5.6%, supervisor_district 1.5% |
| boundary_id | VARCHAR | The boundary's key within its set. 733 distinct, e.g. 060750101011, 060750101012, 060750101021, 060750102011, 060750102012 |
| boundary_name | VARCHAR | (no description in the yml) 60 distinct, e.g. Block Group 1, Block Group 2, Block Group 3, Block Group 4, Block Group 5 |
| area_sq_km | DOUBLE | Spherical area computed in ingestion/geometry.py. Exact on a sphere and therefore off by the Earth's flattening, about 0.3 percent, which is far below the uncertainty in what a boundary means. The upper bound below is 500 rather than something near San Francisco's 121 square kilometres of land, because two boundaries are legitimately enormous: Supervisorial District 4 reaches out to the Farallon Islands 43 km offshore and covers 261 square kilometres, and block group 060759804011 is the ocean water block group at 248. A tighter bound fails on correct data, which is how this number was arrived at. min 0.01226, median 0.1218, max 260.6 |
| population | BIGINT | Population, on block groups only. Null for the other two sets. 7.1% null; min 0, median 1,217, max 6,050 |
| housing_units | BIGINT | (no description in the yml) 7.1% null; min 0, median 559, max 2,424 |
| geojson | VARCHAR | (no description in the yml) 733 distinct |

### stg_spatial__h3_population (staging, view, 39,301 rows)

Grain: One row per H3 cell per resolution: population and housing units from the 2020 Census, areally interpolated from block groups.

| column | type | description |
|---|---|---|
| resolution | INTEGER | H3 resolution. values: 10 98.0%, 8 2.0% |
| h3_cell | BIGINT | The cell, as a BIGINT. 39,301 distinct |
| population | DOUBLE | Interpolated residents. Assumes population is uniform within a block group, so a cell covering half a park gets half a park's worth of people who are not there. min 0, median 0, max 2.943e+04 |
| housing_units | DOUBLE | Interpolated housing units, same method and same caveat. min 0, median 0, max 1.611e+04 |

### stg_spatial__polygon_h3 (staging, view, 84,296 rows)

Grain: One row per boundary per resolution per covering H3 cell.

| column | type | description |
|---|---|---|
| boundary_set | VARCHAR | analysis_neighborhood, supervisor_district or census_block_group. values: census_block_group 57.4%, supervisor_district 30.8%, analysis_neighborhood 11.8% |
| boundary_id | VARCHAR | The boundary's key within its set. 733 distinct, e.g. 4.0, 060759804011, 060759902000, 060759901000, 060750601002 |
| resolution | INTEGER | H3 resolution. 8 or 10, matching RESOLUTIONS in ingestion/h3_points.py. values: 10 95.8%, 8 4.2% |
| h3_cell | BIGINT | The covering cell, as a BIGINT. 40,172 distinct |
| is_interior | BOOLEAN | (no description in the yml) 73.7% true; 2 distinct |
| is_primary | BOOLEAN | Whether this boundary owns the cell. At most one row per (boundary_set, resolution, h3_cell) has this true, which is what makes a membership join safe. That uniqueness is asserted below, because a duplicate would silently double-count every event in the cell rather than erroring. 84.8% true; 2 distinct |
| is_allocation_cell | BOOLEAN | (no description in the yml) 85.4% true; 2 distinct |

### dim_neighborhood (mart, table, 41 rows)

Grain: One row per analysis neighborhood, 41 rows.

| column | type | description |
|---|---|---|
| analysis_neighborhood | VARCHAR | Neighborhood name. The grain of this model. 41 distinct, e.g. Bayview Hunters Point, Bernal Heights, Castro/Upper Market, Chinatown, Excelsior |
| area_sq_km | DOUBLE | Spherical area from ingestion/geometry.py. min 0.3125, median 2.138, max 13.39 |
| published_area_sq_mi | DOUBLE | (no description in the yml) min 0.1207, median 0.8258, max 5.173 |
| population | BIGINT | Interpolated 2020 residents. Zero is possible in principle and does not occur today; every rate divides through x_safe_divide anyway. min 178, median 2.01e+04, max 74,656 |
| housing_units | BIGINT | Interpolated 2020 housing units. min 63, median 8,980, max 28,042 |
| business_count | BIGINT | Registered business locations, ever. The right denominator for a historical commercial rate; use active_business_count for a current one, and note the two differ by more than half. min 39, median 6,045, max 44,708 |
| active_business_count | HUGEINT | (no description in the yml) min 18, median 2,063, max 14,227 |
| population_per_sq_km | DOUBLE | (no description in the yml) min 102.6, median 8,889, max 3.547e+04 |
| businesses_per_sq_km | DOUBLE | (no description in the yml) min 28.89, median 2,254, max 1.636e+04 |
| h3_cell_count | BIGINT | How many r10 cells this neighborhood owns. A coverage diagnostic: a neighborhood whose cell count is out of line with its area has a boundary problem, not a population problem. values: 82 7.3%, 131 4.9%, 66 4.9%, 101 2.4%, 106 2.4%, 113 2.4%, 120 2.4%, 135 2.4%, and 29 more |
| geojson | VARCHAR | (no description in the yml) 41 distinct |

### stg_datasf__supervisor_districts (staging, view, 11 rows)

Grain: One row per supervisor district, 11 of them, as drawn in the 2022 redistricting.

| column | type | description |
|---|---|---|
| supervisor_district | BIGINT | District number, 1 to 11. The grain of this model. min 1, median 6, max 11 |
| district_name | VARCHAR | (no description in the yml) 11 distinct, e.g. SUPERVISORIAL DISTRICT 1, SUPERVISORIAL DISTRICT 10, SUPERVISORIAL DISTRICT 11, SUPERVISORIAL DISTRICT 2, SUPERVISORIAL DISTRICT 3 |
| supervisor_name | VARCHAR | Sitting supervisor when DataSF last published the boundary file. A person, so it goes out of date on an election cycle rather than on a data cycle. 11 distinct, e.g. Bilal Mahmood, Chyanne Chen, Connie Chan, Danny Sauter, Jackie Fielder |
| geojson | VARCHAR | The MultiPolygon as text. 11 distinct |
| socrata_updated_at | TIMESTAMP | (no description in the yml) 2025-01-09T02:33:18.805000 to 2025-01-09T02:33:18.805000; newest complete month 2024-12-01: 0 rows |
| ingested_at | TIMESTAMP | (no description in the yml) 2026-07-31T09:30:38.067363 to 2026-07-31T09:30:38.067363; newest complete month 2026-06-01: 0 rows |

### dim_supervisor_district (mart, table, 11 rows)

Grain: One row per supervisor district, 11 rows, on the 2022 boundaries.

| column | type | description |
|---|---|---|
| supervisor_district | BIGINT | District number, 1 to 11. The grain of this model. min 1, median 6, max 11 |
| supervisor_district_id | VARCHAR | The same value as a string, which is what the H3 bridge and the point staging models carry. Kept so the join does not need a cast on one side. 11 distinct, e.g. 1.0, 10.0, 11.0, 2.0, 3.0 |
| district_name | VARCHAR | (no description in the yml) 11 distinct, e.g. SUPERVISORIAL DISTRICT 1, SUPERVISORIAL DISTRICT 10, SUPERVISORIAL DISTRICT 11, SUPERVISORIAL DISTRICT 2, SUPERVISORIAL DISTRICT 3 |
| supervisor_name | VARCHAR | (no description in the yml) 11 distinct, e.g. Bilal Mahmood, Chyanne Chen, Connie Chan, Danny Sauter, Jackie Fielder |
| area_sq_km | DOUBLE | Spherical area from ingestion/geometry.py. min 4.603, median 9.354, max 260.6 |
| population | BIGINT | Interpolated 2020 residents. min 71,813, median 7.881e+04, max 83,206 |
| housing_units | BIGINT | (no description in the yml) min 24,422, median 3.347e+04, max 50,052 |
| business_count | BIGINT | (no description in the yml) min 11,927, median 2.312e+04, max 57,386 |
| active_business_count | HUGEINT | (no description in the yml) min 4,070, median 7,760, max 19,293 |
| population_per_sq_km | DOUBLE | (no description in the yml) min 287.8, median 7,814, max 1.784e+04 |
| h3_cell_count | BIGINT | (no description in the yml) min 291, median 595, max 16,767 |
| geojson | VARCHAR | (no description in the yml) 11 distinct |

### stg_datasf__311_cases (staging, view, 103,457 rows)

Grain: One row per 311 case, deduplicated to the latest version of each case.

| column | type | description |
|---|---|---|
| case_id | VARCHAR | Unique identifier for the service request. The grain of this model. 103,457 distinct, e.g. 101000232041, 101000263511, 101000298272, 101000303217, 101000316922 |
| opened_at | TIMESTAMP | When the case was opened. 2024-06-14T10:28:36 to 2026-07-30T23:52:52; newest complete month 2026-06-01: 30,609 rows |
| closed_at | TIMESTAMP | When the case was closed. Null while the case is open. 12.4% null; 2025-03-18T11:01:46 to 2026-07-31T00:17:24; newest complete month 2026-06-01: 23,172 rows |
| last_updated_at | TIMESTAMP | (no description in the yml) 2026-06-21T00:21:59 to 2026-07-30T23:53:36; newest complete month 2026-06-01: 24,731 rows |
| status | VARCHAR | Current case status as reported by SF311. values: Closed 87.6%, Open 12.4% |
| agency | VARCHAR | (no description in the yml) 158 distinct, e.g. PW - Street and Environmental Services, MTA - Parking Enforcement Dispatch, Recology - Abandoned, Healthy Streets Operation Center, MTA - Abandoned Vehicles Work |
| service_category | VARCHAR | Top-level request type, e.g. Street and Sidewalk Cleaning. values: Street and Sidewalk Cleaning 34.5%, Parking Enforcement 19.9%, Graffiti Public 8.7%, General Request 8.0%, Encampment 4.3%, Graffiti Private 4.0%, Noise 2.8%, Blocked Street and Sidewalk 2.5%, and 29 more |
| service_subcategory | VARCHAR | (no description in the yml) 0.3% null; 227 distinct, e.g. garbage_and_debris, not_offensive, other_illegal_parking, parking_on_sidewalk, encampment |
| address | VARCHAR | (no description in the yml) 51,678 distinct, e.g. Not associated with a specific address, 1001 OFARRELL ST, SAN FRANCISCO, CA 9..., 10 SOUTH VAN NESS AVE, SAN FRANCISCO,..., 445 LEAVENWORTH ST, SAN FRANCISCO, CA..., 3033 24TH ST, SAN FRANCISCO, CA 94110 |
| upstream_supervisor_district | BIGINT | Supervisor district as DataSF stamped it, 1 to 11. Renamed from supervisor_district when the computed geography arrived: it is assigned at report time rather than recomputed when boundaries move, and ADR-2 rejected it as the answer. Kept for comparison. Join on supervisor_district_id instead. 0.9% null; values: 9 16.1%, 6 14.2%, 5 12.4%, 3 11.9%, 8 10.9%, 10 8.1%, 2 6.4%, 1 5.3%, and 3 more |
| upstream_analysis_neighborhood | VARCHAR | (no description in the yml) 1.2% null; values: Mission 15.9%, South of Market 6.4%, Tenderloin 5.9%, Nob Hill 4.3%, Bayview Hunters Point 4.3%, Castro/Upper Market 4.1%, Hayes Valley 4.0%, Financial District/South Beach 3.7%, and 33 more |
| police_district | VARCHAR | (no description in the yml) 1.0% null; values: MISSION 18.7%, NORTHERN 13.9%, SOUTHERN 10.8%, CENTRAL 10.4%, INGLESIDE 10.2%, TARAVAL 8.5%, PARK 8.0%, BAYVIEW 7.1%, and 2 more |
| request_source | VARCHAR | (no description in the yml) values: Web 41.3%, Mobile 40.8%, Phone 17.2%, Integrated Agency 0.5%, Test <0.1%, Twitter <0.1%, Email <0.1% |
| socrata_updated_at | TIMESTAMP | (no description in the yml) 2026-07-01T10:15:29.839000 to 2026-07-31T10:09:19.346000; newest complete month 2026-06-01: 0 rows |
| ingested_at | TIMESTAMP | (no description in the yml) 2026-07-31T06:59:48.862429 to 2026-07-31T21:48:52.532492; newest complete month 2026-06-01: 0 rows |
| latitude | DOUBLE | Latitude, parsed in ingestion/spatial.py. Null where unusable. 1.2% null; min 37.62, median 37.77, max 37.83 |
| longitude | DOUBLE | Longitude, parsed in ingestion/spatial.py. Null where unusable. 1.2% null; min -122.5, median -122.4, max -122.4 |
| coordinate_status | VARCHAR | One of ok, missing, unparseable, impossible, out_of_bounds. The last two are worth separating: impossible means the value is not a coordinate at all, out_of_bounds means it is a real place that is not in San Francisco. values: ok 98.8%, missing 1.2% |
| is_usable_coordinate | BOOLEAN | Whether the coordinate parsed and landed inside the San Francisco bounding box. False covers four different situations; read coordinate_status to tell them apart. 98.8% true; 2 distinct |
| h3_r8 | BIGINT | (no description in the yml) 1.2% null; 178 distinct |
| h3_r10 | BIGINT | (no description in the yml) 1.2% null; 5,965 distinct |
| analysis_neighborhood | VARCHAR | The neighborhood this case is exactly inside, from ADR-6's cell lookup plus point-in-polygon refinement. Null means outside every neighborhood, which is a real answer for a case in the bay. 1.2% null; values: Mission 15.9%, South of Market 6.4%, Tenderloin 5.9%, Nob Hill 4.3%, Bayview Hunters Point 4.3%, Castro/Upper Market 4.1%, Hayes Valley 4.0%, Financial District 3.7%, and 33 more |
| supervisor_district_id | VARCHAR | (no description in the yml) 1.2% null; values: 9.0 16.5%, 5.0 13.2%, 6.0 12.7%, 3.0 12.0%, 8.0 10.9%, 10.0 7.6%, 2.0 6.8%, 11.0 5.4%, and 3 more |
| census_block_group_geoid | VARCHAR | (no description in the yml) 1.2% null; 677 distinct, e.g. 060750177002, 060750120011, 060750201011, 060750201012, 060759803001 |

### stg_datasf__building_permits (staging, view, 36,230 rows)

Grain: One row per building permit record, deduplicated to the latest version of each record.

| column | type | description |
|---|---|---|
| permit_record_id | VARCHAR | Unique identifier for the permit record. The grain of this model. 36,230 distinct, e.g. 1000492135302, 1001783137927, 100207598913, 1002900386306, 1003451437455 |
| permit_number | VARCHAR | The permit this record belongs to. Deliberately not unique: one permit accumulates a record per revision, up to about 100. 33,098 distinct, e.g. 202009214648, 202607245559, 201911076677, 201711214576, 202411044364 |
| permit_type_code | VARCHAR | (no description in the yml) values: 8 86.2%, 3 7.6%, 4 3.1%, 9 1.2%, 7 0.6%, 2 0.6%, 6 0.4%, 1 0.3%, and 1 more |
| permit_type | VARCHAR | (no description in the yml) 1.2% null; values: otc alterations permit 86.2%, additions alterations or repairs 7.6%, sign - erect 3.1%, wall or painted sign 0.6%, new construction wood frame 0.6%, demolitions 0.4%, new construction 0.3%, grade or quarry or fill or excavate <0.1% |
| submission_method | VARCHAR | (no description in the yml) values: in-house 95.8%, website 4.2%, epr website <0.1% |
| permit_status | VARCHAR | Where the permit is in its lifecycle. Null on about a dozen records out of 1.3 million, so this deliberately carries no not_null test. values: cancelled 32.2%, complete 31.7%, issued 23.0%, filed 6.3%, expired 4.3%, triage 0.8%, withdrawn 0.7%, approved 0.5%, and 8 more |
| status_changed_at | TIMESTAMP | (no description in the yml) 1983-03-01T00:00:00 to 2026-07-31T00:17:58; newest complete month 2026-06-01: 3,947 rows |
| created_at | TIMESTAMP | (no description in the yml) 1980-02-21T00:00:00 to 2026-07-31T00:17:58; newest complete month 2026-06-01: 2,189 rows |
| filed_at | TIMESTAMP | When the application was filed. Null on a handful of older records. 1.9% null; 1980-02-21T00:00:00 to 2026-07-31T00:17:58; newest complete month 2026-06-01: 2,083 rows |
| approved_at | TIMESTAMP | (no description in the yml) 42.0% null; 1981-07-23T00:00:00 to 2026-07-30T17:06:59; newest complete month 2026-06-01: 1,819 rows |
| issued_at | TIMESTAMP | When the permit was issued. Null unless it reached that stage. 39.4% null; 1981-07-23T00:00:00 to 2026-07-30T17:56:54; newest complete month 2026-06-01: 1,961 rows |
| completed_at | TIMESTAMP | When work was recorded complete. Null on most records. 68.2% null; 1983-03-04T00:00:00 to 2026-07-30T15:59:53; newest complete month 2026-06-01: 1,650 rows |
| first_construction_doc_at | TIMESTAMP | (no description in the yml) 99.2% null; 1996-10-17T00:00:00 to 2026-07-30T08:49:46; newest complete month 2026-06-01: 10 rows |
| last_activity_at | TIMESTAMP | (no description in the yml) 1.6% null; 1960-01-01T00:00:00 to 2028-04-27T00:00:00; newest complete month 2028-03-01: 0 rows |
| street_number | VARCHAR | (no description in the yml) 4,005 distinct, e.g. 1, 101, 555, 55, 100 |
| street_number_suffix | VARCHAR | (no description in the yml) 98.0% null; values: A 1.5%, B 0.2%, V 0.2%, C <0.1%, D <0.1%, E <0.1%, L <0.1%, ½ <0.1%, and 3 more |
| street_name | VARCHAR | (no description in the yml) 1,452 distinct, e.g. California, Market, Mission, Geary, Montgomery |
| street_suffix | VARCHAR | (no description in the yml) 1.5% null; values: St 65.4%, Av 23.8%, Bl 2.4%, Dr 2.0%, Wy 1.8%, Ct 0.8%, Tr 0.7%, Ln 0.5%, and 12 more |
| unit | VARCHAR | (no description in the yml) 87.4% null; 279 distinct, e.g. 0, 1, 2, 3, 4 |
| unit_suffix | VARCHAR | (no description in the yml) 98.8% null; 83 distinct, e.g. A, C, B, D, HOA |
| zipcode | VARCHAR | (no description in the yml) <0.1% null; values: 94110 8.7%, 94114 5.9%, 94118 5.5%, 94122 5.3%, 94109 5.3%, 94112 4.9%, 94103 4.8%, 94117 4.7%, and 21 more |
| block | VARCHAR | (no description in the yml) 4,470 distinct, e.g. 3708, 3707, 0289, 0268, 0311 |
| lot | VARCHAR | (no description in the yml) 712 distinct, e.g. 001, 008, 007, 004, 003 |
| permit_description | VARCHAR | (no description in the yml) 0.4% null; 30,610 distinct, e.g. re-roofing: remove and replace roofin..., reroofing, reroofing no hot works, reroofing hot works, waterproofing details around windows ... |
| estimated_cost | DOUBLE | Cost declared by the applicant at filing, in dollars. 2.4% null; min 0, median 1.5e+04, max 1.75e+08 |
| revised_cost | DOUBLE | Cost after departmental revision, in dollars. Differs from estimated_cost often enough that the two should not be used interchangeably. 5.8% null; min 0, median 1.2e+04, max 1.75e+08 |
| existing_use | VARCHAR | (no description in the yml) 5.0% null; 86 distinct, e.g. 1 family dwelling, apartments, 2 family dwelling, office, retail sales |
| proposed_use | VARCHAR | (no description in the yml) 6.9% null; 86 distinct, e.g. 1 family dwelling, apartments, 2 family dwelling, office, retail sales |
| existing_occupancy | VARCHAR | (no description in the yml) 3.9% null; 653 distinct, e.g. R-3, R-2, B, M, B,M |
| proposed_occupancy | VARCHAR | (no description in the yml) 5.7% null; 723 distinct, e.g. R-3, R-2, B, B,M, M |
| existing_construction_type | VARCHAR | (no description in the yml) 9.2% null; values: wood frame (5) 68.4%, constr type 1 13.7%, constr type 3 5.8%, constr type 2 2.7%, constr type 4 0.2% |
| proposed_construction_type | VARCHAR | (no description in the yml) 10.7% null; values: wood frame (5) 67.9%, constr type 1 13.5%, constr type 3 5.3%, constr type 2 2.3%, constr type 4 0.2% |
| existing_units | BIGINT | (no description in the yml) 19.5% null; min 0, median 1, max 1,907 |
| proposed_units | BIGINT | (no description in the yml) 19.7% null; min 0, median 2, max 1,907 |
| existing_stories | BIGINT | (no description in the yml) 8.8% null; min 0, median 3, max 63 |
| proposed_stories | BIGINT | (no description in the yml) 10.5% null; min 0, median 3, max 220 |
| plansets | BIGINT | (no description in the yml) 1.1% null; values: 2 66.3%, 0 31.4%, 1 1.2%, 3 <0.1%, 8 <0.1% |
| is_adu | BOOLEAN | Whether the permit covers an accessory dwelling unit. 1.2% true; 2 distinct |
| is_site_permit | BOOLEAN | (no description in the yml) 1.6% true; 2 distinct |
| is_fire_only_permit | BOOLEAN | (no description in the yml) 5.1% true; 2 distinct |
| is_reroof | BOOLEAN | (no description in the yml) 6.9% true; 2 distinct |
| needs_structural_review | BOOLEAN | (no description in the yml) 4.4% true; 2 distinct |
| is_primary_address | BOOLEAN | (no description in the yml) 91.3% true; 2 distinct |
| upstream_supervisor_district | BIGINT | Supervisor district as DataSF stamped it. Null where the address did not geocode. Kept for comparison; join on supervisor_district_id. 0.1% null; values: 3 15.4%, 8 12.7%, 2 10.8%, 6 10.1%, 9 9.0%, 7 9.0%, 1 8.0%, 10 7.1%, and 3 more |
| upstream_analysis_neighborhood | VARCHAR | (no description in the yml) 0.1% null; values: Financial District/South Beach 10.4%, Mission 7.0%, Sunset/Parkside 6.4%, West of Twin Peaks 5.2%, Outer Richmond 4.2%, Castro/Upper Market 4.0%, Noe Valley 3.9%, Marina 3.9%, and 33 more |
| socrata_updated_at | TIMESTAMP | (no description in the yml) 2026-01-01T12:31:23.428000 to 2026-07-31T12:37:02.131000; newest complete month 2026-06-01: 3,657 rows |
| ingested_at | TIMESTAMP | (no description in the yml) 2026-07-31T07:01:40.822890 to 2026-07-31T21:48:54.086437; newest complete month 2026-06-01: 0 rows |
| latitude | DOUBLE | Latitude. Previously extracted from the location GeoJSON in this model; now parsed once in ingestion/spatial.py so that it and the H3 cell beside it cannot come from different places. 0.1% null; min 37.71, median 37.77, max 37.83 |
| longitude | DOUBLE | Longitude, parsed in ingestion/spatial.py. 0.1% null; min -122.5, median -122.4, max -122.4 |
| coordinate_status | VARCHAR | Why a coordinate is unusable, where it is. values: ok 99.9%, missing 0.1% |
| is_usable_coordinate | BOOLEAN | (no description in the yml) 99.9% true; 2 distinct |
| h3_r8 | BIGINT | (no description in the yml) 0.1% null; 170 distinct |
| h3_r10 | BIGINT | (no description in the yml) 0.1% null; 5,169 distinct |
| analysis_neighborhood | VARCHAR | The neighborhood this permit is exactly inside (ADR-6). 0.1% null; values: Financial District 10.4%, Mission 7.0%, Sunset/Parkside 6.4%, West of Twin Peaks 5.2%, Outer Richmond 4.2%, Castro/Upper Market 4.0%, Noe Valley 3.9%, Marina 3.9%, and 33 more |
| supervisor_district_id | VARCHAR | (no description in the yml) 0.1% null; values: 3.0 15.4%, 8.0 12.7%, 2.0 10.8%, 6.0 10.1%, 9.0 9.0%, 7.0 9.0%, 1.0 8.0%, 10.0 7.1%, and 3 more |
| census_block_group_geoid | VARCHAR | (no description in the yml) 0.1% null; 675 distinct, e.g. 060750117002, 060750615071, 060750615011, 060750117004, 060750611012 |

### int_point_activity (intermediate, view, 503,739 rows)

Grain: One row per dated event, across every point dataset that has a date.

| column | type | description |
|---|---|---|
| dataset | VARCHAR | Registry name of the source, e.g. 311_cases. values: business_locations 72.4%, 311_cases 20.5%, building_permits 7.1% |
| event_id | VARCHAR | The event's key in its own dataset. Unique within a dataset but not across them, so the grain is the pair with dataset. 503,739 distinct, e.g. 0000024-02-999-0000024, 0000028-02-001-0000028, 0000052-01-001-0000052, 0000071-01-001-0000071, 0000071-02-001-0000071 |
| event_month | TIMESTAMP | First day of the month the event happened, as a DATE on both engines via x_month_start. Never null: undated rows are excluded. 1849-01-01T00:00:00 to 2028-02-01T00:00:00; newest complete month 2028-01-01: 0 rows |
| h3_r8 | BIGINT | (no description in the yml) 2.4% null; 15,760 distinct |
| h3_r10 | BIGINT | (no description in the yml) 2.4% null; 47,536 distinct |
| analysis_neighborhood | VARCHAR | Exact neighborhood membership. Null means outside every neighborhood, mostly the registered businesses located outside San Francisco. 15.6% null; values: Financial District 10.4%, Mission 8.4%, South of Market 5.2%, Sunset/Parkside 3.9%, Bayview Hunters Point 3.8%, Tenderloin 3.1%, Castro/Upper Market 2.9%, Outer Richmond 2.8%, and 33 more |
| supervisor_district_id | VARCHAR | (no description in the yml) 15.6% null; values: 3.0 15.0%, 6.0 13.0%, 9.0 9.2%, 8.0 8.0%, 5.0 7.5%, 2.0 7.4%, 10.0 6.6%, 1.0 5.0%, and 3 more |
| category | VARCHAR | The dataset's own category dimension: service type for 311, permit type for permits, licence description for businesses. Coalesced to 'Unknown' rather than left null, so that grouping by it never silently drops rows. 184 distinct, e.g. Unknown, Street and Sidewalk Cleaning, otc alterations permit, Parking Enforcement, Graffiti Public |

### mart_activity_by_h3 (mart, table, 140,163 rows)

Grain: One row per H3 cell per dataset per category per month, at var('h3_mart_resolution'), currently 8.

| column | type | description |
|---|---|---|
| h3_cell | BIGINT | The cell as a BIGINT. Part of the grain. 15,760 distinct |
| h3_resolution | INTEGER | The resolution this mart was built at. Constant per build. 1 distinct |
| dataset | VARCHAR | Registry name of the source. Part of the grain. values: business_locations 85.3%, building_permits 9.8%, 311_cases 4.9% |
| category | VARCHAR | The dataset's own category dimension: service type for 311, permit type for permits, licence description for businesses. Part of the grain. One column name over three vocabularies, so grouping by it without also grouping by dataset pools three unrelated taxonomies. 176 distinct, e.g. Unknown, otc alterations permit, Multiple, additions alterations or repairs, RESTAURANT 1,000 - 2,000 SQFT |
| event_month | TIMESTAMP | First day of the month. Part of the grain. 1849-05-01T00:00:00 to 2028-02-01T00:00:00; newest complete month 2028-01-01: 0 rows |
| analysis_neighborhood | VARCHAR | The neighborhood owning this cell, for filtering and labelling. Null where the cell's centre is outside every neighborhood, which is water or just past the city line. 44.6% null; values: Sunset/Parkside 4.6%, Mission 4.3%, Bayview Hunters Point 4.2%, West of Twin Peaks 2.9%, Financial District 2.2%, Outer Richmond 2.1%, South of Market 2.1%, Bernal Heights 1.7%, and 33 more |
| supervisor_district_id | VARCHAR | The supervisor district owning this cell, as a string, on the 2022 boundaries. Null under the same rule as analysis_neighborhood. Join to dim_supervisor_district on this rather than on the integer district number, which is what the id exists for. 44.6% null; values: 7.0 6.5%, 10.0 6.5%, 8.0 5.7%, 9.0 5.7%, 2.0 5.5%, 3.0 5.1%, 4.0 4.6%, 1.0 4.6%, and 3 more |
| event_count | BIGINT | Events in this cell, dataset, category and month. min 1, median 1, max 1,699 |
| cell_population | DOUBLE | Interpolated residents in this cell. Null where the cell has no block group overlap at all, zero where it genuinely has nobody. 41.6% null; min 0, median 6,199, max 2.943e+04 |
| cell_housing_units | DOUBLE | Interpolated housing units in this cell, same method and same caveat as cell_population, and null under the same condition. 41.6% null; min 0, median 2,568, max 1.611e+04 |
| cell_area_sq_km | DECIMAL(11,10) | The H3 constant for this resolution, carried so a density can be re-derived without another lookup. Identical on every row of a build, which is exactly why events_per_sq_km ranks like the count. values: 0.7373 100.0% |
| events_per_1000_residents | DOUBLE | The normalised companion, and the one to rank on. Null rather than infinite where the cell has no residents, which is correct and common: the bay, the Presidio and the Financial District all have real activity and close to nobody living in them. 41.8% null; min 0.03398, median 0.399, max 654.9 |
| events_per_1000_housing_units | DOUBLE | The same normalisation against dwellings rather than people. Useful where the question is about the building stock; null where the cell has no housing units, for the same reason as above. 41.8% null; min 0.06207, median 0.9149, max 1.8e+04 |
| events_per_sq_km | DOUBLE | Density. A constant rescaling of event_count at fixed resolution, so it ranks identically; kept for comparability with the neighborhood mart. min 1.356, median 1.356, max 2,304 |

### mart_activity_by_neighborhood (mart, table, 40,157 rows)

Grain: One row per neighborhood per dataset per category per month.

| column | type | description |
|---|---|---|
| analysis_neighborhood | VARCHAR | Neighborhood name. Part of the grain. values: Financial District 5.5%, Mission 5.3%, South of Market 4.0%, Bayview Hunters Point 4.0%, Sunset/Parkside 3.7%, Tenderloin 3.6%, Chinatown 3.5%, Marina 3.3%, and 33 more |
| dataset | VARCHAR | Registry name of the source. Part of the grain. values: business_locations 71.5%, building_permits 21.2%, 311_cases 7.3% |
| category | VARCHAR | The dataset's own category dimension. Part of the grain. 172 distinct, e.g. Unknown, otc alterations permit, Multiple, additions alterations or repairs, RESTAURANT - UNDER 1,000 SQFT |
| event_month | TIMESTAMP | First day of the month. Part of the grain. 1849-05-01T00:00:00 to 2028-02-01T00:00:00; newest complete month 2028-01-01: 0 rows |
| event_count | BIGINT | Events in this neighborhood, dataset, category and month. min 1, median 2, max 4,509 |
| population | BIGINT | Interpolated 2020 residents in this neighborhood, from dim_neighborhood. The denominator behind events_per_1000_residents, and an April 2020 count regardless of which month the events are from. values: 23,908 5.5%, 58,062 5.3%, 28,426 4.0%, 40,384 4.0%, 74,656 3.7%, 36,067 3.6%, 14,455 3.5%, 23,325 3.3%, and 33 more |
| housing_units | BIGINT | Interpolated 2020 housing units, the denominator behind the rate below. values: 16,050 5.5%, 26,545 5.3%, 15,863 4.0%, 12,437 4.0%, 28,042 3.7%, 20,807 3.6%, 7,617 3.5%, 14,164 3.3%, and 33 more |
| business_count | BIGINT | Registered business locations ever, the denominator behind events_per_1000_businesses. Not the active count, which is the one dim_neighborhood carries separately and which is less than half of this. values: 44,708 5.5%, 23,440 5.3%, 18,314 4.0%, 13,341 4.0%, 13,949 3.7%, 8,908 3.6%, 9,514 3.5%, 9,660 3.3%, and 33 more |
| area_sq_km | DOUBLE | Spherical land area. Unlike the H3 mart this genuinely differs per row, which is what makes events_per_sq_km a real second measure here and a rescaled count there. values: 2.909 5.5%, 4.876 5.3%, 2.291 4.0%, 13.39 4.0%, 10.95 3.7%, 1.017 3.6%, 0.5816 3.5%, 2.624 3.3%, and 33 more |
| events_per_1000_residents | DOUBLE | The default normalised companion. Null where the neighborhood has no residents. min 0.01339, median 0.09591, max 491.3 |
| events_per_1000_housing_units | DOUBLE | The same normalisation against dwellings. Null where the neighborhood has no housing units. min 0.03566, median 0.2042, max 1,815 |
| events_per_1000_businesses | DOUBLE | The right companion for anything commercial, and the one that most changes the ranking against per-capita. min 0.02237, median 0.321, max 1,744 |
| events_per_sq_km | DOUBLE | Density. Unlike the H3 mart, neighborhood areas genuinely differ. min 0.07467, median 0.9785, max 2,216 |

### stg_datasf__film_locations (staging, view, 2,214 rows)

Grain: One row per published film location record.

| column | type | description |
|---|---|---|
| film_location_id | VARCHAR | Socrata row id. The grain of this model. 2,214 distinct, e.g. row-22d3_65j3-yx8j, row-23az_kpgb.bgun, row-23vm.if46_sw54, row-23ya.usm2~3jq2, row-24bi_cv2p.h5ph |
| film_title | VARCHAR | Title of the film or show. 350 distinct, e.g. Looking, The Phone/Jexi, The Last Black Man in San Francisco, DEVS, Chance Season 2 |
| release_year | BIGINT | Year of release. Null on at least one row upstream ("Goodbye, Mr.Chips"), so no not_null test: a single permanently missing value would make the test a warning nobody reads. <0.1% null; min 1,915, median 2,015, max 2,025 |
| production_company | VARCHAR | (no description in the yml) <0.1% null; 223 distinct, e.g. Mission Street Productions, LLC, Warner Bros. Pictures, Paramount Pictures, TVM Productions, Turner North Center Productions |
| distributor | VARCHAR | (no description in the yml) 6.2% null; 143 distinct, e.g. Netflix, Warner Bros. Pictures, Paramount Pictures, HBO, HULU |
| director | VARCHAR | (no description in the yml) 0.3% null; 290 distinct, e.g. Andrew Haigh, Steven Bochcho, Peyton Reed, Jon Lucas, Scott Moore, Joe Talbot |
| writer | VARCHAR | (no description in the yml) 0.3% null; 308 distinct, e.g. Michael Lannan, Eric Lodal, Harry Julian Fink, Alexandra Cunningham, Jon Lucas, Scott Moore |
| actor_1_name | VARCHAR | (no description in the yml) 0.2% null; 265 distinct, e.g. Jonathan Groff, Hugh Laurie, Clint Eastwood, Taye Diggs, Jamie Clayton |
| actor_2_name | VARCHAR | (no description in the yml) 4.2% null; 275 distinct, e.g. Frankie Alvarez, Gretchen Mol, Kathleen Robertson, Frankie J. Alvarez, Alexandra Shipp |
| actor_3_name | VARCHAR | (no description in the yml) 21.4% null; 170 distinct, e.g. Murray Bartlett, Ethan Suplee, Ian Anthony Dale, Michael Pena, Danny Glover |
| location_description | VARCHAR | Free text location as written by the submitter. Not an address and not a join key. 2.4% null; 1,756 distinct, e.g. Golden Gate Bridge, City Hall, Fairmont Hotel (950 Mason Street, Nob..., Treasure Island, Coit Tower |
| upstream_analysis_neighborhood | VARCHAR | (no description in the yml) 6.5% null; values: Financial District/South Beach 13.5%, North Beach 9.1%, Nob Hill 7.5%, Chinatown 7.4%, Mission 6.9%, Tenderloin 6.3%, Castro/Upper Market 4.0%, Russian Hill 3.9%, and 32 more |
| upstream_supervisor_district | BIGINT | (no description in the yml) 6.5% null; values: 3 37.6%, 6 12.2%, 5 9.9%, 2 8.4%, 8 6.5%, 9 6.4%, 10 5.2%, 7 2.8%, and 3 more |
| fun_facts | VARCHAR | Trivia about the shoot. Null on most rows. 79.0% null; 188 distinct, e.g. With 23 miles of ladders and 300,000 ..., The dome of SF's City Hall is almost ..., In 1945 the Fairmont hosted the Unite..., Driving shots, An artificial island, Treasure Island... |
| socrata_updated_at | TIMESTAMP | (no description in the yml) 2026-02-21T00:44:53.889000 to 2026-02-21T00:44:53.889000; newest complete month 2026-01-01: 0 rows |
| ingested_at | TIMESTAMP | (no description in the yml) 2026-07-31T06:56:41.226332 to 2026-07-31T06:56:41.226332; newest complete month 2026-06-01: 0 rows |
| latitude | DOUBLE | Latitude where published, usable on 2,127 of 2,214 rows. ADR-3 asserted this dataset carried no coordinates; ADR-7 corrects that. 3.9% null; min 36.85, median 37.79, max 37.9 |
| longitude | DOUBLE | Longitude where published. Null for roughly one row in twenty. 3.9% null; min -122.5, median -122.4, max -121.5 |
| coordinate_status | VARCHAR | Why a coordinate is unusable, where it is. values: ok 96.1%, missing 3.9%, out_of_bounds <0.1% |
| is_usable_coordinate | BOOLEAN | (no description in the yml) 96.1% true; 2 distinct |
| h3_r8 | BIGINT | (no description in the yml) 3.9% null; 147 distinct |
| h3_r10 | BIGINT | (no description in the yml) 3.9% null; 895 distinct |
| analysis_neighborhood | VARCHAR | The neighborhood this shoot location is exactly inside (ADR-6). 6.7% null; values: Financial District 13.5%, North Beach 9.1%, Nob Hill 7.5%, Chinatown 7.4%, Mission 6.9%, Tenderloin 6.3%, Castro/Upper Market 4.0%, Russian Hill 3.9%, and 32 more |
| supervisor_district_id | VARCHAR | (no description in the yml) 6.5% null; values: 3.0 37.6%, 6.0 12.2%, 5.0 9.9%, 2.0 8.4%, 8.0 6.5%, 9.0 6.4%, 10.0 5.2%, 7.0 2.8%, and 3 more |
| census_block_group_geoid | VARCHAR | (no description in the yml) 4.5% null; 368 distinct, e.g. 060750112002, 060750117002, 060750105002, 060750601002, 060750101011 |

### mart_film_locations (mart, table, 2,214 rows)

Grain: One row per published film location record, joined to the neighborhood it is actually in, with a count of how many locations each title used.

| column | type | description |
|---|---|---|
| film_location_id | VARCHAR | Socrata row id. The grain of this model. 2,214 distinct, e.g. row-22d3_65j3-yx8j, row-23az_kpgb.bgun, row-23vm.if46_sw54, row-23ya.usm2~3jq2, row-24bi_cv2p.h5ph |
| film_title | VARCHAR | Title of the film or show. Repeats, once per location it used. 350 distinct, e.g. Looking, The Phone/Jexi, The Last Black Man in San Francisco, DEVS, Chance Season 2 |
| release_year | BIGINT | (no description in the yml) <0.1% null; min 1,915, median 2,015, max 2,025 |
| production_company | VARCHAR | (no description in the yml) <0.1% null; 223 distinct, e.g. Mission Street Productions, LLC, Warner Bros. Pictures, Paramount Pictures, TVM Productions, Turner North Center Productions |
| distributor | VARCHAR | (no description in the yml) 6.2% null; 143 distinct, e.g. Netflix, Warner Bros. Pictures, Paramount Pictures, HBO, HULU |
| director | VARCHAR | (no description in the yml) 0.3% null; 290 distinct, e.g. Andrew Haigh, Steven Bochcho, Peyton Reed, Jon Lucas, Scott Moore, Joe Talbot |
| writer | VARCHAR | (no description in the yml) 0.3% null; 308 distinct, e.g. Michael Lannan, Eric Lodal, Harry Julian Fink, Alexandra Cunningham, Jon Lucas, Scott Moore |
| actor_1_name | VARCHAR | (no description in the yml) 0.2% null; 265 distinct, e.g. Jonathan Groff, Hugh Laurie, Clint Eastwood, Taye Diggs, Jamie Clayton |
| actor_2_name | VARCHAR | (no description in the yml) 4.2% null; 275 distinct, e.g. Frankie Alvarez, Gretchen Mol, Kathleen Robertson, Frankie J. Alvarez, Alexandra Shipp |
| actor_3_name | VARCHAR | (no description in the yml) 21.4% null; 170 distinct, e.g. Murray Bartlett, Ethan Suplee, Ian Anthony Dale, Michael Pena, Danny Glover |
| location_description | VARCHAR | (no description in the yml) 2.4% null; 1,756 distinct, e.g. Golden Gate Bridge, City Hall, Fairmont Hotel (950 Mason Street, Nob..., Treasure Island, Coit Tower |
| analysis_neighborhood | VARCHAR | The neighborhood this shoot location is in, computed from its coordinates. Null on the rows with no usable coordinate. 6.7% null; values: Financial District 13.5%, North Beach 9.1%, Nob Hill 7.5%, Chinatown 7.4%, Mission 6.9%, Tenderloin 6.3%, Castro/Upper Market 4.0%, Russian Hill 3.9%, and 32 more |
| supervisor_district_id | VARCHAR | (no description in the yml) 6.5% null; values: 3.0 37.6%, 6.0 12.2%, 5.0 9.9%, 2.0 8.4%, 8.0 6.5%, 9.0 6.4%, 10.0 5.2%, 7.0 2.8%, and 3 more |
| latitude | DOUBLE | (no description in the yml) 3.9% null; min 36.85, median 37.79, max 37.9 |
| longitude | DOUBLE | (no description in the yml) 3.9% null; min -122.5, median -122.4, max -121.5 |
| is_usable_coordinate | BOOLEAN | (no description in the yml) 96.1% true; 2 distinct |
| h3_r8 | BIGINT | (no description in the yml) 3.9% null; 147 distinct |
| h3_r10 | BIGINT | (no description in the yml) 3.9% null; 895 distinct |
| upstream_analysis_neighborhood | VARCHAR | The neighborhood DataSF stamped on the row. Kept beside the computed one because this is the smallest dataset in the warehouse and therefore the cheapest place to eyeball how often the two agree. 6.5% null; values: Financial District/South Beach 13.5%, North Beach 9.1%, Nob Hill 7.5%, Chinatown 7.4%, Mission 6.9%, Tenderloin 6.3%, Castro/Upper Market 4.0%, Russian Hill 3.9%, and 32 more |
| locations_for_title | BIGINT | How many location records this title has. The window function. values: 1 5.8%, 7 5.7%, 11 5.5%, 24 5.4%, 16 5.1%, 15 4.7%, 13 4.1%, 3 4.1%, and 25 more |
| neighborhoods_for_title | BIGINT | How many distinct neighborhoods this title was shot in. values: 10 9.8%, 6 9.8%, 4 9.3%, 7 8.8%, 8 8.0%, 5 7.6%, 9 6.1%, 14 6.1%, and 10 more |
| fun_facts | VARCHAR | (no description in the yml) 79.0% null; 188 distinct, e.g. With 23 miles of ladders and 300,000 ..., The dome of SF's City Hall is almost ..., In 1945 the Fairmont hosted the Unite..., Driving shots, An artificial island, Treasure Island... |

### mart_pipeline_freshness (mart, table, 7 rows)

Grain: One row per registered source.

| column | type | description |
|---|---|---|
| source_name | VARCHAR | Registry name of the source, e.g. 311_cases. The grain of this model. 7 distinct, e.g. 311_cases, analysis_neighborhoods, building_permits, business_locations, census_block_groups |
| source_table | VARCHAR | Raw table the source lands in, e.g. raw_311_cases. 7 distinct, e.g. raw_311_cases, raw_analysis_neighborhoods, raw_building_permits, raw_business_locations, raw_census_block_groups |
| staging_model | VARCHAR | Staging model built from this source. Also the join key to dbt's test results, which is why it has to match the model name exactly. 7 distinct, e.g. stg_census__block_groups, stg_datasf__311_cases, stg_datasf__analysis_neighborhoods, stg_datasf__building_permits, stg_datasf__business_locations |
| tier | VARCHAR | core, reference or demoted, per ADR-7. Reference sources are the boundary sets: they change every several years, so staleness is not a signal and they carry no SLA, but they are not demoted either because every spatial mart depends on them. Demoted sources carry no SLA and earn no maintenance. values: core 42.9%, reference 42.9%, demoted 14.3% |
| row_count | BIGINT | Rows currently in the raw table, counted directly rather than accumulated. min 11, median 2,214, max 729,403 |
| row_delta | BIGINT | Rows the most recent ingestion run added. 0 is the common healthy case: the run went out, found nothing new, and wrote nothing. values: 0 57.1%, 31,191 14.3%, 364,731 14.3%, 499 14.3% |
| previous_row_count | BIGINT | row_count minus row_delta, i.e. the count before the last run. min 11, median 2,214, max 364,672 |
| last_load_at | TIMESTAMP | When rows last landed in the raw zone. Null if nothing has ever loaded. 2026-07-31T06:56:41.226332 to 2026-07-31T21:51:06.884731; newest complete month 2026-06-01: 0 rows |
| last_ingest_run_id | VARCHAR | (no description in the yml) 7 distinct, e.g. 20260731T065640Z, 20260731T093034Z, 20260731T093037Z, 20260731T093038Z, 20260731T214842Z |
| last_run_finished_at | TIMESTAMP | When ingestion last ran at all, successful or not. Later than last_load_at whenever recent runs found nothing new. 2026-07-31T21:48:53.391891 to 2026-07-31T21:51:10.081736; newest complete month 2026-06-01: 0 rows |
| last_run_status | VARCHAR | success or failed, from the ingestion run manifest. values: success 100.0% |
| last_run_mode | VARCHAR | (no description in the yml) values: incremental 100.0% |
| hours_since_load | DOUBLE | Hours since last_load_at, fractional, in UTC on both engines. See x_utc_now in macros/cross_engine.sql for why that needed saying. min 173.3, median 185.7, max 188.2 |
| hours_since_run_attempt | DOUBLE | Hours since ingestion last ran, fractional. values: 173.3 42.9%, 173.3 14.3%, 173.3 14.3%, 173.3 14.3%, 173.3 14.3% |
| stale_after_hours | INTEGER | Freshness SLA in hours. Null means the source has no SLA. 57.1% null; values: 168 28.6%, 48 14.3% |
| is_stale | BOOLEAN | Whether hours_since_load has passed stale_after_hours. Always false for sources with no SLA, so this never fires on a demoted source. 42.9% true; 2 distinct |
| point_count | BIGINT | Rows this source contributed to the spatial precompute. Null for a source with no point geometry, which is how a non-spatial source is told apart from a spatial one whose coordinates all failed. 42.9% null; min 2,214, median 6.984e+04, max 364,731 |
| usable_point_count | HUGEINT | Of those, how many produced a coordinate inside San Francisco. 42.9% null; min 2,127, median 6.92e+04, max 298,076 |
| missing_coordinate_count | HUGEINT | Rows with no coordinate at all. Expected to be nonzero forever and deliberately not counted against health. 42.9% null; min 46, median 665, max 10,913 |
| out_of_bounds_count | HUGEINT | Rows whose coordinate is a real place outside San Francisco. Almost entirely registered businesses located elsewhere, which is correct data, so this does not count against health either. 42.9% null; values: 0 28.6%, 1 14.3%, 55,742 14.3% |
| malformed_coordinate_count | HUGEINT | Rows whose coordinate could not be parsed or was not on Earth. Unlike the two above this is a pipeline fault, not a fact about the world, so any value above zero makes is_healthy false. It is the shape an upstream column change takes. 42.9% null; values: 0 57.1% |
| coordinate_drop_rate_pct | DOUBLE | Percentage of this source's rows that could not be placed on a map, all four reasons combined. Measured 2026-07-31: 311 1.20, permits 0.12, street trees 1.58, film locations 3.93, business locations 18.27. The last is high because the registry records businesses located outside the city, not because it is dirty. 42.9% null; min 0.127, median 2.566, max 18.28 |
| tests_total | BIGINT | Tests run against this source's staging model in the last completed dbt run. values: 10 28.6%, 11 28.6%, 3 14.3%, 4 14.3%, 9 14.3% |
| tests_passed | HUGEINT | Of those, how many passed. values: 10 28.6%, 11 28.6%, 3 14.3%, 4 14.3%, 9 14.3% |
| tests_failed | HUGEINT | Of those, how many failed. values: 0 100.0% |
| tests_warned | HUGEINT | Of those, how many warned. Warnings are signals, not failures. values: 0 100.0% |
| tests_errored | HUGEINT | Of those, how many errored, meaning the test itself could not run. values: 0 100.0% |
| last_test_run_at | TIMESTAMP | When that dbt run started. Null before the second ever run. 2026-08-08T02:19:01 to 2026-08-08T02:19:01; newest complete month 2026-07-01: 0 rows |
| is_healthy | BOOLEAN | False if the last ingestion run failed, if any test failed or errored, if any coordinate was malformed, or if the source is past its SLA. True otherwise. The single column to read when checking in. 57.1% true; 2 distinct |

### stg_census__block_groups (staging, view, 681 rows)

Grain: One row per census block group, 681 of them in San Francisco.

| column | type | description |
|---|---|---|
| block_group_geoid | VARCHAR | The 12-character GEOID: state, county, tract, block group. The grain of this model. Kept a string because its leading zeros are meaningful ("06" is California) and a numeric cast eats them. 681 distinct, e.g. 060750101011, 060750101012, 060750101021, 060750102011, 060750102012 |
| state_fips | VARCHAR | (no description in the yml) values: 06 100.0% |
| county_fips | VARCHAR | (no description in the yml) values: 075 100.0% |
| tract_code | VARCHAR | (no description in the yml) 244 distinct, e.g. 030900, 032700, 035300, 013300, 021500 |
| block_group_number | VARCHAR | (no description in the yml) values: 1 35.5%, 2 33.5%, 3 21.9%, 4 6.6%, 5 1.5%, 6 0.4%, 0 0.3%, 7 0.3% |
| block_group_name | VARCHAR | (no description in the yml) values: Block Group 1 35.5%, Block Group 2 33.5%, Block Group 3 21.9%, Block Group 4 6.6%, Block Group 5 1.5%, Block Group 6 0.4%, Block Group 0 0.3%, Block Group 7 0.3% |
| population | BIGINT | 2020 Census population (POP100). Zero is legitimate and occurs on block groups that are entirely park, water or industrial, which is why every rate divides through x_safe_divide. min 0, median 1,217, max 6,050 |
| housing_units | BIGINT | 2020 Census housing units (HU100). Zero is legitimate. min 0, median 559, max 2,424 |
| land_area_sq_km | DOUBLE | Land area, converted from the square metres the Census publishes. Kept separate from water: several San Francisco block groups are mostly bay, and a density over total area understates them badly. min 0, median 0.1175, max 4.338 |
| water_area_sq_km | DOUBLE | Water area, converted from square metres. values: 0 93.8%, 0.004369 0.1%, 0.008871 0.1%, 0.009359 0.1%, 0.009745 0.1%, 0.009769 0.1%, 0.01053 0.1%, 0.01221 0.1%, and 35 more |
| internal_point_latitude | DOUBLE | A point the Census guarantees is inside the polygon. Not the centroid, which for a crescent-shaped block group can be outside it. min 37.71, median 37.76, max 37.83 |
| internal_point_longitude | DOUBLE | Longitude of that internal point. min -123, median -122.4, max -122.4 |
| geojson | VARCHAR | The block group polygon as text. 681 distinct |
| socrata_updated_at | TIMESTAMP | (no description in the yml) 2020-04-01T00:00:00 to 2020-04-01T00:00:00; newest complete month 2020-03-01: 0 rows |
| ingested_at | TIMESTAMP | (no description in the yml) 2026-07-31T09:30:40.288231 to 2026-07-31T09:30:40.288231; newest complete month 2026-06-01: 0 rows |

### stg_spatial__pip_sample (staging, view, 24,000 rows)

Grain: One row per sampled point per boundary set: the exact point-in-polygon answer, computed in Python by an implementation separate from the one that assigns boundaries.

| column | type | description |
|---|---|---|
| source_table | VARCHAR | Which dataset the sampled point came from. values: raw_311_cases 25.0%, raw_building_permits 25.0%, raw_business_locations 25.0%, raw_film_locations 25.0% |
| row_key | VARCHAR | The point's key in its own dataset. 8,000 distinct, e.g. 0004652-01-001-0004652, 0009699-04-001-0009699, 0010587-01-001-0010587, 0011192-02-001-0011192, 0012721-12-001-0012721 |
| boundary_set | VARCHAR | (no description in the yml) values: analysis_neighborhood 33.3%, census_block_group 33.3%, supervisor_district 33.3% |
| latitude | DOUBLE | (no description in the yml) min 37.61, median 37.78, max 37.93 |
| longitude | DOUBLE | (no description in the yml) min -122.5, median -122.4, max -122.3 |
| exact_boundary_id | VARCHAR | The boundary the point is really inside, by exact geometry. Null means it is outside every boundary in the set, which is a correct answer and is credited as agreement by the tests. 1.5% null; 726 distinct, e.g. 3.0, 6.0, Financial District, 9.0, Mission |
| h3_r8 | BIGINT | (no description in the yml) 238 distinct |
| h3_r10 | BIGINT | (no description in the yml) 3,318 distinct |

## Join map

| from | to | cardinality | safe |
|---|---|---|---|
| mart_activity_by_h3.analysis_neighborhood | dim_neighborhood.analysis_neighborhood | many to one | yes |
| mart_activity_by_neighborhood.analysis_neighborhood | dim_neighborhood.analysis_neighborhood | many to one | yes |
| mart_film_locations.analysis_neighborhood | dim_neighborhood.analysis_neighborhood | many to one | yes |
| stg_datasf__311_cases.analysis_neighborhood | dim_neighborhood.analysis_neighborhood | many to one | yes |
| stg_datasf__building_permits.analysis_neighborhood | dim_neighborhood.analysis_neighborhood | many to one | yes |
| stg_datasf__business_locations.analysis_neighborhood | dim_neighborhood.analysis_neighborhood | many to one | yes |
| stg_datasf__film_locations.analysis_neighborhood | dim_neighborhood.analysis_neighborhood | many to one | yes |
| mart_film_locations.upstream_analysis_neighborhood | dim_neighborhood.analysis_neighborhood | many to one | NO |
| stg_datasf__311_cases.upstream_supervisor_district | dim_supervisor_district.supervisor_district | many to one | NO |
| mart_activity_by_h3.h3_cell | stg_spatial__polygon_h3.h3_cell | many to one, but only with is_primary in the join | yes |
| stg_spatial__h3_population.h3_cell | stg_spatial__polygon_h3.h3_cell | many to many by design, one row per boundary the cell serves | yes |
| int_point_activity.analysis_neighborhood | dim_neighborhood.analysis_neighborhood | many to one | yes |
| mart_activity_by_h3.supervisor_district_id | dim_supervisor_district.supervisor_district_id | many to one | yes |

- `mart_film_locations.upstream_analysis_neighborhood` to `dim_neighborhood.analysis_neighborhood`: The dirty join, and the worked example of the class. This is the neighborhood DataSF stamped on the row, not the one computed here from the coordinates. It will mostly match, which is what makes it dangerous: the join succeeds and answers a different question. Join on analysis_neighborhood instead.
- `stg_datasf__311_cases.upstream_supervisor_district` to `dim_supervisor_district.supervisor_district`: The same shape of trap on the districts. The upstream column was assigned when the row was published, under whichever boundaries were current then, and the dimension is on the 2022 lines. Join on supervisor_district_id.
- `mart_activity_by_h3.h3_cell` to `stg_spatial__polygon_h3.h3_cell`: Three flags, each for a different job, and picking the wrong one is the easiest mistake available here. is_primary: this boundary owns the cell, at most one per cell, so the join cannot fan out. Use it to LABEL a cell. is_interior: the cell is entirely inside. Plain membership with neither flag fans every edge cell out into two or three rows and double counts every event in it.
  - on: `mart_activity_by_h3.h3_cell = stg_spatial__polygon_h3.h3_cell and stg_spatial__polygon_h3.resolution = mart_activity_by_h3.h3_resolution and stg_spatial__polygon_h3.boundary_set = 'analysis_neighborhood' and stg_spatial__polygon_h3.is_primary`
- `stg_spatial__h3_population.h3_cell` to `stg_spatial__polygon_h3.h3_cell`: The third flag, and the one that is not interchangeable with is_primary. To SPREAD a measure such as population across boundaries, use is_allocation_cell: is_primary keeps one boundary per cell and would discard the losing boundaries and their residents with them.
  - on: `stg_spatial__h3_population.h3_cell = stg_spatial__polygon_h3.h3_cell and stg_spatial__h3_population.resolution = stg_spatial__polygon_h3.resolution and stg_spatial__polygon_h3.is_allocation_cell`
- `int_point_activity.analysis_neighborhood` to `dim_neighborhood.analysis_neighborhood`: The spine to its denominators. Nulls do not match, which is correct and is the exclusion the two-marts disclosure is about: the join silently drops events that are outside every neighborhood.
- `mart_activity_by_h3.supervisor_district_id` to `dim_supervisor_district.supervisor_district_id`: Join on the string id rather than on the integer district number: the dimension carries both for exactly this reason, so neither side needs a cast. The label is subject to the cell-membership disclosure.

## Verified examples

Every query below was executed against this target at generation time. The row count is what it returned.

### ex.reports-per-capita-by-neighborhood

Which neighborhoods report the most street cleaning per resident?

```sql
-- Reports per resident, not reports. The denominator is named in the
-- output so the answer cannot quietly drop it.
select
    m.analysis_neighborhood,
    sum(m.event_count) as reports,
    d.population as residents_april_2020,
    round(1000.0 * sum(m.event_count) / d.population, 1) as reports_per_1000_residents
from mart_activity_by_neighborhood as m
join dim_neighborhood as d
    on m.analysis_neighborhood = d.analysis_neighborhood
where m.dataset = '311_cases'
    and m.category = 'Street and Sidewalk Cleaning'
    and d.population > 0
group by m.analysis_neighborhood, d.population
order by reports_per_1000_residents desc
```

Demonstrates: refuse.rank-by-raw-count, refuse.311-measures-reporting-not-incidence. Verified against duckdb at 2026-08-08T03:10:01+00:00, 41 rows.

### ex.h3-cells-ranked-by-rate

Which hexagons are noisier than their population explains?

```sql
-- Ranked by the rate that varies per cell. events_per_sq_km is the count
-- times a constant at fixed resolution and would return the same order as
-- event_count.
select
    h3_cell,
    analysis_neighborhood,
    sum(event_count) as events,
    round(max(cell_population), 1) as cell_population,
    round(1000.0 * sum(event_count) / max(cell_population), 1)
        as events_per_1000_residents
from mart_activity_by_h3
where dataset = '311_cases'
    and cell_population > 0
group by h3_cell, analysis_neighborhood
order by events_per_1000_residents desc
limit 20
```

Demonstrates: refuse.events-per-sq-km-on-the-h3-mart. Verified against duckdb at 2026-08-08T03:10:01+00:00, 20 rows.

### ex.rate-with-denominator-vintage

What is the 311 rate per resident by neighborhood, with the denominator's vintage stated?

```sql
-- The vintage is a column rather than a footnote: every row carries the
-- April 2020 denominator it was divided by, and the label travels with the
-- number into whatever reads this.
select
    d.analysis_neighborhood,
    'April 2020 Decennial Census' as denominator_vintage,
    d.population as residents,
    sum(m.event_count) as events,
    round(1000.0 * sum(m.event_count) / d.population, 1) as events_per_1000_residents
from mart_activity_by_neighborhood as m
join dim_neighborhood as d
    on m.analysis_neighborhood = d.analysis_neighborhood
where m.dataset = '311_cases'
    and d.population > 0
group by d.analysis_neighborhood, d.population
order by events_per_1000_residents desc
limit 15
```

Demonstrates: refuse.per-capita-divides-by-april-2020. Verified against duckdb at 2026-08-08T03:10:01+00:00, 15 rows.

### ex.lowest-rate-with-exclusions-counted

Which hexagons have the lowest 311 rate per resident, and how many were excluded for having no residents?

```sql
-- A lowest-rate question is only answerable alongside the count of areas
-- the question does not apply to, so that count is in every row.
with per_cell as (
    select
        h3_cell,
        analysis_neighborhood,
        sum(event_count) as events,
        max(cell_population) as cell_population
    from mart_activity_by_h3
    where dataset = '311_cases'
    group by h3_cell, analysis_neighborhood
)
select
    h3_cell,
    analysis_neighborhood,
    events,
    round(cell_population, 1) as cell_population,
    round(1000.0 * events / cell_population, 1) as events_per_1000_residents,
    (select count(*) from per_cell where coalesce(cell_population, 0) = 0)
        as cells_excluded_no_residents,
    (select count(*) from per_cell) as cells_total
from per_cell
where cell_population > 0
order by events_per_1000_residents asc, h3_cell
limit 10
```

Demonstrates: refuse.null-rate-is-not-a-low-rate. Verified against duckdb at 2026-08-08T03:10:01+00:00, 10 rows.

### ex.permit-filings-per-month-by-type

How many building permits were filed per month, by permit type?

```sql
-- Filings, and labelled as filings. The activity spine dates a permit at
-- filing, so this is demand and not construction, and the newest month is
-- excluded because it is always partial.
select
    date_trunc('month', filed_at) as filed_month,
    permit_type,
    count(*) as records_filed,
    count(distinct permit_number) as permits_filed
from stg_datasf__building_permits
where filed_at >= timestamp '2025-01-01'
    and date_trunc('month', filed_at)
        < date_trunc('month', (select max(filed_at) from stg_datasf__building_permits))
group by filed_month, permit_type
order by filed_month desc, records_filed desc
limit 40
```

Demonstrates: refuse.permits-are-filings-not-construction. Verified against duckdb at 2026-08-08T03:10:01+00:00, 40 rows.

### ex.distinct-businesses-by-neighborhood

How many businesses are registered in each neighborhood, counting certificates rather than rows?

```sql
-- Three numbers because there are three answers: rows, certificates ever
-- registered, and certificates with no end date. Reporting one of them
-- alone is the failure this example exists for.
select
    analysis_neighborhood,
    count(*) as registry_rows,
    count(distinct certificate_number) as certificates_ever,
    count(distinct case when is_active then certificate_number end) as certificates_active
from stg_datasf__business_locations
where analysis_neighborhood is not null
group by analysis_neighborhood
order by certificates_active desc
limit 15
```

Demonstrates: refuse.business-registry-is-not-a-business-count. Verified against duckdb at 2026-08-08T03:10:01+00:00, 15 rows.

## Freshness

mart_pipeline_freshness, projected. last_load_at is when rows last landed in the raw zone, which is the build's own view of its inputs and not a publish time.

| source | tier | row_count | last_load_at | last_run_finished_at | stale_after_hours | is_stale |
|---|---|---|---|---|---|---|
| 311_cases | core | 134,457 | 2026-07-31T21:48:52.532492 | 2026-07-31T21:48:53.391891 | 48 | true |
| analysis_neighborhoods | reference | 41 | 2026-07-31T09:30:36.846363 | 2026-07-31T21:51:08.327049 | none | false |
| building_permits | core | 36,611 | 2026-07-31T21:48:54.086437 | 2026-07-31T21:48:54.125785 | 168 | true |
| business_locations | core | 729,403 | 2026-07-31T21:51:06.884731 | 2026-07-31T21:51:07.189818 | 168 | true |
| census_block_groups | reference | 681 | 2026-07-31T09:30:40.288231 | 2026-07-31T21:51:08.935482 | none | false |
| film_locations | demoted | 2,214 | 2026-07-31T06:56:41.226332 | 2026-07-31T21:51:10.081736 | none | false |
| supervisor_districts | reference | 11 | 2026-07-31T09:30:38.067363 | 2026-07-31T21:51:08.925510 | none | false |

## Integrity

Before trusting this pack, compare its integrity block against the target itself: the schema hash of every model you intend to query, and the dbt invocation it was built from. If they disagree, this pack describes something the target does not contain, and the correct response is to refuse every question rather than to answer from a stale description.

Built from dbt invocation `c0e3245a-5fb9-4e20-a081-21a7058289d0` (1.12.0, adapter duckdb), manifest generated 2026-08-08T03:09:39.146747Z.

| model | schema hash | rows |
|---|---|---|
| stg_datasf__analysis_neighborhoods | 233f8a6cd6a92ff4 | 41 |
| stg_spatial__point_geography | f4790438b00b7701 | 506,632 |
| stg_datasf__business_locations | 5b5bda14160ccc7a | 364,731 |
| stg_spatial__boundary | ad3aa3bb34efeb82 | 733 |
| stg_spatial__h3_population | 14cca39c4696c0e5 | 39,301 |
| stg_spatial__polygon_h3 | fbfb55e7f5a806da | 84,296 |
| dim_neighborhood | d6ab3c72cd7f141e | 41 |
| stg_datasf__supervisor_districts | 59fdaddbe6095f59 | 11 |
| dim_supervisor_district | 58c1e954a3a26872 | 11 |
| stg_datasf__311_cases | ceb94bbffcee8a90 | 103,457 |
| stg_datasf__building_permits | ebaffc82217966e2 | 36,230 |
| int_point_activity | 1f8104335f958779 | 503,739 |
| mart_activity_by_h3 | 0e29db73e198cd99 | 140,163 |
| mart_activity_by_neighborhood | c206f557c69a5990 | 40,157 |
| stg_datasf__film_locations | 62a8ae1c5c808081 | 2,214 |
| mart_film_locations | c65a9053b2458bb4 | 2,214 |
| mart_pipeline_freshness | 6552dcb2fdafb99d | 7 |
| stg_census__block_groups | 6d15d6d6dbb98ce7 | 681 |
| stg_spatial__pip_sample | 2ebb8d9d7a3add17 | 24,000 |
