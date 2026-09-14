"""Decide which open-questions file a question belongs in.

`wiki/open-questions.md` is a SINGLE TRACKED file on a public remote, so every
question ever recorded shared one publishability decision: one private question
made the whole file unpublishable. In practice that is exactly what happened —
115 questions accumulated there naming coursework units, a tenancy project and
a research corpus, and the file simply stayed uncommitted across sessions, which
is a workaround rather than a fix.

Splitting it makes the trap structurally impossible instead of something each
session has to remember:

    wiki/open-questions.md          TRACKED   — publishable questions only
    wiki/open-questions.private.md  gitignored + guard-blocked — everything else

**Fail closed.** A question is public only when it explicitly carries a tag
naming a project in PUBLIC_PROJECTS. No tags, unknown tags, or any private tag
all route to the private file. That mirrors the rule already enforced for
tracked pages: publishability must be DECLARED, never inferred, because the
safe direction is to withhold.
"""
import os

from scripts.config import PROJECT_ROOT

PUBLIC_FILE = "open-questions.md"
PRIVATE_FILE = "open-questions.private.md"


def _public_projects():
    from scripts.public_repo_guard import PUBLIC_PROJECTS
    return {p.lower() for p in PUBLIC_PROJECTS}


def _private_tags():
    from scripts.public_repo_guard import PRIVATE_PROJECT_TAGS
    return {p.lower() for p in PRIVATE_PROJECT_TAGS}


def is_publishable(tags) -> bool:
    """True only if the tags declare a public project and none is private."""
    normalised = {str(t).strip().lower() for t in (tags or []) if str(t).strip()}
    if not normalised:
        return False                      # undeclared is not public
    if normalised & _private_tags():
        return False                      # an explicit private tag always vetoes
    return bool(normalised & _public_projects())


def questions_path(tags) -> str:
    """Absolute path of the file this question should be appended to."""
    name = PUBLIC_FILE if is_publishable(tags) else PRIVATE_FILE
    return str(PROJECT_ROOT / "wiki" / name)


def all_questions_paths():
    """Both files, for readers that should see every question locally."""
    return [str(PROJECT_ROOT / "wiki" / PUBLIC_FILE),
            str(PROJECT_ROOT / "wiki" / PRIVATE_FILE)]


def count_questions(paths=None) -> int:
    total = 0
    for p in (paths or all_questions_paths()):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                total += sum(1 for line in fh if line.startswith("## Q:"))
    return total
