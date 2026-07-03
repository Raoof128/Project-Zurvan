import os
import shutil

import pytest

from scripts.config import PROJECT_ROOT


@pytest.fixture
def extraction_workspace(tmp_path, monkeypatch):
    """Minimal CWD-relative workspace for extract_source (mock LLM provider)."""
    prompts_dir = tmp_path / "scripts" / "prompts"
    prompts_dir.mkdir(parents=True)
    shutil.copy(
        PROJECT_ROOT / "scripts" / "prompts" / "extract_source.md",
        prompts_dir / "extract_source.md",
    )
    # Source must contain the mock provider's evidence quote verbatim.
    source = tmp_path / "source.md"
    source.write_text(
        "Zurvan turns raw sources into a persistent Markdown wiki...\nMore text.\n"
    )
    monkeypatch.delenv("ZURVAN_LLM_PROVIDER", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_extract_writes_real_newlines_in_claim_tags(extraction_workspace):
    # Regression: tags were joined with a literal backslash-n ("\\n  - "),
    # producing malformed one-line YAML frontmatter in claim pages.
    from scripts.extract import extract_source

    extract_source("source.md")

    claim_file = extraction_workspace / "wiki" / "claims" / "claim-dummy-001.md"
    assert claim_file.exists()
    content = claim_file.read_text()
    assert "\\n" not in content
    assert "tags:\n  - ai\n  - retrieval" in content


def test_extract_creates_output_dirs_on_fresh_tree(extraction_workspace):
    # Regression: data/extractions/ and wiki/summaries/ were opened for write
    # without makedirs, crashing on a fresh checkout.
    from scripts.extract import extract_source

    extract_source("source.md")

    assert (extraction_workspace / "data" / "extractions" / "source.json").exists()
    assert (
        extraction_workspace / "wiki" / "summaries" / "source_summary.md"
    ).exists()
