"""Adversarial tests against the cross-project publication guard.

The guard decides whether a tracked wiki page may reach a public remote. A
privacy check is only worth what it withstands, so these tests try to defeat
it rather than confirm it: evasion via where tags are written, how they are
spelled, how the path is spelled, and content that a tag check cannot see.

Every fixture is synthetic. The private-looking values here are invented.
"""

import pytest

from scripts.public_repo_guard import (
    PRIVATE_PROJECT_TAGS,
    PUBLIC_PROJECTS,
    audit_cross_project_pages,
    is_publishable_page,
    page_project,
    page_tags,
    page_verdict,
)


def page(tags_block="", body="Some decision text.", frontmatter=True, project="zurvan"):
    """A page that is publishable unless a test makes it otherwise."""
    if not frontmatter:
        return body
    declared = f"project: {project}\n" if project is not None else ""
    return f"---\ntitle: \"A page\"\ntype: decision\n{declared}{tags_block}---\n\n{body}\n"


def block_tags(*tags):
    return "tags:\n" + "".join(f'  - "{t}"\n' for t in tags)


# ── baseline: the guard does its intended job ─────────────────────────────────

def test_a_declared_public_project_page_is_publishable():
    assert is_publishable_page(page(project="zurvan"))


def test_a_private_project_page_is_blocked():
    assert page_verdict(page(block_tags("private-project-a"), project="zurvan")) is not None


# ── evasion 1: hide the tags somewhere the parser might still find them ───────

def test_a_project_declared_in_the_body_does_not_count():
    """Only frontmatter is trusted; prose must not confer publishability."""
    sneaky = page(project=None, body="project: zurvan\n\nActually private content.")

    assert page_project(sneaky) is None
    assert page_verdict(sneaky) is not None


def test_a_second_frontmatter_block_in_the_body_is_ignored():
    sneaky = page(project=None, body="---\nproject: zurvan\n---\nprivate")

    assert page_verdict(sneaky) is not None


def test_a_page_with_no_frontmatter_is_blocked():
    assert page_verdict(page(frontmatter=False)) is not None


def test_an_empty_page_is_blocked():
    assert page_verdict("") is not None


def test_an_unterminated_frontmatter_block_is_blocked():
    """No closing delimiter means no trustworthy frontmatter."""
    assert page_verdict('---\nproject: zurvan\n\nstill in frontmatter?') is not None


# ── evasion 2: spell the tag so it looks public but isn't ─────────────────────

@pytest.mark.parametrize("spelling", ["zurvan", "Zurvan", "ZURVAN", "  zurvan  ",
                                     '"zurvan"', "'zurvan'"])
def test_a_declared_project_matches_regardless_of_case_or_quoting(spelling):
    """Legitimate variation must not be punished, or people route around it."""
    assert is_publishable_page(page(project=spelling))


@pytest.mark.parametrize("spelling", ["private-project-a", "private-project-a", "private-project-a", "  private-project-a  "])
def test_private_tags_are_vetoed_regardless_of_case_or_padding(spelling):
    """Asserted ALONGSIDE a public tag on purpose. Without the public tag the
    page is blocked anyway for having no public tag, so the test would pass
    without the veto ever firing — which is how a padded-tag bypass hid."""
    verdict = page_verdict(page(block_tags("zurvan", spelling)))

    assert verdict is not None
    assert "private project" in verdict


def test_a_cyrillic_homoglyph_project_is_not_treated_as_public():
    """A Cyrillic a makes a different string; it must not inherit public status."""
    homoglyph = "zurvаn"
    assert homoglyph != "zurvan"
    assert page_verdict(page(project=homoglyph)) is not None


@pytest.mark.parametrize("lookalike", ["zurvan-private", "notzurvan", "zurvanx"])
def test_a_project_name_that_merely_contains_a_public_one_does_not_match(lookalike):
    """Matching is exact: `zurvan-private` must not inherit `zurvan`'s status."""
    verdict = page_verdict(page(project=lookalike))
    assert verdict is not None and "not a public project" in verdict


# ── evasion 3: launder private content under a public tag ─────────────────────

def test_a_private_tag_vetoes_even_under_a_public_project():
    """The bypass found when red-teaming the allowlist: correctly declared for a
    public project, but carrying another project's content."""
    mixed = page(block_tags("zurvan", "simurgh", "private-project-c"), project="zurvan")

    verdict = page_verdict(mixed)
    assert verdict is not None
    assert "private project" in verdict


def test_an_account_uuid_blocks_an_otherwise_publishable_page():
    """The other bypass: a public-project page carrying an organisation ID."""
    leaky = page(project="anthropic",
                 body="Approved for Organization ID 9168437b-5ed5-4744-bdd3-c5220c80357e.")

    verdict = page_verdict(leaky)
    assert verdict is not None and "UUID" in verdict


def test_uuid_detection_is_case_insensitive():
    leaky = page(project="zurvan",
                 body="ref 9168437B-5ED5-4744-BDD3-C5220C80357E")

    assert page_verdict(leaky) is not None


def test_a_support_ticket_reference_blocks_the_page():
    leaky = page(project="anthropic", body="ref TS-019f4512-cc52-75dc-ab74-b1a8b1e97c4c")

    verdict = page_verdict(leaky)
    assert verdict is not None and "ticket" in verdict


def test_a_git_sha_is_not_mistaken_for_an_account_identifier():
    """40-hex commit SHAs and 64-hex content hashes are everywhere in this repo;
    flagging them would make the guard useless and get it switched off."""
    ordinary = page(project="zurvan",
                    body="commit 25fa050e1f2a3b4c5d6e7f8091a2b3c4d5e6f708, "
                         "hash 2da49ab849764ab29b037c08834266ca67151cf1e746999c332e6c3546779c39")

    assert page_verdict(ordinary) is None


# ── evasion 4: spell the PATH so the guard skips the file ─────────────────────

def _reader(mapping):
    def read(path):
        return mapping[path]
    return read


def test_a_nested_subdirectory_is_still_checked():
    files = {"wiki/decisions/2026/q3/private.md": page(block_tags("private-project-a"), project="zurvan")}

    assert audit_cross_project_pages(list(files), _reader(files))


def test_a_capitalised_path_is_still_checked():
    """macOS is case-insensitive, so wiki/Decisions/ addresses the same file."""
    files = {"wiki/Decisions/private.md": page(block_tags("private-project-a"), project="zurvan")}

    assert audit_cross_project_pages(list(files), _reader(files))


def test_a_non_markdown_file_under_decisions_is_still_checked():
    """The wiki/sources leak was exactly a non-.md sibling."""
    files = {"wiki/decisions/private.md.bak": page(block_tags("private-project-a"), project="zurvan")}

    assert audit_cross_project_pages(list(files), _reader(files))


def test_digests_are_checked_as_well_as_decisions():
    files = {"wiki/digests/private.md": page(block_tags("private-project-a"), project="zurvan")}

    assert audit_cross_project_pages(list(files), _reader(files))


def test_paths_outside_the_cross_project_dirs_are_not_checked():
    """Scope discipline: this check must not start policing scripts/ or tests/."""
    files = {"scripts/thing.py": "tags: private-project-a", "tests/test_x.py": "private-project-a"}

    assert audit_cross_project_pages(list(files), _reader(files)) == []


def test_a_gitkeep_is_exempt_but_a_file_merely_ending_in_gitkeep_is_not():
    exempt = {"wiki/decisions/.gitkeep": ""}
    assert audit_cross_project_pages(list(exempt), _reader(exempt)) == []


# ── evasion 5: break the parser ───────────────────────────────────────────────

def test_an_empty_project_value_is_blocked():
    assert page_verdict(page(project="")) is not None


def test_a_page_with_tags_but_no_project_is_blocked():
    """Tags no longer confer publishability — this was the fail-open hole."""
    assert page_verdict(page(block_tags("zurvan"), project=None)) is not None


@pytest.mark.parametrize("form,expected", [
    ('tags: [private-project-a, search]\n', {"private-project-a", "search"}),
    ('tags: private-project-a, search\n', {"private-project-a", "search"}),
    ('tags:\n  - "private-project-a"\n  - "search"\n', {"private-project-a", "search"}),
    ('tags:\n    - private-project-a\n    - search\n', {"private-project-a", "search"}),
    ('tags:\n\t- private-project-a\n', {"private-project-a"}),
])
def test_every_tag_spelling_used_in_the_wiki_parses(form, expected):
    """All three forms appear in the real store. A parser that misses one lets
    a private tag through the veto unnoticed — and reported a false positive on
    wiki/digests/claudes-constitution.md, which uses the bare comma form."""
    assert page_tags(page(form)) == expected
    assert "private project" in page_verdict(page(form, project="zurvan"))


def test_an_unreadable_page_is_reported_rather_than_skipped():
    """Fail closed: the guard cannot prove an unreadable page is safe."""
    def explode(_path):
        raise OSError("unreadable")

    offenders = audit_cross_project_pages(["wiki/decisions/x.md"], explode)

    assert offenders and offenders[0][1] == "unreadable"


def test_the_two_tag_sets_do_not_overlap():
    """An overlapping tag would make the veto and the allowlist contradict."""
    assert not (PUBLIC_PROJECTS & PRIVATE_PROJECT_TAGS)
