import subprocess
import sys
from pathlib import Path

BLOCKED_PATTERNS = [
    "raw/",
    # Ingested source pages carry the full text of private documents. The
    # extension patterns below only catch .pdf/.docx, so a sibling artefact
    # (<page>.md.pre-repair.bak) slipped past both this guard and .gitignore.
    # Directory patterns match the whole subtree, which closes that.
    "wiki/sources/",
    "dist/snapshots/",
    "dist/backups/",
    "evidence-packs/",
    "reports/",
    "data/search.sqlite",
    "data/graph.sqlite",
    "data/graph_export.md",
    "data/graph_export.dot",
    "data/image_manifest.json",
    ".zurvan/",
    ".env",
    "snapshots/",
    "publications/",
    "dist/publications/",
    ".pdf",
    ".docx"
]

# Paths that are tracked BY DESIGN but can carry any project's content, because
# Zurvan writes every project's memory into one store. A decision about a
# tenancy, a clinical study or a student's coursework lands in the same
# wiki/decisions/ as a decision about this repo — and the remote is public.
CROSS_PROJECT_DIRS = ("wiki/decisions/", "wiki/digests/")

# Allowlist, not a denylist: a page may only be tracked if it is tagged for a
# project that is already public. A denylist would fail OPEN for every new
# private project, which is exactly the case that matters.
PUBLIC_PROJECT_TAGS = frozenset({
    "zurvan",
    "project-zurvan",
    "simurgh",
    "project-simurgh",
    "llm-shield",
    "gmail",
    "gmail-mcp",
    "anthropic",
    "raoof128",
})

# A VETO, checked before the allowlist. Red-teaming the allowlist showed it is
# not sufficient on its own: a page can be correctly tagged for a public
# project AND carry another project's content (e.g. a decision tagged
# "zurvan" whose subject is ingesting a private research corpus). Any of these
# tags blocks the page no matter what else it carries.
PRIVATE_PROJECT_TAGS = frozenset({
    "private-project-a", "private-project-b", "private-project-b-full", "private-project-c",
    "private-project-c", "private-project-d", "private-project-e", "private-project-f", "private-project-g", "private-project-h", "private-project-h",
    "private-project-i", "private-person", "private-project-k",
})

# Identifiers that must never reach a public remote even inside an otherwise
# publishable page: account/organisation IDs and support-ticket references.
# Red-teaming found the Anthropic CVP decision passed the tag checks while
# carrying an Organization ID and a ticket ref.
import re as _re

_IDENTIFIER_PATTERNS = (
    (_re.compile(r'\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b', _re.I),
     "UUID (account/organisation identifier)"),
    (_re.compile(r'\bTS-[0-9a-f]{8}-[0-9a-f-]{4,}\b', _re.I), "support-ticket reference"),
)

_EMAIL_PATTERN = _re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')


def page_identifiers(text):
    """Blocking identifiers found in a page, as (value, kind) pairs."""
    found = []
    for pattern, kind in _IDENTIFIER_PATTERNS:
        for match in pattern.findall(text):
            found.append((match, kind))
    return found


def _frontmatter(text):
    """Return the YAML frontmatter block only, or '' if there isn't one.

    Deliberately strict: tags written in the BODY must not make a page look
    publishable, so anything after the closing delimiter is discarded.
    """
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def page_tags(text):
    """Tags declared in frontmatter, lowercased. Supports block and inline form."""
    import re

    fm = _frontmatter(text)
    if not fm:
        return set()

    # Three spellings are in use across the wiki and all must parse, or the
    # guard reports a false positive on a correctly-tagged page:
    #   tags: [a, b]        tags: a, b, c        tags:\n  - a\n  - b
    bracketed = re.search(r'^tags:[ \t]*\[(.*?)\]', fm, re.M)
    if bracketed:
        raw = bracketed.group(1).split(",")
    else:
        same_line = re.search(r'^tags:[ \t]*(\S.*)$', fm, re.M)
        if same_line:
            raw = same_line.group(1).split(",")
        else:
            block = re.search(r'^tags:[ \t]*\n((?:[ \t]+-[ \t]*.*\n?)+)', fm, re.M)
            if not block:
                return set()
            raw = re.findall(r'^[ \t]+-[ \t]*(.*)$', block.group(1), re.M)

    # strip() again AFTER unquoting: '  private-project-a  ' would otherwise keep its inner
    # padding, match neither the veto nor the allowlist, and let a padded
    # private tag ride along beside a genuine public one.
    cleaned = (t.strip().strip('"').strip("'").strip().lower() for t in raw)
    return {t for t in cleaned if t}


def page_verdict(text):
    """Why a page may not be published, or None if it may.

    Three layers, cheapest and most decisive first. Each exists because the
    one before it was shown insufficient when red-teamed against the real
    store: a private-project veto, then a public-project allowlist, then a
    scan for identifiers that must not be published even on a public project's
    page.
    """
    tags = page_tags(text)

    private = sorted(tags & PRIVATE_PROJECT_TAGS)
    if private:
        return f"tagged for a private project: {', '.join(private)}"

    if not tags & PUBLIC_PROJECT_TAGS:
        return f"no public project tag (tags={sorted(tags) or 'none'})"

    identifiers = page_identifiers(text)
    if identifiers:
        kinds = sorted({kind for _, kind in identifiers})
        return f"contains {', '.join(kinds)}"

    return None


def is_publishable_page(text):
    """True only if the page survives every layer of page_verdict."""
    return page_verdict(text) is None


def audit_cross_project_pages(tracked, read_text=None):
    """Flag tracked cross-project pages that are not marked for a public project.

    Fails closed: an unreadable, empty, frontmatter-less or untagged page is
    reported, because the guard cannot show it is safe to publish.
    """
    if read_text is None:
        def read_text(path):
            return Path(path).read_text(encoding="utf-8", errors="replace")

    offenders = []
    for path in tracked:
        lowered = path.lower()
        if not any(lowered.startswith(d) for d in CROSS_PROJECT_DIRS):
            continue
        if lowered.endswith(".gitkeep"):
            continue
        try:
            text = read_text(path)
        except (OSError, UnicodeError):
            offenders.append((path, "unreadable"))
            continue
        verdict = page_verdict(text)
        if verdict:
            offenders.append((path, verdict))
    return offenders


def advisory_emails(tracked, read_text=None):
    """Non-fatal: email addresses on tracked cross-project pages.

    Not blocking, because a digest of a public paper legitimately quotes its
    authors' addresses. Reported so a human can judge, since the guard cannot
    tell a quoted public address from a private correspondent's.
    """
    if read_text is None:
        def read_text(path):
            return Path(path).read_text(encoding="utf-8", errors="replace")

    found = []
    for path in tracked:
        lowered = path.lower()
        if not any(lowered.startswith(d) for d in CROSS_PROJECT_DIRS):
            continue
        try:
            addresses = sorted(set(_EMAIL_PATTERN.findall(read_text(path))))
        except (OSError, UnicodeError):
            continue
        if addresses:
            found.append((path, addresses))
    return found


def main():
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"Failed to run git ls-files: {e}")
        sys.exit(1)

    tracked = result.stdout.splitlines()
    bad = []
    for path in tracked:
        for pattern in BLOCKED_PATTERNS:
            if pattern.endswith("/"):
                if path.startswith(pattern):
                    if not path.endswith(".gitkeep"):
                        bad.append(path)
            elif pattern.startswith("."):
                if path.endswith(pattern):
                    # Exclude docs/*.pdf
                    if not path.startswith("docs/") and not path.startswith("tests/"):
                        bad.append(path)
            else:
                if path == pattern:
                    bad.append(path)

    if bad:
        print("Public repo guard failed. Remove these tracked private files:")
        for path in bad:
            print(f" - {path}")
        sys.exit(1)

    unmarked = audit_cross_project_pages(tracked)
    if unmarked:
        print(
            "Public repo guard failed. These tracked pages are not marked for a\n"
            "public project, and this store holds every project's memory:"
        )
        for path, why in unmarked:
            print(f" - {path} ({why})")
        print(
            "\nEither add a public project tag (one of: "
            f"{', '.join(sorted(PUBLIC_PROJECT_TAGS))})\n"
            "or untrack the page:  git rm --cached <path>"
        )
        sys.exit(1)

    for path, addresses in advisory_emails(tracked):
        print(f"NOTE: {path} contains email addresses: {', '.join(addresses)}")

    print("Public repo guard passed.")

if __name__ == "__main__":
    main()
