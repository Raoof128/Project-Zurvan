import os
from pathlib import Path

# Absolute path to the Zurvan repo root (parent of scripts/).
# Using __file__ means this is CWD-independent — safe when the MCP
# server is launched from any working directory.
#
# `ZURVAN_ROOT` overrides it, and exists for ONE reason: a test or smoke must be
# able to run against a throwaway store. `tests/test_cli.py::test_cli_remember`
# spawned the real CLI and wrote a note into the live `wiki/` on every run — 48
# of them had accumulated by 2026-09-15, along with 28 notes and 4 decisions from
# the MCP smoke. Those pages are indexed, so test execution was changing the
# chunk count, the retrieval candidates, the graph and therefore every retrieval
# baseline. Cleaning up afterwards is not a fix: a crash between write and
# cleanup still contaminates the corpus. The override is read at import, so a
# subprocess started with it set never touches the real store at all.
PROJECT_ROOT: Path = Path(
    os.environ.get("ZURVAN_ROOT") or Path(__file__).parent.parent
).resolve()

def get_config_dir() -> Path:
    env_dir = os.environ.get("ZURVAN_CONFIG_DIR")
    if env_dir:
        config_dir = Path(env_dir).resolve()
    else:
        config_dir = Path.home() / ".zurvan"
    
    # Safely create it if it doesn't exist
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return config_dir

def get_registry_path() -> Path:
    return get_config_dir() / "projects.json"


def get_search_db_path() -> Path:
    """Path to the search index, honouring ZURVAN_SEARCH_DB.

    The smoke test runs under ZURVAN_EMBED_PROVIDER=mock so it needs no model
    download. Without a redirect it rebuilt the REAL index with
    deterministic-hash vectors, silently destroying semantic search — measured
    at 0% semantic recall afterwards, twice. Every component that opens the
    index resolves it here so one environment variable moves all of them.
    """
    override = os.environ.get("ZURVAN_SEARCH_DB")
    if override:
        return Path(override)
    return PROJECT_ROOT / "data" / "search.sqlite"
