import pytest

from scripts import prune_stub_pages as psp


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_classifies_a_claim_stub(tmp_path):
    p = _write(tmp_path / "Claim-foo.md",
               "---\ntitle: Claim-foo\n---\n# Claim-foo\n\n"
               "Evidence missing? No, cited from [foo.pdf](../sources/foo.md)")
    assert psp.classify_page(p) == psp.CLAIM_STUB


def test_classifies_a_concept_stub(tmp_path):
    p = _write(tmp_path / "AutoConcept-foo.md",
               "---\ntitle: AutoConcept-foo\n---\n# AutoConcept-foo\n\n"
               "Generated from [foo.pdf](../sources/foo.md)")
    assert psp.classify_page(p) == psp.CONCEPT_STUB


def test_a_hand_authored_claim_is_never_a_stub(tmp_path):
    # Real claims carry type/confidence/source and an Evidence section.
    p = _write(tmp_path / "claim-real.md",
               '---\ntype: claim\nconfidence: "high"\nsource: "wiki/sources/x.md"\n---\n'
               "# Claim\nBroadly safe means oversight.\n\n## Evidence\n> oversight\n")
    assert psp.classify_page(p) == psp.AUTHORED


def test_a_hand_authored_concept_is_never_a_stub(tmp_path):
    # wiki/concepts/corrigibility.md has this shape and MUST survive.
    p = _write(tmp_path / "corrigibility.md",
               "---\ntitle: Corrigibility\ntype: concept\ntags: safety\n---\n"
               "# Corrigibility\n\nFrom [[../digests/x]]. Real prose here.\n")
    assert psp.classify_page(p) == psp.AUTHORED


def test_filename_alone_never_decides(tmp_path):
    # A page NAMED like a stub but carrying real content is authored. Selecting
    # by filename would have deleted it.
    p = _write(tmp_path / "Claim-important.md",
               "---\ntitle: Claim-important\ntype: claim\n---\n# Claim\n"
               "A real fact.\n\n## Evidence\n> a real quote\n")
    assert psp.classify_page(p) == psp.AUTHORED


def test_a_stub_with_extra_prose_is_treated_as_authored(tmp_path):
    # Fail CLOSED: anything a human may have edited is out of scope.
    p = _write(tmp_path / "Claim-edited.md",
               "---\ntitle: Claim-edited\n---\n# Claim-edited\n\n"
               "Evidence missing? No, cited from [f.pdf](../sources/f.md)\n\n"
               "NOTE: I checked this by hand and it holds.\n")
    assert psp.classify_page(p) == psp.AUTHORED


def test_manifest_reports_kind_and_source_linkage(tmp_path):
    _write(tmp_path / "claims" / "Claim-a.md",
           "---\ntitle: Claim-a\n---\n# Claim-a\n\nEvidence missing? No, cited from [a.pdf](../sources/a.md)")
    _write(tmp_path / "concepts" / "AutoConcept-a.md",
           "---\ntitle: AutoConcept-a\n---\n# AutoConcept-a\n\nGenerated from [a.pdf](../sources/a.md)")
    _write(tmp_path / "claims" / "claim-real.md",
           '---\ntype: claim\n---\n# Claim\nx\n\n## Evidence\n> x\n')

    m = psp.build_manifest(claims_dir=tmp_path / "claims", concepts_dir=tmp_path / "concepts")

    kinds = sorted(e["kind"] for e in m)
    assert kinds == [psp.CLAIM_STUB, psp.CONCEPT_STUB]
    assert all(e["source_link"] == "../sources/a.md" for e in m)
    assert all(len(e["content_hash"]) == 64 for e in m)


def test_dry_run_deletes_nothing(tmp_path):
    p = _write(tmp_path / "claims" / "Claim-a.md",
               "---\ntitle: Claim-a\n---\n# Claim-a\n\nEvidence missing? No, cited from [a.pdf](../sources/a.md)")
    m = psp.build_manifest(claims_dir=tmp_path / "claims", concepts_dir=tmp_path / "concepts")

    assert psp.apply(m, snapshot_dir=tmp_path / "snap", dry_run=True) == 0
    assert p.exists(), "a dry run must never delete"


def test_apply_snapshots_before_deleting(tmp_path):
    # These pages are gitignored, so git is not the parachute.
    p = _write(tmp_path / "claims" / "Claim-a.md",
               "---\ntitle: Claim-a\n---\n# Claim-a\n\nEvidence missing? No, cited from [a.pdf](../sources/a.md)")
    m = psp.build_manifest(claims_dir=tmp_path / "claims", concepts_dir=tmp_path / "concepts")

    assert psp.apply(m, snapshot_dir=tmp_path / "snap", dry_run=False) == 1
    assert not p.exists()
    assert (tmp_path / "snap" / "Claim-a.md").exists(), "must snapshot before deleting"
