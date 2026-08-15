# sf-data-warehouse context pack, target published

An analytics warehouse over seven public San Francisco datasets, modelled with dbt into staging views, one intermediate model and six marts, in which every geography is precomputed rather than computed at query time. This pack describes the published export of that warehouse and not the warehouse: 6 marts, one Parquet file each, and nothing else. Anything the export does not carry is a refusal here even where the warehouse can answer it.
Target `published`, 6 models, generated 2026-08-15T17:25:29+00:00, prose revision `64e423921de52c85`, spec 2026-08-07, pack 1.0.0.
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

### refuse.export-has-no-row-level-records (absent)

- "list the 311 cases in the Mission last week"
- "show me the individual permits filed on this street"
- "which addresses have the most complaints"
- "how many distinct permits were filed, not records"
- "how many businesses are registered, counting certificates"

**Rule.** Do not answer questions that need one row per case, permit or business registration. This export carries monthly aggregates and two dimension tables. The only per-record model in it is mart_film_locations, which is one row per shoot location.

Why: The export is six marts written as one Parquet file each. 311 cases, building permits and business registrations reach it only as counts per neighborhood or per H3 cell, per dataset, category and month, so there is no case number, no permit number, no certificate number and no address to group by, filter on or count distinct.
Instead: Counts by neighborhood or cell, dataset, category and month, with the rate beside the count. If the question genuinely needs a record, say that this export cannot answer it and that the warehouse the export is written from can.
Evidence: ADR-12; column mart_activity_by_neighborhood.event_count; column mart_activity_by_neighborhood.event_month; model mart_film_locations

### refuse.export-has-no-staging-or-intermediate-models (absent)

- "join this to the staging model"
- "recompute the monthly counts on the issue date instead"
- "deduplicate the underlying rows yourself"
- "group the raw events by something other than these columns"

**Rule.** Do not write a query against a staging or intermediate model. None of them is in this export, and the aggregates here cannot be recomputed on a different basis from what is in the files.

Why: The warehouse has a staging view per source and one intermediate event spine, and the marts are built from them with the grain, the date basis and the geography already chosen. This export is the marts. A different choice of any of the three is a rebuild of the warehouse rather than a different query here.
Instead: Answer from the marts on the basis they were built with, and state that basis in the answer: counts per month by dataset and category, dated the way the mart dates them. If a different basis is required, say the export cannot provide it.
Evidence: ADR-8; ADR-12; column mart_activity_by_neighborhood.dataset

### refuse.export-has-no-h3-bridge (absent)

- "which cells fall in this neighborhood"
- "relabel these cells by supervisor district"
- "spread population across the cells of a district"
- "which cells does this boundary cover"

**Rule.** Do not attempt to relabel an H3 cell or to allocate a measure across cells. The cell-to-boundary bridge is not in this export, and the labels on mart_activity_by_h3 are the only ones available.

Why: In the warehouse a bridge table maps every cell to the boundaries it touches, with three flags for labelling, interior membership and allocation. This export carries the result of that join and not the join: analysis_neighborhood and supervisor_district_id on the H3 mart are the labelling flag already applied, and cell_population is the allocation already done. There is no flag here to choose differently with.
Instead: Use the labels the H3 mart carries, subject to disclose.h3-mart-neighborhood-labels-are-approximate, and prefer mart_activity_by_neighborhood when both marts can answer the question.
Evidence: column mart_activity_by_h3.analysis_neighborhood; column mart_activity_by_h3.supervisor_district_id; column mart_activity_by_h3.cell_population; ADR-5

### refuse.311-measures-reporting-not-incidence (mismeasured)

- "which neighborhood has the most problems"
- "where are the worst conditions in the city"
- "which part of San Francisco is dirtiest"
- "where is graffiti worst"
- "which neighborhood is most neglected"

**Rule.** Do not answer questions about where conditions are worst using 311 volume. Report what was reported, and say so in the answer.

Why: 311 counts reports, not conditions, and reporting propensity varies with who lives somewhere, whether they know the service exists, language, housing tenure, and whether one prolific reporter is active in a block. This warehouse holds no independent measure of incidence to calibrate against, so the direction and size of that bias can be named here and not estimated.
Instead: Which neighborhoods file the most 311 reports of a given category per resident or per business, labelled as reports rather than as conditions. See example `ex.export-reports-per-capita-by-neighborhood`.
Evidence: ADR-10; model mart_activity_by_neighborhood; the size of the reporting bias (not measured in this project)

### refuse.311-is-not-a-safety-measure (mismeasured)

- "which neighborhood is most dangerous"
- "where is it least safe to live"
- "rank neighborhoods by safety"

**Rule.** Refuse this twice and say both halves. There is no crime data in this warehouse, and 311 volume is not a proxy for it. Do not answer a safety question from any column here.

Why: This fails on two independent grounds, and a pack that states only the first invites the substitution the second exists to prevent: with no crime data available, a model looking for the nearest number reaches for 311 volume, which measures who reports rather than what happens.
Instead: Say that neither crime data nor any measure of conditions is present, and offer 311 reports by category per capita as a description of what residents reported, with the label attached. See example `ex.export-reports-per-capita-by-neighborhood`.
Evidence: ADR-10; model mart_activity_by_neighborhood

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

### refuse.export-counts-permit-records-at-filing (mismeasured)

- "how much construction is happening in the Mission"
- "is building activity going up"
- "how many permits were issued last year"
- "how many permits were filed in this neighborhood"

**Rule.** Do not answer construction or permit-count questions from this export. The building_permits rows here count permit records dated at filing, and the export holds no permit number to deduplicate them and no issue date to date them by. Answer about records filed, labelled as such, or refuse.

Why: A permit is filed as several records, since revisions and addenda file separately under one permit number, so a count of records is not a count of permits. Filing is also not issuing and neither is building: a filed permit may never be issued and an issued one may never be built. In the warehouse both of those are answerable with a different query. Here neither is, because the deduplicating column and the issue date are upstream of this export.
Instead: Permit records filed per month by category, which is the permit type, labelled as records filed rather than as permits or as construction. Say that this export cannot count distinct permits.
Evidence: ADR-12; column mart_activity_by_neighborhood.dataset; column mart_activity_by_neighborhood.category; column mart_activity_by_neighborhood.event_count

### refuse.export-counts-registrations-not-businesses (mismeasured)

- "how many businesses are in the Mission"
- "which neighborhood has the most restaurants"
- "how many businesses opened last year"
- "show business openings since 1900"
- "was the city more commercial before the war"

**Rule.** Do not read the business_locations rows of the activity marts as a count of businesses, and do not plot them as a historical series. Use the business counts on the dimension tables for a number of businesses, and say the early months are survivorship rather than history.

Why: Those rows count registry records dated by when each location opened, and one business accumulates records by moving or changing hands. The export has no certificate number to count distinct with. The same column is why the series runs back to the nineteenth century: it is the set of locations the registry holds today, dated backwards, so the far end is whatever the city still keeps rather than what existed at the time.
Instead: dim_neighborhood.business_count and active_business_count for how many businesses, which differ by more than half, so state which one a rate divides by. For a series, restrict the window to recent years and say the registry is a current-state snapshot.
Evidence: column mart_activity_by_neighborhood.event_month; column dim_neighborhood.business_count; column dim_neighborhood.active_business_count; coordinates outside San Francisco, 18.27 percent of business registry rows (measured 2026-07-31)

### refuse.rank-by-raw-count (misnormalised)

- "which neighborhood has the most 311 cases"
- "rank neighborhoods by number of reports"
- "where is the most activity"
- "which cell has the highest count"

**Rule.** Do not rank neighborhoods or cells by event_count. Rank by a rate, name the denominator, and note that the denominators disagree with each other on purpose.

Why: A raw count per area is mostly a map of where people live, so the ranking rediscovers the census. Per 1000 residents and per 1000 businesses return different lists, and that disagreement is information: the Financial District has almost no residents and enormous daytime activity, so its per-capita rate is close to meaningless and its per-business rate is not. This is not the pack's opinion; CLAUDE.md requires every count mart to expose a normalised companion for the same reason.
Instead: Rank by a rate, name the denominator in the answer, and where the question is commercial prefer per 1000 businesses. See example `ex.export-reports-per-capita-by-neighborhood`.
Evidence: column mart_activity_by_neighborhood.event_count; column mart_activity_by_neighborhood.events_per_1000_residents; column mart_activity_by_neighborhood.events_per_1000_businesses; doc CLAUDE.md

### refuse.events-per-sq-km-on-the-h3-mart (misnormalised)

- "which cells are densest in events per square kilometre"
- "does the density agree with the count"
- "show both the count and the density per cell"

**Rule.** Do not present mart_activity_by_h3.events_per_sq_km as a second measure that agrees with the count. It is the count times a constant.

Why: Every cell at a fixed resolution has the same area, so events_per_sq_km ranks identically to event_count by construction. It exists to be comparable with the neighborhood mart, where areas genuinely differ, and for no other reason.
Instead: Rank cells by events_per_1000_residents, which is the normalisation that varies per cell, and use events_per_sq_km only when comparing a cell against a neighborhood. See example `ex.export-h3-cells-ranked-by-rate`.
Evidence: column mart_activity_by_h3.events_per_sq_km; column mart_activity_by_h3.cell_area_sq_km; column mart_activity_by_neighborhood.events_per_sq_km

### refuse.per-capita-divides-by-april-2020 (misnormalised)

- "has the rate per resident changed since 2024"
- "which neighborhood grew fastest"
- "are complaints per capita rising"

**Rule.** State the denominator's vintage in any answer that uses a per-capita rate, and never attribute a change in such a rate to population change.

Why: Population is the 2020 Decennial count, because the ACS API now requires a key and ADR-1 keeps credentials off the ingestion path. Every per-capita rate here divides recent events by an April 2020 denominator, so neighborhoods that have grown or shrunk since then are systematically off and no change in a rate over time can be attributed to population.
Instead: Give the rate with the denominator labelled as the April 2020 Census count, and prefer per 1000 businesses where the question is commercial. See example `ex.export-rate-with-denominator-vintage`.
Evidence: ADR-1; column dim_neighborhood.population; column mart_activity_by_neighborhood.events_per_1000_residents

### refuse.null-rate-is-not-a-low-rate (misnormalised)

- "which area has the lowest complaint rate"
- "where are the quietest cells"
- "which neighborhood has the fewest reports per resident"

**Rule.** Never answer a lowest-rate question without excluding null rates explicitly and saying how many were excluded.

Why: events_per_1000_residents is null, not zero, where the denominator is zero. That is correct and common: the bay, the Presidio and the Financial District all have real activity and close to nobody living in them. An area with no residents does not have an infinite complaint rate; it has a question that does not apply.
Instead: Exclude the nulls in the query, report how many rows that removed, and say that those areas have no denominator rather than a low rate. See example `ex.export-lowest-rate-with-exclusions-counted`.
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

### disclose.export-cell-population-is-interpolated

When: Any answer using mart_activity_by_h3.cell_population, or a per-capita rate computed from it.
**State.** A cell's population is interpolated twice and is not a census count: residents are assumed spread evenly within a census block group, and the share falling in each H3 cell is allocated from there. Treat it as an estimate good to about a percent in aggregate and not as a population of that hexagon, and never report it for a single cell as though it were measured.
Why: The Census publishes population by block group. Block groups do not nest inside neighborhoods or inside hexagons, so this project spreads block group population over the cells each one covers rather than clipping polygons. The neighborhood denominators in dim_neighborhood are the same arithmetic summed back up, which is why a rate from the neighborhood mart is the more defensible of the two.
Evidence: column mart_activity_by_h3.cell_population; column mart_activity_by_h3.events_per_1000_residents; column dim_neighborhood.population; ADR-6

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

## Models

### dim_neighborhood (mart, table, 41 rows)

Grain: One row per analysis neighborhood, 41 rows.

| column | type | description |
|---|---|---|
| analysis_neighborhood | VARCHAR | Neighborhood name. The grain of this model. 41 distinct, e.g. Bayview Hunters Point, Bernal Heights, Castro/Upper Market, Chinatown, Excelsior |
| area_sq_km | DOUBLE | Spherical area from ingestion/geometry.py. min 0.3125, median 2.138, max 13.39 |
| published_area_sq_mi | DOUBLE | (no description in the yml) min 0.1207, median 0.8258, max 5.173 |
| population | BIGINT | Interpolated 2020 residents. Zero is possible in principle and does not occur today; every rate divides through x_safe_divide anyway. min 178, median 2.01e+04, max 74,656 |
| housing_units | BIGINT | Interpolated 2020 housing units. min 63, median 8,980, max 28,042 |
| business_count | BIGINT | Registered business locations, ever. The right denominator for a historical commercial rate; use active_business_count for a current one, and note the two differ by more than half. min 39, median 6,049, max 44,790 |
| active_business_count | DOUBLE | (no description in the yml) min 18, median 2,059, max 1.427e+04 |
| population_per_sq_km | DOUBLE | (no description in the yml) min 102.6, median 8,889, max 3.547e+04 |
| businesses_per_sq_km | DOUBLE | (no description in the yml) min 28.89, median 2,256, max 1.64e+04 |
| h3_cell_count | BIGINT | How many r10 cells this neighborhood owns. A coverage diagnostic: a neighborhood whose cell count is out of line with its area has a boundary problem, not a population problem. values: 82 7.3%, 131 4.9%, 66 4.9%, 101 2.4%, 106 2.4%, 113 2.4%, 120 2.4%, 135 2.4%, and 29 more |
| geojson | VARCHAR | (no description in the yml) 41 distinct |

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
| business_count | BIGINT | (no description in the yml) min 11,948, median 2.315e+04, max 57,478 |
| active_business_count | DOUBLE | (no description in the yml) min 4,075, median 7,755, max 1.933e+04 |
| population_per_sq_km | DOUBLE | (no description in the yml) min 287.8, median 7,814, max 1.784e+04 |
| h3_cell_count | BIGINT | (no description in the yml) min 291, median 595, max 16,767 |
| geojson | VARCHAR | (no description in the yml) 11 distinct |

### mart_activity_by_h3 (mart, table, 144,049 rows)

Grain: One row per H3 cell per dataset per category per month, at var('h3_mart_resolution'), currently 8.

| column | type | description |
|---|---|---|
| h3_cell | BIGINT | The cell as a BIGINT. Part of the grain. 15,790 distinct |
| h3_resolution | INTEGER | The resolution this mart was built at. Constant per build. 1 distinct |
| dataset | VARCHAR | Registry name of the source. Part of the grain. values: business_locations 83.2%, building_permits 10.1%, 311_cases 6.7% |
| category | VARCHAR | The dataset's own category dimension: service type for 311, permit type for permits, licence description for businesses. Part of the grain. One column name over three vocabularies, so grouping by it without also grouping by dataset pools three unrelated taxonomies. 176 distinct, e.g. Unknown, otc alterations permit, Multiple, additions alterations or repairs, RESTAURANT 1,000 - 2,000 SQFT |
| event_month | TIMESTAMP | First day of the month. Part of the grain. 1849-05-01T00:00:00 to 2028-02-01T00:00:00; newest complete month 2028-01-01: 0 rows |
| analysis_neighborhood | VARCHAR | The neighborhood owning this cell, for filtering and labelling. Null where the cell's centre is outside every neighborhood, which is water or just past the city line. 43.7% null; values: Sunset/Parkside 4.7%, Mission 4.3%, Bayview Hunters Point 4.2%, West of Twin Peaks 3.0%, Financial District 2.2%, Outer Richmond 2.1%, South of Market 2.1%, Bernal Heights 1.7%, and 33 more |
| supervisor_district_id | VARCHAR | The supervisor district owning this cell, as a string, on the 2022 boundaries. Null under the same rule as analysis_neighborhood. Join to dim_supervisor_district on this rather than on the integer district number, which is what the id exists for. 43.6% null; values: 7.0 6.6%, 10.0 6.5%, 8.0 5.8%, 9.0 5.7%, 2.0 5.6%, 3.0 5.1%, 4.0 4.7%, 1.0 4.7%, and 3 more |
| event_count | BIGINT | Events in this cell, dataset, category and month. min 1, median 1, max 1,745 |
| cell_population | DOUBLE | Interpolated residents in this cell. Null where the cell has no block group overlap at all, zero where it genuinely has nobody. 40.6% null; min 0, median 6,199, max 2.943e+04 |
| cell_housing_units | DOUBLE | Interpolated housing units in this cell, same method and same caveat as cell_population, and null under the same condition. 40.6% null; min 0, median 2,568, max 1.611e+04 |
| cell_area_sq_km | DECIMAL(11,10) | The H3 constant for this resolution, carried so a density can be re-derived without another lookup. Identical on every row of a build, which is exactly why events_per_sq_km ranks like the count. values: 0.7373 100.0% |
| events_per_1000_residents | DOUBLE | The normalised companion, and the one to rank on. Null rather than infinite where the cell has no residents, which is correct and common: the bay, the Presidio and the Financial District all have real activity and close to nobody living in them. 40.8% null; min 0.03398, median 0.3992, max 676.1 |
| events_per_1000_housing_units | DOUBLE | The same normalisation against dwellings rather than people. Useful where the question is about the building stock; null where the cell has no housing units, for the same reason as above. 40.8% null; min 0.06207, median 0.918, max 1.8e+04 |
| events_per_sq_km | DOUBLE | Density. A constant rescaling of event_count at fixed resolution, so it ranks identically; kept for comparability with the neighborhood mart. min 1.356, median 1.356, max 2,367 |

### mart_activity_by_neighborhood (mart, table, 41,965 rows)

Grain: One row per neighborhood per dataset per category per month.

| column | type | description |
|---|---|---|
| analysis_neighborhood | VARCHAR | Neighborhood name. Part of the grain. values: Financial District 5.4%, Mission 5.2%, South of Market 3.9%, Bayview Hunters Point 3.9%, Sunset/Parkside 3.7%, Tenderloin 3.6%, Chinatown 3.4%, Marina 3.3%, and 33 more |
| dataset | VARCHAR | Registry name of the source. Part of the grain. values: business_locations 68.5%, building_permits 21.6%, 311_cases 9.9% |
| category | VARCHAR | The dataset's own category dimension. Part of the grain. 172 distinct, e.g. Unknown, otc alterations permit, Multiple, additions alterations or repairs, RESTAURANT - UNDER 1,000 SQFT |
| event_month | TIMESTAMP | First day of the month. Part of the grain. 1849-05-01T00:00:00 to 2028-02-01T00:00:00; newest complete month 2028-01-01: 0 rows |
| event_count | BIGINT | Events in this neighborhood, dataset, category and month. min 1, median 2, max 4,707 |
| population | BIGINT | Interpolated 2020 residents in this neighborhood, from dim_neighborhood. The denominator behind events_per_1000_residents, and an April 2020 count regardless of which month the events are from. values: 23,908 5.4%, 58,062 5.2%, 28,426 3.9%, 40,384 3.9%, 74,656 3.7%, 36,067 3.6%, 14,455 3.4%, 23,325 3.3%, and 33 more |
| housing_units | BIGINT | Interpolated 2020 housing units, the denominator behind the rate below. values: 16,050 5.4%, 26,545 5.2%, 15,863 3.9%, 12,437 3.9%, 28,042 3.7%, 20,807 3.6%, 7,617 3.4%, 14,164 3.3%, and 33 more |
| business_count | BIGINT | Registered business locations ever, the denominator behind events_per_1000_businesses. Not the active count, which is the one dim_neighborhood carries separately and which is less than half of this. values: 44,790 5.4%, 23,480 5.2%, 18,345 3.9%, 13,361 3.9%, 13,972 3.7%, 8,931 3.6%, 9,537 3.4%, 9,682 3.3%, and 33 more |
| area_sq_km | DOUBLE | Spherical land area. Unlike the H3 mart this genuinely differs per row, which is what makes events_per_sq_km a real second measure here and a rescaled count there. values: 2.909 5.4%, 4.876 5.2%, 2.291 3.9%, 13.39 3.9%, 10.95 3.7%, 1.017 3.6%, 0.5816 3.4%, 2.624 3.3%, and 33 more |
| events_per_1000_residents | DOUBLE | The default normalised companion. Null where the neighborhood has no residents. min 0.01339, median 0.09905, max 548 |
| events_per_1000_housing_units | DOUBLE | The same normalisation against dwellings. Null where the neighborhood has no housing units. min 0.03566, median 0.2087, max 2,024 |
| events_per_1000_businesses | DOUBLE | The right companion for anything commercial, and the one that most changes the ranking against per-capita. min 0.02233, median 0.3291, max 1,946 |
| events_per_sq_km | DOUBLE | Density. Unlike the H3 mart, neighborhood areas genuinely differ. min 0.07467, median 0.9833, max 2,277 |

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
| row_count | BIGINT | Rows currently in the raw table, counted directly rather than accumulated. min 11, median 2,214, max 2,921,878 |
| row_delta | BIGINT | Rows the most recent ingestion run added. 0 is the common healthy case: the run went out, found nothing new, and wrote nothing. values: 0 57.1%, 29,574 14.3%, 365,400 14.3%, 544 14.3% |
| previous_row_count | BIGINT | row_count minus row_delta, i.e. the count before the last run. min 11, median 2,214, max 2,556,478 |
| last_load_at | TIMESTAMP | When rows last landed in the raw zone. Null if nothing has ever loaded. 2026-07-31T06:56:41.226332 to 2026-08-15T16:13:11.286330; newest complete month 2026-07-01: 4 rows |
| last_ingest_run_id | VARCHAR | (no description in the yml) 7 distinct, e.g. 20260731T065640Z, 20260731T093034Z, 20260731T093037Z, 20260731T093038Z, 20260815T160922Z |
| last_run_finished_at | TIMESTAMP | When ingestion last ran at all, successful or not. Later than last_load_at whenever recent runs found nothing new. 2026-08-15T16:09:45.549805 to 2026-08-15T16:13:18.954474; newest complete month 2026-07-01: 0 rows |
| last_run_status | VARCHAR | success or failed, from the ingestion run manifest. values: success 100.0% |
| last_run_mode | VARCHAR | (no description in the yml) values: incremental 100.0% |
| hours_since_load | DOUBLE | Hours since last_load_at, fractional, in UTC on both engines. See x_utc_now in macros/cross_engine.sql for why that needed saying. min 0.9464, median 367.7, max 370.2 |
| hours_since_run_attempt | DOUBLE | Hours since ingestion last ran, fractional. min 0.9444, median 0.9458, max 1.004 |
| stale_after_hours | INTEGER | Freshness SLA in hours. Null means the source has no SLA. 57.1% null; values: 168 28.6%, 48 14.3% |
| is_stale | BOOLEAN | Whether hours_since_load has passed stale_after_hours. Always false for sources with no SLA, so this never fires on a demoted source. 0.0% true; 1 distinct |
| point_count | BIGINT | Rows this source contributed to the spatial precompute. Null for a source with no point geometry, which is how a non-spatial source is told apart from a spatial one whose coordinates all failed. 42.9% null; min 2,214, median 8.987e+04, max 365,400 |
| usable_point_count | DOUBLE | Of those, how many produced a coordinate inside San Francisco. 42.9% null; min 2,127, median 8.901e+04, max 2.986e+05 |
| missing_coordinate_count | DOUBLE | Rows with no coordinate at all. Expected to be nonzero forever and deliberately not counted against health. 42.9% null; min 65, median 865.5, max 1.093e+04 |
| out_of_bounds_count | DOUBLE | Rows whose coordinate is a real place outside San Francisco. Almost entirely registered businesses located elsewhere, which is correct data, so this does not count against health either. 42.9% null; values: 0 28.6%, 1 14.3%, 5.589e+04 14.3% |
| malformed_coordinate_count | DOUBLE | Rows whose coordinate could not be parsed or was not on Earth. Unlike the two above this is a pipeline fault, not a fact about the world, so any value above zero makes is_healthy false. It is the shape an upstream column change takes. 42.9% null; values: 0 57.1% |
| coordinate_drop_rate_pct | DOUBLE | Percentage of this source's rows that could not be placed on a map, all four reasons combined. Measured 2026-07-31: 311 1.20, permits 0.12, street trees 1.58, film locations 3.93, business locations 18.27. The last is high because the registry records businesses located outside the city, not because it is dirty. 42.9% null; min 0.1681, median 2.548, max 18.29 |
| tests_total | BIGINT | Tests run against this source's staging model in the last completed dbt run. values: 10 28.6%, 11 28.6%, 3 14.3%, 4 14.3%, 9 14.3% |
| tests_passed | DOUBLE | Of those, how many passed. values: 10 28.6%, 11 28.6%, 3 14.3%, 4 14.3%, 9 14.3% |
| tests_failed | DOUBLE | Of those, how many failed. values: 0 100.0% |
| tests_warned | DOUBLE | Of those, how many warned. Warnings are signals, not failures. values: 0 100.0% |
| tests_errored | DOUBLE | Of those, how many errored, meaning the test itself could not run. values: 0 100.0% |
| last_test_run_at | TIMESTAMP | When that dbt run started. Null before the second ever run. 2026-08-11T01:05:30 to 2026-08-11T01:05:30; newest complete month 2026-07-01: 0 rows |
| is_healthy | BOOLEAN | False if the last ingestion run failed, if any test failed or errored, if any coordinate was malformed, or if the source is past its SLA. True otherwise. The single column to read when checking in. 100.0% true; 1 distinct |

## Join map

| from | to | cardinality | safe |
|---|---|---|---|
| mart_activity_by_h3.analysis_neighborhood | dim_neighborhood.analysis_neighborhood | many to one | yes |
| mart_activity_by_neighborhood.analysis_neighborhood | dim_neighborhood.analysis_neighborhood | many to one | yes |
| mart_film_locations.analysis_neighborhood | dim_neighborhood.analysis_neighborhood | many to one | yes |
| mart_film_locations.upstream_analysis_neighborhood | dim_neighborhood.analysis_neighborhood | many to one | NO |
| mart_activity_by_h3.supervisor_district_id | dim_supervisor_district.supervisor_district_id | many to one | yes |

- `mart_film_locations.upstream_analysis_neighborhood` to `dim_neighborhood.analysis_neighborhood`: The dirty join, and the worked example of the class. This is the neighborhood DataSF stamped on the row, not the one computed here from the coordinates. It will mostly match, which is what makes it dangerous: the join succeeds and answers a different question. Join on analysis_neighborhood instead.
- `mart_activity_by_h3.supervisor_district_id` to `dim_supervisor_district.supervisor_district_id`: Join on the string id rather than on the integer district number: the dimension carries both for exactly this reason, so neither side needs a cast. The label is subject to the cell-membership disclosure.

## Verified examples

Every query below was executed against this target at generation time. The row count is what it returned.

### ex.export-reports-per-capita-by-neighborhood

Which neighborhoods report the most street cleaning per resident, and does the answer hold when the denominator changes?

```sql
-- Reports per resident, not reports, and the second denominator beside the
-- first because they disagree on purpose. The export needs no join for this:
-- mart_activity_by_neighborhood carries its own denominators.
select
    analysis_neighborhood,
    sum(event_count) as reports,
    max(population) as residents_april_2020,
    max(business_count) as businesses,
    round(1000.0 * sum(event_count) / max(population), 1) as reports_per_1000_residents,
    round(1000.0 * sum(event_count) / max(business_count), 1) as reports_per_1000_businesses
from mart_activity_by_neighborhood
where dataset = '311_cases'
    and category = 'Street and Sidewalk Cleaning'
    and population > 0
    and business_count > 0
group by analysis_neighborhood
order by reports_per_1000_residents desc
limit 15
```

Demonstrates: refuse.rank-by-raw-count, refuse.311-measures-reporting-not-incidence, refuse.311-is-not-a-safety-measure. Verified against published at 2026-08-15T17:25:29+00:00, 15 rows.

### ex.export-h3-cells-ranked-by-rate

Which hexagons are noisier than their population explains?

```sql
-- The refusal made checkable in one result set. Ranking cells by
-- events_per_sq_km returns the count's order exactly, because every cell at a
-- fixed resolution has the same area; the per-resident rate does not.
with per_cell as (
    select
        h3_cell,
        analysis_neighborhood,
        sum(event_count) as events,
        max(cell_area_sq_km) as cell_area_sq_km,
        max(cell_population) as cell_population
    from mart_activity_by_h3
    where dataset = '311_cases'
        and cell_population > 0
    group by h3_cell, analysis_neighborhood
)
select
    h3_cell,
    analysis_neighborhood,
    events,
    rank() over (order by events desc) as rank_by_count,
    rank() over (order by events / cell_area_sq_km desc) as rank_by_events_per_sq_km,
    round(1000.0 * events / cell_population, 1) as events_per_1000_residents,
    rank() over (order by events / cell_population desc) as rank_by_rate
from per_cell
order by events_per_1000_residents desc
limit 12
```

Demonstrates: refuse.events-per-sq-km-on-the-h3-mart. Verified against published at 2026-08-15T17:25:29+00:00, 12 rows.

### ex.export-rate-with-denominator-vintage

What is the 311 rate per resident by neighborhood, with the denominator's vintage stated?

```sql
-- The vintage is a column, and so is the window it divides, because the two do
-- not match: April 2020 residents under events from 2024 onward. Anything
-- reading this result can see the gap without being told about it separately.
select
    analysis_neighborhood,
    'April 2020 Decennial Census' as denominator_vintage,
    max(population) as residents,
    min(event_month) as first_event_month,
    max(event_month) as last_event_month,
    sum(event_count) as events,
    round(1000.0 * sum(event_count) / max(population), 1) as events_per_1000_residents
from mart_activity_by_neighborhood
where dataset = '311_cases'
    and population > 0
group by analysis_neighborhood
order by events_per_1000_residents desc
limit 12
```

Demonstrates: refuse.per-capita-divides-by-april-2020. Verified against published at 2026-08-15T17:25:29+00:00, 12 rows.

### ex.export-lowest-rate-with-exclusions-counted

Which hexagons have the lowest 311 rate per resident, and how many were excluded for having no residents?

```sql
-- A lowest-rate answer is only honest alongside the number of areas the
-- question does not apply to, so that count rides in every row rather than in a
-- caveat. Cells with no residents are excluded and counted, never read as zero.
with per_cell as (
    select
        h3_cell,
        analysis_neighborhood,
        sum(event_count) as events,
        max(cell_population) as cell_population
    from mart_activity_by_h3
    where dataset = '311_cases'
    group by h3_cell, analysis_neighborhood
),
counted as (
    select
        count(*) as cells_total,
        count(*) filter (where coalesce(cell_population, 0) = 0) as cells_excluded_no_residents
    from per_cell
)
select
    p.h3_cell,
    p.analysis_neighborhood,
    p.events,
    round(p.cell_population, 1) as cell_population,
    round(1000.0 * p.events / p.cell_population, 1) as events_per_1000_residents,
    c.cells_excluded_no_residents,
    c.cells_total
from per_cell as p
cross join counted as c
where p.cell_population > 0
order by events_per_1000_residents asc, p.h3_cell
limit 10
```

Demonstrates: refuse.null-rate-is-not-a-low-rate. Verified against published at 2026-08-15T17:25:29+00:00, 10 rows.

## Freshness

published/manifest.json. published_at is when this export was written, which is the age of the files you are reading. The per-source rows are mart_pipeline_freshness as it stood in the build this export was written from: last_load_at is when rows landed in the raw zone, not when this export was published, and the two have been days apart. Neither number is the other's substitute.

**Published at 2026-08-15T17:10:09.680022+00:00**, manifest version 2.

| source | tier | row_count | last_load_at | last_run_finished_at | stale_after_hours | is_stale |
|---|---|---|---|---|---|---|
| 311_cases | core | 527,079 | 2026-08-15T16:09:43.019249 | 2026-08-15T16:09:45.549805 | 48 | false |
| analysis_neighborhoods | reference | 41 | 2026-07-31T09:30:36.846363 | 2026-08-15T16:13:13.632434 | none | false |
| building_permits | core | 44,643 | 2026-08-15T16:09:48.224041 | 2026-08-15T16:09:48.639405 | 168 | false |
| business_locations | core | 2,921,878 | 2026-08-15T16:13:11.286330 | 2026-08-15T16:13:12.162086 | 168 | false |
| census_block_groups | reference | 681 | 2026-07-31T09:30:40.288231 | 2026-08-15T16:13:17.742120 | none | false |
| film_locations | demoted | 2,214 | 2026-07-31T06:56:41.226332 | 2026-08-15T16:13:18.954474 | none | false |
| supervisor_districts | reference | 11 | 2026-07-31T09:30:38.067363 | 2026-08-15T16:13:16.906501 | none | false |

## Integrity

Before trusting this pack, compare its integrity block against the target itself: the schema hash of every model you intend to query, and the dbt invocation it was built from. If they disagree, this pack describes something the target does not contain, and the correct response is to refuse every question rather than to answer from a stale description.

Built from dbt invocation `53450eca-97c0-4541-98b0-30fa0dc51db7` (1.12.0, adapter duckdb), manifest generated 2026-08-15T17:09:56.243164Z.

schema_hash is over the Parquet as read, which is what you opened. published_manifest_schema_hashes are publish/export.py's, over the warehouse tables the export was written from, copied from published/manifest.json unmodified. Compare the second against the manifest in the bucket and the first against the files.

| model | schema hash | manifest schema hash | rows |
|---|---|---|---|
| dim_neighborhood | a45c3b61db7d3719 | d6ab3c72cd7f141e | 41 |
| dim_supervisor_district | ca53a22ebf03c6d9 | 58c1e954a3a26872 | 11 |
| mart_activity_by_h3 | 0e29db73e198cd99 | 0e29db73e198cd99 | 144,049 |
| mart_activity_by_neighborhood | c206f557c69a5990 | c206f557c69a5990 | 41,965 |
| mart_film_locations | c65a9053b2458bb4 | c65a9053b2458bb4 | 2,214 |
| mart_pipeline_freshness | 2bf00211bd04d5a1 | 6552dcb2fdafb99d | 7 |

The two disagree on dim_neighborhood, dim_supervisor_district, mart_pipeline_freshness: HUGEINT has no Parquet type, so DuckDB writes those columns as DOUBLE and the two hashes differ for a schema that is otherwise identical. A count read from this export is a float. Expected, and not a sign that the export is corrupt.
