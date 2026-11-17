"""A narrow read of a bucket zone must not download the whole zone.

gcsfs pads each read to 5 MiB, which made every daily run fetch about three
times the raw zone (dev note 2026-11-16). `remote.exact_range` turns that off.
"""

import hashlib
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fsspec.spec import AbstractBufferedFile, AbstractFileSystem

import raw_zone
import remote

BUCKET = "gs://example-bucket"
TABLE = "raw_example"
NEWEST = "2026-10-23T09:17:00.000Z"


class _CountingFile(AbstractBufferedFile):
    def _fetch_range(self, start: int, end: int) -> bytes:
        with self.fs.local(self.path).open("rb") as handle:
            handle.seek(start)
            data = handle.read(end - start)
        self.fs.fetched += len(data)
        return data


class FakeBucket(AbstractFileSystem):
    """A directory served as gs://, with gcsfs's read defaults and a byte count."""

    protocol = ("gs", "gcs")
    cachable = False

    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = directory
        self.fetched = 0

    def local(self, path: str) -> Path:
        return self.directory / self._strip_protocol(path).split("/", 1)[1]

    def info(self, path: str, **kwargs) -> dict:
        local = self.local(path)
        kind = "directory" if local.is_dir() else "file"
        return {"name": self._strip_protocol(path), "size": local.stat().st_size, "type": kind}

    def ls(self, path: str, detail: bool = True, **kwargs) -> list:
        base = self._strip_protocol(path)
        entries = [
            self.info(f"{base}/{child.name}") for child in sorted(self.local(path).iterdir())
        ]
        return entries if detail else [entry["name"] for entry in entries]

    def modified(self, path: str) -> datetime:
        return datetime.fromtimestamp(self.local(path).stat().st_mtime, tz=timezone.utc)

    def _open(self, path, mode="rb", block_size=None, cache_type="readahead", **kwargs):
        # 5 MiB readahead is what gcsfs.core.GCSFile defaults to.
        return _CountingFile(self, path, mode, block_size or 5 * 2**20, cache_type=cache_type)


def _filler(row: int, column: int) -> str:
    return hashlib.sha256(f"{row}-{column}".encode()).hexdigest()


@pytest.fixture
def zone(tmp_path: Path) -> Path:
    """One raw table whose watermark column is a small share of its bytes."""
    rows = [
        {
            "id": str(row),
            "_socrata_updated_at": NEWEST if row == 4321 else "2026-10-22T09:17:00.000Z",
            **{f"filler_{column}": _filler(row, column) for column in range(10)},
        }
        for row in range(5000)
    ]
    raw_zone.write_batch(
        TABLE, rows, "20261023T091700Z", 0, ingest_date="2026-10-23", root=tmp_path
    )
    return tmp_path


def _watermark_through(monkeypatch: pytest.MonkeyPatch, bucket: FakeBucket, zone: Path):
    """`read_watermark` on the fixture, addressed as a gs:// zone served by `bucket`."""
    monkeypatch.setattr(remote, "filesystem", lambda: bucket)
    monkeypatch.setattr(remote, "duckdb_filesystem", lambda: bucket)
    return raw_zone.read_watermark(TABLE, f"{BUCKET}/{zone.name}")


def _zone_bytes(zone: Path) -> int:
    return sum(path.stat().st_size for path in zone.rglob("*.parquet"))


def test_a_one_column_read_fetches_a_fraction_of_the_zone(
    monkeypatch: pytest.MonkeyPatch, zone: Path
) -> None:
    bucket = remote.exact_range(FakeBucket)(zone.parent)
    assert _watermark_through(monkeypatch, bucket, zone) == NEWEST
    assert bucket.fetched < 0.1 * _zone_bytes(zone), (
        f"reading one column fetched {bucket.fetched} of {_zone_bytes(zone)} bytes. "
        "remote.exact_range has stopped turning readahead off."
    )


def test_the_padding_this_guards_against_is_real(
    monkeypatch: pytest.MonkeyPatch, zone: Path
) -> None:
    """Without `exact_range` the same read fetches most of the file."""
    bucket = FakeBucket(zone.parent)
    assert _watermark_through(monkeypatch, bucket, zone) == NEWEST
    assert bucket.fetched > 0.8 * _zone_bytes(zone)


def test_an_explicit_cache_type_still_wins() -> None:
    opened = {}

    class Recorder:
        def _open(self, path, mode="rb", **kwargs):
            opened[mode] = kwargs

    fs = remote.exact_range(Recorder)()
    fs._open("x")
    fs._open("x", "rb", cache_type="readahead")
    assert opened["rb"] == {"cache_type": "readahead"}
    fs._open("x", "wb")
    assert opened["wb"] == {}, "writes keep the filesystem's own buffering"


def test_gcsfs_still_takes_the_cache_type() -> None:
    """`exact_range` relies on gcsfs passing `cache_type` through to its file."""
    gcsfs = pytest.importorskip("gcsfs")
    opener = inspect.signature(gcsfs.core.GCSFileSystem._open).parameters.values()
    assert any(parameter.kind is parameter.VAR_KEYWORD for parameter in opener)
    assert "cache_type" in inspect.signature(gcsfs.core.GCSFile.__init__).parameters
