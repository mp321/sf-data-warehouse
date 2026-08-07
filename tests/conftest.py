"""Put `ingestion/` on `sys.path`, so the tests import what the pipeline imports.

`ingestion/` is a directory of scripts and not a package, on purpose: there is
no `pyproject.toml` in this repo, and ruff.toml says why. At runtime
`python ingestion/spatial.py` puts that directory on `sys.path` itself, which
is what makes `import geometry` resolve there. pytest is started from the repo
root and does not, so this does the same thing.

The alternative, adding `__init__.py` files and importing `ingestion.geometry`,
would mean every script in that directory changed how it imports its siblings
in order to be testable, which is a larger change than the tests are worth and
is the wrong way round: the tests should exercise the arrangement that runs.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO_ROOT / "ingestion"))

# tools/context_pack/ is the same arrangement for the same reason: a directory
# of scripts whose entry point puts itself on sys.path at runtime. Its modules
# are prefixed `pack_` so that adding this line cannot shadow anything, which
# `ingestion/datasets.py` is the cautionary tale for; see ruff.toml.
sys.path.insert(0, str(REPO_ROOT / "tools" / "context_pack"))
