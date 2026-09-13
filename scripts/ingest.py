import os
import argparse
import hashlib
import datetime
import json
import re as _re_ingest
from pypdf import PdfReader
from scripts.db import find_source_by_hash, register_source, unregister_source
from scripts.config import PROJECT_ROOT

def require_ingest_dependencies():
    """Fail fast, and legibly, if the interpreter can't finish an ingest.

    ``append_log`` reaches PyYAML through ``wiki_merge`` → ``safe_write``, but
    only at the very end of the run. On an interpreter without PyYAML (a bare
    Homebrew ``python3``, say) the ingest therefore wrote the source page and
    then died on an import — so check before touching anything. Mirrors the
    interpreter probe in ``scripts/zurvan``.
    """
    try:
        import yaml  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            f"Error: this interpreter cannot run an ingest ({exc}).\n"
            "Zurvan needs an interpreter with PyYAML installed. Either run the\n"
            "wrapper, which probes for one:\n"
            "    scripts/zurvan ...\n"
            "or point PYTHONPATH at the repo and use that interpreter directly:\n"
            "    PYTHONPATH=. <python-with-pyyaml> -m scripts.ingest <file>\n"
            "ZURVAN_PYTHON=<path> selects the interpreter for the wrapper."
        )

def calculate_hash(filepath):
    sha256 = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()

# The 7 ligatures MuPDF knows about. pypdf emits these glyphs verbatim, which
# makes the affected words invisible to BM25 ("identiﬁable" is not "identifiable")
# and impossible to quote in a claim, since `claim add` checks the evidence
# byte-for-byte against the page.
LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl",
    "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "ft", "ﬆ": "st",
}

_PARAGRAPH_BREAK = "␞"  # placeholder while unwrapping; not valid in PDF text


def normalize_extracted_text(text: str) -> str:
    """Repair the extraction artefacts that break search and claim anchoring.

    Two defects, both observed in real ingests:
      * ligature glyphs (191 across three Anthropic PDFs under pypdf);
      * one-word-per-line wrapping — one paper came through with 98% of its
        lines holding a single word, which defeats phrase search entirely.
    Blank lines are preserved as paragraph boundaries; everything else is
    unwrapped back into running text.

    Deliberately does NOT join hyphens at line ends. PyMuPDF's TEXT_DEHYPHENATE
    flag has no effect on extraction output (it only joins within a block), and
    a naive join corrupts legitimate compounds — "state-\\nof-the-art" would
    become "stateof-the-art". Measured cost of leaving them: 39 hyphen-breaks
    across 245k characters.
    """
    for glyph, plain in LIGATURES.items():
        text = text.replace(glyph, plain)
    text = text.replace(" ", " ")
    # Only a TRULY empty line is a paragraph break. A whitespace-only line
    # ("\n \n") is the word separator in one-word-per-line output, not a
    # paragraph: treating it as one leaves pypdf's output at 97% one-word lines
    # and invents thousands of false paragraphs. Measured on the real corpus.
    text = text.replace("\r\n", "\n")
    text = _re_ingest.sub(r"\n\n+", _PARAGRAPH_BREAK, text)
    text = _re_ingest.sub(r"[ \t]*\n[ \t]*", " ", text)
    text = text.replace(_PARAGRAPH_BREAK, "\n\n")
    return _re_ingest.sub(r"[ \t]{2,}", " ", text).strip()


def _extract_pdf(filepath: str) -> str:
    """PDF text via PyMuPDF, falling back to pypdf.

    PyMuPDF is ~4x faster than pypdf on this corpus (0.71s vs 2.88s over 93
    pages) and emits no ligature glyphs. pypdf stays as the fallback so an
    environment without PyMuPDF still ingests rather than failing.
    """
    try:
        import pymupdf
    except ImportError:
        reader = PdfReader(filepath)
        return "\n".join((page.extract_text() or "") for page in reader.pages)

    with pymupdf.open(filepath) as doc:
        return "\n".join(page.get_text("text") for page in doc)


def extract_text(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    if ext in ['.txt', '.md']:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    elif ext == '.pdf':
        return normalize_extracted_text(_extract_pdf(filepath))
    else:
        raise ValueError(f"Unsupported file format: {ext}")

def pdf_figure_inventory(filepath: str) -> list:
    """Per-page count of embedded images in a PDF. No extraction, no OCR.

    A PDF's text layer contains nothing of what a chart shows. In one real
    ingest four raster charts held per-model values that appear nowhere in the
    prose, and the caption-count heuristic missed them entirely because that
    paper used no "Figure N" captions. Nothing warned about any of it.

    This records what is there so the gap is stated rather than silent; reading
    the figures is a separate, opt-in visual pass (``scripts/export_figures.py``).
    """
    try:
        import pymupdf
    except ImportError:
        return []

    figures = []
    try:
        with pymupdf.open(filepath) as doc:
            for number, page in enumerate(doc, start=1):
                count = len(page.get_images(full=True))
                if count:
                    figures.append({"page": number, "images": count})
    except Exception:
        return []
    return figures


def render_figure_notice(figures: list) -> str:
    """The 'these are not transcribed' banner appended to a source page."""
    if not figures:
        return ""
    total = sum(f["images"] for f in figures)
    pages = ", ".join(str(f["page"]) for f in figures)
    return (
        "\n## Figures — NOT transcribed\n\n"
        f"This PDF contains **{total} embedded image(s)** across "
        f"{len(figures)} page(s): {pages}.\n\n"
        "Their content is **absent from the text above** — a chart's values, a "
        "diagram's labels and any text inside a screenshot do not appear in a "
        "PDF's text layer. Do not treat this page as complete coverage of the "
        "document, and do not anchor a claim to a figure through it.\n\n"
        "To read them: `python scripts/export_figures.py <pdf>` writes the "
        "images to disk for a visual pass, then record what they show in a "
        "companion note.\n"
    )


def source_page_stem(filepath):
    """Sanitised, extension-free slug for a source's derived wiki pages.

    Dropping the original extension keeps PDF/DOCX-derived pages out of the
    public_repo_guard blocklist (which matches the substring ``.pdf``/``.docx``)
    and avoids the double ``.md.md`` suffix; sanitising matches extract.py's
    ``source_id`` convention so the pages a source produces share one slug.
    """
    from scripts.filename_utils import sanitize_filename
    basename = os.path.basename(filepath)
    return sanitize_filename(os.path.splitext(basename)[0])

def create_source_page(filepath, file_hash, text):
    basename = os.path.basename(filepath)
    stem = source_page_stem(filepath)
    filename = f"{stem}.md"
    source_dir = str(PROJECT_ROOT / "wiki" / "sources")
    os.makedirs(source_dir, exist_ok=True)
    out_path = os.path.join(source_dir, filename)

    figures = pdf_figure_inventory(filepath) if filepath.lower().endswith(".pdf") else []
    figure_count = sum(f["images"] for f in figures)

    content = f"""---
title: {basename}
hash: {file_hash}
figures: {figure_count}
figures_transcribed: false
ingested_at: {datetime.datetime.now().isoformat()}
---

# {basename}

## Extracted Text

{text}
{render_figure_notice(figures)}
## Extracted Entities & Claims
*(To be populated by LLM)*
"""
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(content)
    return filename

def create_stub_concept_and_claim(filepath):
    # This is a stub for LLM extraction (no LLM output = no fabricated evidence)
    basename = os.path.basename(filepath)
    stem = source_page_stem(filepath)
    # Create a dummy concept
    concept_dir = str(PROJECT_ROOT / "wiki" / "concepts")
    os.makedirs(concept_dir, exist_ok=True)
    with open(os.path.join(concept_dir, f"AutoConcept-{stem}.md"), 'w') as f:
        f.write(f"---\ntitle: AutoConcept-{stem}\n---\n# AutoConcept-{stem}\n\nGenerated from [{basename}](../sources/{stem}.md)")

    # Create a dummy claim
    claim_dir = str(PROJECT_ROOT / "wiki" / "claims")
    os.makedirs(claim_dir, exist_ok=True)
    with open(os.path.join(claim_dir, f"Claim-{stem}.md"), 'w') as f:
        f.write(f"---\ntitle: Claim-{stem}\n---\n# Claim-{stem}\n\nEvidence missing? No, cited from [{basename}](../sources/{stem}.md)")

def append_log(filename):
    from scripts.wiki_merge import append_log_ingest
    append_log_ingest(filename)

def update_index(filename):
    # Basic index update, rebuild_index.py handles more comprehensive rebuilds
    index_path = str(PROJECT_ROOT / "wiki" / "index.md")
    with open(index_path, 'a', encoding='utf-8') as f:
        f.write(f"- [sources/{filename}](sources/{filename})\n")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def is_image_file(filepath: str) -> bool:
    return os.path.splitext(filepath)[1].lower() in IMAGE_EXTENSIONS


def scan_for_embedded_images(text: str) -> list:
    """Return list of {path, is_remote} dicts for all ![alt](url) references in text."""
    refs = []
    for m in _re_ingest.finditer(r"!\[.*?\]\((.+?)\)", text):
        path = m.group(1).strip()
        refs.append({
            "path": path,
            "is_remote": path.startswith("http://") or path.startswith("https://"),
        })
    return refs


def log_embedded_image_refs(refs: list) -> None:
    """Log each embedded image reference as image-skip. Never downloads anything."""
    from scripts.wiki_merge import append_log_image_skip
    for ref in refs:
        append_log_image_skip(ref["path"])


def ingest_image_stub(filepath: str) -> None:
    """Create a pending-visual stub for an image file. No extraction, no OCR, no network."""
    from scripts.wiki_merge import append_log_image_skip

    basename = os.path.basename(filepath)
    slug = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in basename)

    source_dir = str(PROJECT_ROOT / "wiki" / "sources")
    os.makedirs(source_dir, exist_ok=True)

    # Collision-safe: loop until we find an unused filename
    base_candidate = os.path.join(source_dir, f"{slug}.md")
    candidate = base_candidate
    counter = 2
    while os.path.exists(candidate):
        candidate = os.path.join(source_dir, f"{slug}-{counter}.md")
        counter += 1

    # Path in frontmatter must match the ACTUAL file written, not the original slug
    relative_path = f"sources/{os.path.basename(candidate)}"

    content = (
        f"---\n"
        f"type: image-stub\n"
        f"status: pending-visual\n"
        f"path: {relative_path}\n"
        f"original: {basename}\n"
        f"ingested_at: {datetime.datetime.now().isoformat()}\n"
        f"---\n\n"
        f"# {basename}\n\n"
        f"Image detected but not processed. Pending visual extraction.\n"
    )
    with open(candidate, "w", encoding="utf-8") as f:
        f.write(content)

    # Append to manifest JSON
    manifest_path = str(PROJECT_ROOT / "data" / "image_manifest.json")
    os.makedirs(str(PROJECT_ROOT / "data"), exist_ok=True)
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        manifest = []
    manifest.append({
        "type": "image",
        "path": relative_path,
        "original": basename,
        "status": "pending",
        "reason": "image-skip",
        "ingested_at": datetime.datetime.now().isoformat(),
    })
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    append_log_image_skip(basename)
    print(f"Image detected but not processed: {basename}")


def main():
    parser = argparse.ArgumentParser(description="Ingest a raw source document")
    parser.add_argument("filepath", help="Path to the raw source file")
    args = parser.parse_args()

    require_ingest_dependencies()

    if not os.path.exists(args.filepath):
        print(f"Error: File '{args.filepath}' does not exist.")
        return

    # Check if in raw directory
    if not os.path.abspath(args.filepath).startswith(os.path.abspath("raw")):
        print("Warning: File is not in the 'raw/' directory. Best practice is to ingest from 'raw/'.")

    # Detect image files before attempting text extraction
    if is_image_file(args.filepath):
        ingest_image_stub(args.filepath)
        return

    file_hash = calculate_hash(args.filepath)
    
    existing = find_source_by_hash(file_hash)
    if existing is not None and existing != args.filepath:
        print(
            f"Source '{args.filepath}' has the same content as already-ingested "
            f"'{existing}'. Nothing to do."
        )
        return

    if not register_source(args.filepath, file_hash):
        print(f"Source '{args.filepath}' has already been ingested.")
        return

    print(f"Ingesting {args.filepath}...")
    try:
        text = extract_text(args.filepath)
        filename = create_source_page(args.filepath, file_hash, text)
        create_stub_concept_and_claim(args.filepath)
        append_log(filename)
        update_index(filename)
    except BaseException:
        # The registration above claims the path before any of this work runs.
        # Release it so the half-finished ingest can simply be re-run, instead
        # of the retry being refused as "already been ingested".
        unregister_source(args.filepath)
        raise

    print(f"Successfully ingested {args.filepath}")

if __name__ == "__main__":
    main()
