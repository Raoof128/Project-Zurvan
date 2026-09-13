import os
from pathlib import Path
from scripts.config import get_config_dir, get_registry_path

def test_get_config_dir_default(monkeypatch):
    monkeypatch.delenv("ZURVAN_CONFIG_DIR", raising=False)
    # mock home
    monkeypatch.setattr(Path, "home", lambda: Path("/fake/home"))
    
    # We can't actually assert without hitting permissions if it tries to mkdir /fake/home
    # So we'll patch mkdir
    def fake_mkdir(self, *args, **kwargs):
        pass
    monkeypatch.setattr(Path, "mkdir", fake_mkdir)
    
    d = get_config_dir()
    assert str(d) == "/fake/home/.zurvan"

def test_get_config_dir_override(monkeypatch):
    monkeypatch.setenv("ZURVAN_CONFIG_DIR", "/fake/override")
    
    def fake_mkdir(self, *args, **kwargs):
        pass
    monkeypatch.setattr(Path, "mkdir", fake_mkdir)
    
    d = get_config_dir()
    assert str(d) == "/fake/override"
    
def test_get_registry_path(monkeypatch):
    monkeypatch.setenv("ZURVAN_CONFIG_DIR", "/fake/override")
    
    def fake_mkdir(self, *args, **kwargs):
        pass
    monkeypatch.setattr(Path, "mkdir", fake_mkdir)
    
    p = get_registry_path()
    assert str(p) == "/fake/override/projects.json"


# ── search index path resolution ──────────────────────────────────────────────

def test_search_db_defaults_to_the_repo_index(monkeypatch):
    from scripts.config import PROJECT_ROOT, get_search_db_path

    monkeypatch.delenv("ZURVAN_SEARCH_DB", raising=False)

    assert get_search_db_path() == PROJECT_ROOT / "data" / "search.sqlite"


def test_search_db_honours_the_override(monkeypatch, tmp_path):
    """The smoke test runs under the mock embedding provider. Without this
    redirect it rebuilt the REAL index with deterministic-hash vectors and
    silently destroyed semantic search — it happened twice."""
    from pathlib import Path
    from scripts.config import get_search_db_path

    target = tmp_path / "smoke" / "search.sqlite"
    monkeypatch.setenv("ZURVAN_SEARCH_DB", str(target))

    assert get_search_db_path() == Path(str(target))


def test_an_empty_override_falls_back_to_the_real_index(monkeypatch):
    """An unset-but-exported variable must not send the index to ''."""
    from scripts.config import PROJECT_ROOT, get_search_db_path

    monkeypatch.setenv("ZURVAN_SEARCH_DB", "")

    assert get_search_db_path() == PROJECT_ROOT / "data" / "search.sqlite"


def test_missing_index_diagnostic_goes_to_stderr(monkeypatch, capsys, tmp_path):
    """Callers parse stdout as JSON; a diagnostic there corrupts the payload."""
    from scripts.hybrid_search import search_hybrid

    monkeypatch.setenv("ZURVAN_SEARCH_DB", str(tmp_path / "absent.sqlite"))

    assert search_hybrid("anything") == []

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Search index not found" in captured.err
