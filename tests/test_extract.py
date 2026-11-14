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
    # Z2: claims go through the ledger, which lives under ZURVAN_ROOT; without it
    # the ledger would resolve to the REAL repository.
    monkeypatch.setenv("ZURVAN_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def extract_source(rel):
    """One extraction is one operation (as `scripts/extract.py __main__` opens it)."""
    from scripts import knowledge_revision as kr
    from scripts.extract import extract_source as _extract
    with kr.operation(writer="extract", eager=True):
        _extract(rel)


def test_extract_writes_real_newlines_in_claim_tags(extraction_workspace):
    # Regression: tags were joined with a literal backslash-n ("\\n  - "),
    # producing malformed one-line YAML frontmatter in claim pages.
    extract_source("source.md")

    # Z2: pages are generated from the ledger, named by ledger id (clm_...).
    claim_files = list((extraction_workspace / "wiki" / "claims").glob("clm_*.md"))
    assert len(claim_files) == 1, claim_files
    content = claim_files[0].read_text()
    assert "\\n" not in content
    assert 'tags:\n  - "ai"\n  - "retrieval"' in content


def test_extract_creates_output_dirs_on_fresh_tree(extraction_workspace):
    # Regression: data/extractions/ and wiki/summaries/ were opened for write
    # without makedirs, crashing on a fresh checkout.
    extract_source("source.md")

    assert list((extraction_workspace / "data" / "extractions").glob("source-*.json"))
    assert list((extraction_workspace / "wiki" / "summaries").glob("source-*_summary.md"))
