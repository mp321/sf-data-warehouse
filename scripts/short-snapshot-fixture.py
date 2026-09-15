"""Write a raw zone whose newest snapshot partition is a run that died mid-fetch.

`make ci-build` builds this zone and then asks two questions of it, which
together are ADR-19 step 3's argument rather than a restatement of it:

  - `check_runs.py` passes on it. The zone is internally consistent, because
    `_flush` counts as it writes, so the manifest claims exactly the rows that
    durably landed. That is the check being right, not the check being fooled.
  - `make build` refuses it. The only thing that gives the partition away is
    the key count against the partition before it, which is the question
    `check_snapshots.py` asks and nothing else in the project does.

Written here rather than committed as Parquet under tests/fixtures/ because
the fixtures there are JSON API responses, and this is a zone: a directory
layout, two partitions and their manifests. Generating it through
`raw_zone.write_batch` also means it is the layout the pipeline writes rather
than a copy of it that can drift, which is test_prune_raw.py's argument for
building zones in `tmp_path` instead of mocking them.

The numbers are small and the ratio is not. 100 keys against 500 is an 80%
drop against a 2% tolerance, which is the shape of a run killed after its
first flush and is nowhere near the 0.006% a day of withdrawn registrations
the guard has to stay quiet through.

Usage:
    python scripts/short-snapshot-fixture.py data/ci/short
"""

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingestion"))

import raw_zone

TABLE = "raw_business_locations"
DATASET = "business_locations"
GRAIN = "uniqueid"

COMPLETE = ("2026-09-05", 500)
SHORT = ("2026-09-06", 100)


def write_partition(root: Path, partition: str, keys: int) -> None:
    """One run, one flush, `keys` records of the same registry."""
    run_id = f"{partition.replace('-', '')}T091700Z"
    watermark = f"{partition}T09:17:00.000Z"
    rows = [
        {
            GRAIN: f"key-{n:06d}",
            raw_zone.WATERMARK_COLUMN: watermark,
            raw_zone.RUN_ID_COLUMN: run_id,
            "dba_name": f"business {n:06d}",
        }
        for n in range(keys)
    ]
    raw_zone.write_batch(TABLE, rows, run_id, 0, ingest_date=partition, root=root)
    raw_zone.write_run_manifest(
        TABLE,
        {
            "run_id": run_id,
            "dataset": DATASET,
            "table_name": TABLE,
            "ingest_date": partition,
            "started_at": f"{partition}T09:17:00",
            "finished_at": f"{partition}T09:18:00",
            "watermark_in": None,
            "watermark_out": watermark,
            # What durably landed, which is what `_finish` records on the
            # failure path too. The short run claims 100 rows and holds 100.
            "rows_written": keys,
            "files_written": 1,
            "mode": "snapshot",
            "status": "success",
            "error": None,
        },
        root=root,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path, help="directory to write the zone into")
    args = parser.parse_args()

    shutil.rmtree(args.root, ignore_errors=True)
    args.root.mkdir(parents=True)
    for partition, keys in (COMPLETE, SHORT):
        write_partition(args.root, partition, keys)

    print(
        f"short-snapshot fixture zone at {args.root}: {TABLE} "
        f"ingest_date={COMPLETE[0]} with {COMPLETE[1]} key(s), then "
        f"ingest_date={SHORT[0]} with {SHORT[1]}"
    )


if __name__ == "__main__":
    main()
