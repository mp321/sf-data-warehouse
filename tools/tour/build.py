"""Build docs/tour/index.html from the dbt manifest and the local warehouse (read-only).

Run from the repo root after a dbt build: .venv/bin/python -I tools/tour/build.py
"""

import argparse
import collections
import json
import math
import pathlib
import re
import subprocess
import sys

import duckdb

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
CASE = "101004387939"  # the 311 case traced on the page; override with --case


def simplify(pts, tol):
    """Douglas-Peucker on an open polyline."""
    if len(pts) < 3:
        return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = math.hypot(dx, dy) or 1e-9
    best, dmax = 0, -1.0
    for k in range(1, len(pts) - 1):
        d = abs(dy * pts[k][0] - dx * pts[k][1] + x2 * y1 - y2 * x1) / norm
        if d > dmax:
            best, dmax = k, d
    if dmax <= tol:
        return [pts[0], pts[-1]]
    return simplify(pts[: best + 1], tol)[:-1] + simplify(pts[best:], tol)


def map_paths(con):
    """Neighborhood outlines as SVG path strings in a 1000-wide viewBox, plus its height."""
    rings = {}
    rows = con.execute("select analysis_neighborhood, geojson from dim_neighborhood").fetchall()
    for name, gj in rows:
        g = json.loads(gj)
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        # drop the Farallon Islands, 40 km offshore, so the city fills the frame
        rings[name] = [p[0] for p in polys if max(x for x, _ in p[0]) > -122.56]
    pts = [p for rs in rings.values() for r in rs for p in r]
    lon0, lat0 = min(x for x, _ in pts), max(y for _, y in pts)
    kx = math.cos(math.radians(37.76))
    scale = 1000 / ((max(x for x, _ in pts) - lon0) * kx)

    def proj(p):
        return ((p[0] - lon0) * kx * scale, (lat0 - p[1]) * scale)

    paths = {}
    for name, rs in rings.items():
        d = []
        for r in rs:
            xy = [proj(p) for p in r]
            mid = len(xy) // 2  # split the closed ring so both halves have distinct ends
            xy = simplify(xy[: mid + 1], 0.7)[:-1] + simplify(xy[mid:], 0.7)
            if len(xy) >= 4:
                d.append("M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in xy) + "Z")
        paths[name] = "".join(d)
    return paths, round((lat0 - min(y for _, y in pts)) * scale)


def month_window(con):
    """Complete months with full 311 coverage: newest month dropped, start at half the peak."""
    months = con.execute(
        "select event_month, sum(event_count) from mart_activity_by_neighborhood "
        "where dataset = '311_cases' group by 1 order by 1"
    ).fetchall()[:-1]
    peak = max(n for _, n in months)
    first = len(months) - 1
    while first > 0 and months[first - 1][1] >= peak / 2:
        first -= 1
    return months[first][0], months[-1][0]


def neighborhoods(con, lo, hi):
    paths, height = map_paths(con)
    cats = [
        r[0]
        for r in con.execute(
            "select category from mart_activity_by_neighborhood where dataset = '311_cases' "
            "and event_month between ? and ? group by 1 order by sum(event_count) desc limit 8",
            [lo, hi],
        ).fetchall()
    ]
    counts = collections.defaultdict(dict)
    for name, cat, n in con.execute(
        "select analysis_neighborhood, category, sum(event_count) "
        "from mart_activity_by_neighborhood "
        "where dataset = '311_cases' and event_month between ? and ? group by all",
        [lo, hi],
    ).fetchall():
        counts[name][cat] = n
    hoods = [
        {
            "name": name,
            "pop": pop,
            "housing": hu,
            "biz": biz,
            "km2": round(km2, 3),
            "d": paths[name],
            "all": sum(counts[name].values()),
            "c": [counts[name].get(c, 0) for c in cats],
        }
        for name, pop, hu, biz, km2 in con.execute(
            "select analysis_neighborhood, population, housing_units, business_count, area_sq_km "
            "from dim_neighborhood order by 1"
        ).fetchall()
    ]
    return hoods, cats, height


def first_sentence(text):
    return re.split(r"(?<=\.)\s", (text or "").strip().replace("\n", " "), maxsplit=1)[0]


def lineage(manifest):
    """Sources, models and exposures with their parents, plus test counts by node and type."""
    tests_on, test_types = collections.Counter(), collections.Counter()
    for n in manifest["nodes"].values():
        if n["resource_type"] == "test":
            meta = n.get("test_metadata") or {}
            test_types[meta.get("name") or "singular (custom SQL)"] += 1
            for dep in n["depends_on"]["nodes"]:
                tests_on[dep] += 1
    nodes = []
    for uid, s in manifest["sources"].items():
        layer = "derived" if s["source_name"] == "derived_spatial" else "raw"
        nodes.append(
            {
                "id": s["name"],
                "layer": layer,
                "deps": [],
                "kind": "Parquet zone",
                "desc": first_sentence(s.get("description")),
                "tests": tests_on[uid],
            }
        )
    for uid, n in manifest["nodes"].items():
        if n["resource_type"] == "model":
            nodes.append(
                {
                    "id": n["name"],
                    "layer": n["fqn"][1] if len(n["fqn"]) > 2 else "marts",
                    "deps": [d.split(".")[-1] for d in n["depends_on"]["nodes"]],
                    "kind": n["config"]["materialized"],
                    "desc": first_sentence(n["description"]),
                    "tests": tests_on[uid],
                    "cols": len(n["columns"]),
                }
            )
    for e in manifest["exposures"].values():
        nodes.append(
            {
                "id": e["name"],
                "layer": "outputs",
                "deps": [d.split(".")[-1] for d in e["depends_on"]["nodes"]],
                "kind": "exposure",
                "desc": first_sentence(e.get("description")),
                "tests": 0,
            }
        )
    return nodes, test_types.most_common()


def trace(con, case):
    """One 311 case at every layer, from raw strings to its mart row."""
    raw = con.execute(
        "select requested_datetime, updated_datetime, status_description, service_name, "
        "service_subtype, street, lat, long, analysis_neighborhood, _socrata_updated_at "
        "from raw_datasf.raw_311_cases where service_request_id = ? order by _socrata_updated_at",
        [case],
    ).fetchall()
    if not raw:
        raise SystemExit(f"case {case} is not in the warehouse; pass --case with one that is")
    stg = con.execute(
        "select opened_at, closed_at, status, agency, service_category, service_subcategory, "
        "latitude, longitude from stg_datasf__311_cases where case_id = ?",
        [case],
    ).fetchone()
    geo = con.execute(
        "select h3_r8, h3_r10, analysis_neighborhood, supervisor_district_id, "
        "census_block_group_geoid, neighborhood_assignment_method "
        "from stg_spatial__point_geography where row_key = ? and source_table = 'raw_311_cases'",
        [case],
    ).fetchone()
    methods = con.execute(
        "select neighborhood_assignment_method, count(*) from stg_spatial__point_geography "
        "where source_table = 'raw_311_cases' group by 1 order by 2 desc"
    ).fetchall()
    month = stg[0].replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    mart = con.execute(
        "select event_count, events_per_1000_residents, events_per_1000_housing_units, "
        "events_per_1000_businesses, events_per_sq_km from mart_activity_by_neighborhood "
        "where analysis_neighborhood = ? and dataset = '311_cases' and category = ? "
        "and event_month = ?",
        [geo[2], stg[4], month],
    ).fetchone()
    funnel = con.execute(
        "select (select count(*) from raw_datasf.raw_311_cases), "
        "(select count(*) from stg_datasf__311_cases), "
        "(select sum(event_count) from mart_activity_by_neighborhood "
        "where dataset = '311_cases')"
    ).fetchone()
    raw_keys = [
        "requested_datetime",
        "updated_datetime",
        "status_description",
        "service_name",
        "service_subtype",
        "street",
        "lat",
        "long",
        "analysis_neighborhood",
    ]
    return {
        "id": case,
        "month": month.strftime("%Y-%m-%d"),
        "raw_copies": [r[9] for r in raw],
        "raw": dict(zip(raw_keys, raw[-1][:9], strict=True)),
        "stg": [
            stg[0].isoformat(" ", "minutes"),
            stg[1].isoformat(" ", "minutes"),
            *stg[2:6],
            round(stg[6], 6),
            round(stg[7], 6),
        ],
        "geo": [hex(geo[0])[2:], hex(geo[1])[2:], *geo[2:]],
        "methods": methods,
        "mart": [mart[0], *(round(x, 1) for x in mart[1:])],
        "funnel": list(funnel),
    }


def python_test_count():
    """How many tests pytest collects, so the page does not carry a typed number."""
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return int(re.search(r"(\d+) tests? collected", out).group(1))


def pack_counts():
    """Refusal, disclosure and example counts from the committed DuckDB context pack."""
    pack = json.loads((REPO / "context-pack/context_pack.duckdb.json").read_text())
    return {
        "refusals": len(pack["refusals"]["entries"]),
        "disclosures": len(pack["disclosures"]),
        "examples": len(pack["examples"]),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=REPO / "data/sf.duckdb", type=pathlib.Path)
    ap.add_argument("--manifest", default=REPO / "dbt/target/manifest.json", type=pathlib.Path)
    ap.add_argument("--out", default=REPO / "docs/tour/index.html", type=pathlib.Path)
    ap.add_argument("--case", default=CASE)
    ap.add_argument("--artifact", type=pathlib.Path, help="also write a body-only copy here")
    args = ap.parse_args()

    con = duckdb.connect(str(args.db), read_only=True)
    manifest = json.loads(args.manifest.read_text())
    lo, hi = month_window(con)
    hoods, cats, height = neighborhoods(con, lo, hi)
    nodes, test_types = lineage(manifest)
    passed, total, at, secs = con.execute(
        "select count(*) filter (where status = 'pass'), count(*), min(run_started_at), "
        "round(sum(execution_time_seconds), 1) from meta_dbt_run_results "
        "where resource_type = 'test'"
    ).fetchone()
    adrs = len(list((REPO / "docs").glob("*/adr-*.md")))
    data = {
        "built": manifest["metadata"]["generated_at"][:10],
        "window": [lo.strftime("%Y-%m"), hi.strftime("%Y-%m")],
        "cats": cats,
        "viewH": height,
        "hoods": hoods,
        "nodes": nodes,
        "testTypes": test_types,
        "testRun": {"passed": passed, "total": total, "at": str(at)[:10], "secs": secs},
        "adrs": adrs,
        "pyTests": python_test_count(),
        "pack": pack_counts(),
        "trace": trace(con, args.case),
    }

    page = (HERE / "page.html").read_text()
    page = page.replace("/*DATA*/null", json.dumps(data, separators=(",", ":"), default=str))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page)
    if args.artifact:
        head = r"^.*?<head>\s*<meta charset=\"utf-8\">\s*<meta name=\"viewport\"[^>]*>\s*"
        body = re.sub(head, "", page, count=1, flags=re.S).replace("</head>\n<body>\n", "", 1)
        args.artifact.write_text(body.rsplit("</body>", 1)[0])
    print(f"{args.out} {len(page) // 1024} KB, map {lo:%Y-%m} to {hi:%Y-%m}, {adrs} ADRs")


if __name__ == "__main__":
    main()
