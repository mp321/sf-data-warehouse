"""What the context pack generator must fail on, and what it must never drop.

PLAN-6's testing section asks for three things: a round trip on the JSON, a test
that the compact markdown stays under budget, and a test that a deliberately
stale pack is rejected. All three are here, plus the two rules the spec says are
easy to implement as warnings by mistake:

    - an entry citing something the target does not have fails generation
    - a refusal is never dropped to fit the budget

The never-dropped rule covers traps as well as of 2026-08-07, when ADR-13 put
the traps block into the markdown. That is one rendering decision with a test
each way: traps are rendered at a generous budget, and they are still there at
every budget the ladder can satisfy.

None of this touches the real warehouse or the dbt manifest, both of which are
gitignored. The profile tests build a two-table DuckDB in memory instead, so
this suite runs on a fresh clone with no pipeline, which is what lets it sit in
the same pytest job as the geometry tests.
"""

import hashlib
import json

import duckdb
import pytest

from generate import build_examples_block, disagreements
from pack_inputs import grain_sentence
from pack_profile import column_shape, profile_model
from pack_prose import (
    ProseError,
    TargetFacts,
    check_class_three_examples,
    check_join_duplication,
    load_prose,
    resolve_citation,
    select_for_target,
    validate_shape,
)
from pack_render import BudgetExceeded, estimate_tokens, render
from pack_target import TARGET_NAMES, Target

# ---------------------------------------------------------------------------
# Fixtures: a target, and a pack shaped like the real one but small
# ---------------------------------------------------------------------------


@pytest.fixture
def target():
    connection = duckdb.connect(":memory:")
    connection.execute(
        """
        create table main.events as
        select * from (values
            ('311_cases', 'Graffiti', 8, 1.5, timestamp '2026-05-04', true, 613196575465799679),
            ('311_cases', 'Graffiti', 3, null,  timestamp '2026-06-04', true, 613196575465799679),
            ('311_cases', 'Cleaning', 5, 2.5,  timestamp '2026-06-11', false, 613196575453216767),
            ('permits',   'Cleaning', 2, 4.5,  timestamp '2026-07-02', false, 613196575453216767)
        ) as t(dataset, category, event_count, rate, event_month, is_open, h3_cell)
        """
    )
    yield Target("duckdb", connection, "main", ["events"])
    connection.close()


def a_pack(**overrides) -> dict:
    """A structurally complete pack with one model and one refusal of each kind."""
    pack = {
        "spec_version": "2026-08-05",
        "pack_version": "1.0.0",
        "target": "duckdb",
        "generated_at": "2026-08-06T00:00:00+00:00",
        "prose_revision": "abcdef0123456789",
        "identity": {
            "name": "sf-data-warehouse",
            "description": "A warehouse.",
            "publisher": "DataSF",
            "jurisdiction": "San Francisco, California",
            "licence": "Public domain.",
            "repo": "https://example.invalid/repo",
            "source_urls": [],
            "update_cadence": [],
            "how_to_read": "Read the refusals first.",
        },
        "build": {"invocation_id": "abc", "dbt_version": "1.12.0"},
        "models": [
            {
                "name": "events",
                "layer": "mart",
                "materialisation": "table",
                "grain": "One row per event.",
                "row_count": 4,
                "depends_on": [],
                "columns": [
                    {
                        "name": "dataset",
                        "type": "VARCHAR",
                        "description": "Registry name of the source.",
                        "profile": {"null_rate": 0.0, "distinct_count": 2},
                        "low_signal": False,
                    },
                    {
                        "name": "filler",
                        "type": "VARCHAR",
                        "description": "",
                        "profile": {"null_rate": 0.0, "distinct_count": 1},
                        "low_signal": True,
                    },
                ],
            }
        ],
        "joins": [
            {
                "from": "events.dataset",
                "to": "sources.name",
                "on": "events.dataset = sources.name",
                "cardinality": "many to one",
                "safe": True,
                "why": "",
                "declared_by": "relationships test",
            }
        ],
        "freshness": {"basis": "mart_pipeline_freshness, projected.", "sources": []},
        "traps": [
            {
                "id": "trap.category-means-something-different-per-dataset",
                "state": "Group by dataset whenever you group by category.",
                "why": "One column name, three vocabularies.",
                "evidence": [{"kind": "column", "ref": "events.category"}],
            }
        ],
        "refusals": {
            "no_ground_truth": "No ground truth here.",
            "census_exception": "Except the census.",
            "closed_world_rule": "Refuse what is not in this pack.",
            "entries": [
                {
                    "id": "refuse.absent-thing",
                    "class": "absent",
                    "question_shapes": ["where is the thing"],
                    "rule": "Do not answer about the thing.",
                    "why": "It is not here.",
                    "evidence": [{"kind": "model", "ref": "events"}],
                    "instead": {"answer": "Say what is here."},
                },
                {
                    "id": "refuse.rank-by-raw-count",
                    "class": "misnormalised",
                    "question_shapes": ["which area has the most"],
                    "rule": "Rank by a rate.",
                    "why": "A count is a population map.",
                    "evidence": [{"kind": "column", "ref": "events.event_count"}],
                    "instead": {"answer": "Rank by a rate.", "example": "ex.rate"},
                },
            ],
        },
        "disclosures": [
            {
                "id": "disclose.something",
                "when": "Any answer using the rate.",
                "state": "The denominator is from 2020.",
                "why": "The census is decennial.",
                "evidence": [{"kind": "measurement", "ref": "the lag", "measured": False}],
            }
        ],
        "examples": [
            {
                "id": "ex.rate",
                "question": "What is the rate?",
                "sql": "select 1",
                "demonstrates": ["refuse.rank-by-raw-count"],
                "required": True,
                "verified": {"target": "duckdb", "at": "now", "rows": 1, "sql_sha256": "x"},
            },
            {
                "id": "ex.surplus",
                "question": "Anything else?",
                "sql": "select 2",
                "demonstrates": [],
                "required": False,
                "verified": {"target": "duckdb", "at": "now", "rows": 1, "sql_sha256": "y"},
            },
        ],
        "integrity": {
            "self_refusal": "Compare this against the target.",
            "dbt": {
                "invocation_id": "abc",
                "manifest_generated_at": "2026-08-06T00:00:00Z",
                "dbt_version": "1.12.0",
                "adapter_type": "duckdb",
            },
            "models": {"events": {"schema_hash": "1111222233334444", "row_count": 4}},
        },
    }
    pack.update(overrides)
    return pack


# ---------------------------------------------------------------------------
# The grain sentence, which is a hard failure when it is missing
# ---------------------------------------------------------------------------


def test_grain_sentence_is_the_first_one_row_per_sentence():
    description = "One row per 311 case, deduplicated. Columns are renamed here."
    assert grain_sentence(description) == "One row per 311 case, deduplicated."


def test_grain_sentence_survives_folded_yaml_newlines():
    description = "One row per boundary per resolution per\ncovering H3 cell. The bridge table."
    expected = "One row per boundary per resolution per covering H3 cell."
    assert grain_sentence(description) == expected


def test_grain_sentence_does_not_stop_at_an_abbreviation():
    description = "One row per source, e.g. 311_cases, per month. Then more prose."
    assert grain_sentence(description) == "One row per source, e.g. 311_cases, per month."


def test_grain_sentence_is_none_when_there_is_no_grain():
    assert grain_sentence("A table of things that are useful.") is None


# ---------------------------------------------------------------------------
# Profiles: the rules in section 4.3, one test each for the ones with teeth
# ---------------------------------------------------------------------------


def test_column_shape_puts_h3_cells_before_numbers():
    # A BIGINT cell id would otherwise get a min, a max and a median, which are
    # three meaningless numbers.
    assert column_shape("h3_cell", "BIGINT") == "h3_cell"
    assert column_shape("h3_r10", "BIGINT") == "h3_cell"
    assert column_shape("event_count", "BIGINT") == "numeric"
    assert column_shape("geojson", "VARCHAR") == "blob"
    assert column_shape("is_open", "BOOLEAN") == "boolean"
    assert column_shape("event_month", "TIMESTAMP") == "temporal"


def test_profile_carries_the_right_statistic_per_shape(target):
    columns = target.columns("events")
    profiles = profile_model(target, "events", columns, 4)

    # Low cardinality: every value with its share, and only where values repeat.
    assert profiles["dataset"]["values"] == [
        {"value": "311_cases", "share": 0.75},
        {"value": "permits", "share": 0.25},
    ]
    # Numeric: min, max, median, and a null rate that is not silently zero.
    assert profiles["rate"]["null_rate"] == 0.25
    assert profiles["rate"]["min"] == 1.5
    assert profiles["rate"]["max"] == 4.5
    # Boolean: the true share.
    assert profiles["is_open"]["true_share"] == 0.5
    # H3: a distinct count and nothing else.
    assert profiles["h3_cell"]["distinct_count"] == 2
    assert "examples" not in profiles["h3_cell"]
    assert "median" not in profiles["h3_cell"]


def test_newest_complete_month_comes_from_the_data_not_the_clock(target):
    """The month before the month the maximum falls in.

    Reading it off today's date would make the pack change at midnight on the
    first of the month with no data having moved, which turns the CI drift check
    into a monthly false alarm.
    """
    profiles = profile_model(target, "events", target.columns("events"), 4)
    assert profiles["event_month"]["max"].startswith("2026-07")
    assert profiles["event_month"]["newest_complete_month"] == "2026-06-01"
    assert profiles["event_month"]["newest_complete_month_count"] == 2


def test_an_empty_model_is_said_once_rather_than_profiled(target):
    target.execute("create table main.empty_events as select * from main.events where false")
    target.model_names.append("empty_events")
    profiles = profile_model(target, "empty_events", target.columns("empty_events"), 0)
    assert profiles["dataset"] == {"null_rate": None, "note": "the model holds no rows"}


# ---------------------------------------------------------------------------
# The round trip, and the budget ladder
# ---------------------------------------------------------------------------


def test_pack_round_trips_through_json():
    pack = a_pack()
    assert json.loads(json.dumps(pack)) == pack


def test_markdown_carries_nothing_the_json_does_not():
    """The one direction the spec fixes: the markdown may carry less, never more.

    Checked on the parts a reader acts on, which are the ids and the SQL.
    """
    pack = a_pack()
    markdown, _ = render(pack, budget=100000)
    for entry in pack["refusals"]["entries"]:
        assert entry["id"] in markdown
        assert entry["rule"] in markdown
    for entry in pack["disclosures"]:
        assert entry["state"] in markdown
    for entry in pack["traps"]:
        assert entry["state"] in markdown
    for example in pack["examples"]:
        assert example["sql"] in markdown
    assert pack["models"][0]["grain"] in markdown


def test_the_traps_block_is_rendered_before_the_schema():
    """ADR-13. A trap changes the query that gets written, so it has to arrive first.

    Ordering rather than mere presence: a warning that lands after the schema
    has to overturn a draft rather than shape one, which is section 9's argument
    for putting refusals first and applies unchanged here.
    """
    pack = a_pack()
    markdown, _ = render(pack, budget=100000)
    assert markdown.index("## Traps") < markdown.index("## Models")
    assert markdown.index("## Mandatory disclosures") < markdown.index("## Traps")


def test_a_generous_budget_drops_nothing():
    _, report = render(a_pack(), budget=100000)
    assert report["dropped"] == []
    assert report["estimated_tokens"] <= 100000


def test_the_budget_drops_in_the_documented_order():
    pack = a_pack()
    full, _ = render(pack, budget=100000)
    ladder = []
    budget = estimate_tokens(full)
    for _ in range(4):
        budget -= 1
        text, report = render(pack, budget=budget)
        ladder.append(tuple(report["dropped"]))
        budget = estimate_tokens(text)
    assert ladder[0] == ("surplus examples",)
    assert ladder[1] == ("surplus examples", "undocumented-column markers")
    assert ladder[2][:3] == (
        "surplus examples",
        "undocumented-column markers",
        "profile statistics",
    )


def test_a_surplus_example_goes_and_a_required_one_stays():
    pack = a_pack()
    full, _ = render(pack, budget=100000)
    text, report = render(pack, budget=estimate_tokens(full) - 1)
    assert report["dropped"] == ["surplus examples"]
    assert "ex.rate" in text
    assert "ex.surplus" not in text


def test_refusals_traps_and_grains_are_never_dropped_to_fit():
    """Section 9. A pack missing a refusal is worse than no pack: it reads complete.

    Traps are on this list rather than in the ladder as of ADR-13, so a budget
    that squeezes out every statistic still carries all four kinds of prose.
    """
    pack = a_pack()
    for budget in range(200, 2000, 100):
        try:
            text, _ = render(pack, budget=budget)
        except BudgetExceeded:
            continue
        for entry in pack["refusals"]["entries"]:
            assert entry["rule"] in text
        for entry in pack["disclosures"]:
            assert entry["state"] in text
        for entry in pack["traps"]:
            assert entry["state"] in text
        assert pack["models"][0]["grain"] in text


def test_a_budget_nothing_can_fit_fails_rather_than_truncating():
    with pytest.raises(BudgetExceeded) as error:
        render(a_pack(), budget=10)
    assert "never dropped" in str(error.value)


# ---------------------------------------------------------------------------
# A deliberately stale pack is rejected
# ---------------------------------------------------------------------------


def _live(**overrides) -> dict:
    live = {
        "target": "duckdb",
        "prose_revision": "abcdef0123456789",
        "spec_version": "2026-08-05",
        "models": {"events": "1111222233334444"},
    }
    live.update(overrides)
    return live


def test_a_current_pack_has_no_disagreements():
    assert disagreements(a_pack(), _live()) == []


def test_a_pack_whose_schema_hash_moved_is_rejected():
    problems = disagreements(a_pack(), _live(models={"events": "9999888877776666"}))
    assert len(problems) == 1
    assert "schema hash" in problems[0]


def test_a_pack_built_from_older_prose_is_rejected():
    problems = disagreements(a_pack(), _live(prose_revision="0000000000000000"))
    assert any("prose revision" in problem for problem in problems)


def test_a_pack_missing_a_model_the_target_holds_is_rejected():
    live = _live(models={"events": "1111222233334444", "arrived_later": "aaaa"})
    problems = disagreements(a_pack(), live)
    assert any("does not describe" in problem for problem in problems)


def test_a_pack_for_another_target_is_rejected_outright():
    problems = disagreements(a_pack(target="published"), _live())
    assert problems == ["this is a published pack, not a duckdb one"]


# ---------------------------------------------------------------------------
# Citations, which fail the build rather than warning
# ---------------------------------------------------------------------------


def _facts(target_name="duckdb") -> TargetFacts:
    return TargetFacts(
        target_name=target_name,
        columns={"events": ["dataset", "event_count"], "dim_thing": ["name"]},
        adrs={"adr-1", "adr-6"},
        sources={"311_cases": {"start_date": "2024-01-01T00:00:00.000Z", "tier": "core"}},
    )


def test_a_citation_of_a_model_the_target_lacks_does_not_resolve():
    assert resolve_citation({"kind": "model", "ref": "events"}, _facts()) is None
    problem = resolve_citation({"kind": "model", "ref": "street_trees"}, _facts())
    assert problem and "not in the duckdb target" in problem


def test_a_citation_of_a_column_the_target_lacks_does_not_resolve():
    assert resolve_citation({"kind": "column", "ref": "events.dataset"}, _facts()) is None
    problem = resolve_citation({"kind": "column", "ref": "events.h3_r9"}, _facts())
    assert problem and "no column 'h3_r9'" in problem


def test_a_registry_citation_fails_when_the_value_it_asserts_has_moved():
    citation = {
        "kind": "registry",
        "ref": "311_cases.start_date",
        "equals": "2024-01-01T00:00:00.000Z",
    }
    assert resolve_citation(citation, _facts()) is None
    moved = dict(citation, equals="2020-01-01T00:00:00.000Z")
    problem = resolve_citation(moved, _facts())
    assert problem and "boundary that moved" in problem


def test_an_entry_claiming_a_target_it_cannot_resolve_against_fails_generation():
    """Not a warning. An entry about a model that was cut has to fail the build.

    The failure mode of a refusal list is not being obviously wrong; it is going
    on looking complete for a year after the warehouse moved.
    """
    prose = {
        "refusals": [
            {
                "id": "refuse.about-a-cut-model",
                "applies_to": ["duckdb"],
                "evidence": [{"kind": "model", "ref": "street_trees"}],
            }
        ]
    }
    with pytest.raises(ProseError) as error:
        select_for_target(prose, _facts())
    assert "refuse.about-a-cut-model" in str(error.value)


def test_an_entry_that_does_not_claim_the_target_is_simply_not_rendered():
    prose = {
        "refusals": [
            {
                "id": "refuse.published-only",
                "applies_to": ["published"],
                "evidence": [{"kind": "model", "ref": "street_trees"}],
            }
        ]
    }
    assert select_for_target(prose, _facts())["refusals"] == []


def test_a_prose_join_that_restates_a_declared_one_fails():
    derived = [{"from": "events.dataset", "to": "dim_thing.name"}]
    prose_joins = [{"id": "join.restated", "from": "events.dataset", "to": "dim_thing.name"}]
    with pytest.raises(ProseError) as error:
        check_join_duplication(prose_joins, derived)
    assert "join.restated" in str(error.value)
    check_join_duplication([{"id": "join.new", "from": "a.b", "to": "c.d"}], derived)


def test_a_class_three_refusal_with_no_example_fails():
    refusals = [{"id": "refuse.rank-by-raw-count", "class": "misnormalised"}]
    with pytest.raises(ProseError):
        check_class_three_examples(refusals, [])
    check_class_three_examples(
        refusals, [{"id": "ex.rate", "demonstrates": ["refuse.rank-by-raw-count"]}]
    )


# ---------------------------------------------------------------------------
# Examples, which are verified or they are not in the pack
# ---------------------------------------------------------------------------


def test_an_example_edited_without_reverification_fails(target):
    sql = "select 1 as answer"
    candidate = {
        "id": "ex.edited",
        "question": "What?",
        "sql": sql,
        "verified": {"sql_sha256": hashlib.sha256(b"select 2 as answer").hexdigest()},
    }
    with pytest.raises(Exception) as error:
        build_examples_block(target, [candidate], set())
    assert "does not match the hash" in str(error.value)


def test_an_example_that_errors_fails_the_build(target):
    sql = "select * from no_such_model"
    candidate = {
        "id": "ex.broken",
        "question": "What?",
        "sql": sql,
        "verified": {"sql_sha256": hashlib.sha256(sql.encode()).hexdigest()},
    }
    with pytest.raises(Exception) as error:
        build_examples_block(target, [candidate], set())
    assert "ex.broken" in str(error.value)


def test_a_verified_example_records_what_it_returned(target):
    sql = "select dataset from main.events where dataset = '311_cases'"
    candidate = {
        "id": "ex.works",
        "question": "Which?",
        "sql": sql,
        "demonstrates": ["refuse.rank-by-raw-count"],
        "verified": {"sql_sha256": hashlib.sha256(sql.encode()).hexdigest()},
    }
    entry = build_examples_block(target, [candidate], {"refuse.rank-by-raw-count"})[0]
    assert entry["verified"]["rows"] == 3
    assert entry["verified"]["target"] == "duckdb"
    assert entry["required"] is True


# ---------------------------------------------------------------------------
# The real prose file, which is the thing a maintainer edits
# ---------------------------------------------------------------------------


def test_the_committed_prose_is_well_formed():
    prose, revision = load_prose()
    validate_shape(prose, TARGET_NAMES)
    assert len(revision) == 16


def test_every_class_three_refusal_in_the_committed_prose_has_an_example():
    prose, _ = load_prose()
    check_class_three_examples(prose["refusals"], prose["examples"])


def test_the_committed_prose_states_the_sentences_the_pack_needs():
    prose, _ = load_prose()
    assert "NO GROUND-TRUTH MEASURE" in prose["preamble"]["no_ground_truth"]
    assert "refuse and name what is missing" in prose["preamble"]["closed_world_rule"]
