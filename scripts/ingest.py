import os
import argparse
import hashlib
import datetime
import json
import re as _re_ingest
from pypdf import PdfReader
from scripts.db import register_source

def calculate_hash(filepath):
    sha256 = hashlib.sha256()
    with open(filepath, 'rb') as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()

def extract_text(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    if ext in ['.txt', '.md']:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    elif ext == '.pdf':
        reader = PdfReader(filepath)
        text = ''
        for page in reader.pages:
            text += page.extract_text() + '\n'
        return text
    else:
        raise ValueError(f"Unsupported file format: {ext}")

def create_source_page(filepath, file_hash, text):
    basename = os.path.basename(filepath)
    filename = f"{basename}.md"
    source_dir = os.path.join("wiki", "sources")
    os.makedirs(source_dir, exist_ok=True)
    out_path = os.path.join(source_dir, filename)
    
    content = f"""---
title: {basename}
hash: {file_hash}
ingested_at: {datetime.datetime.now().isoformat()}
---

# {basename}

## Extracted Text
{text[:1000]}...
*(Text truncated for preview, see raw source for full content)*

## Extracted Entities & Claims
*(To be populated by LLM)*
"""
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(content)
    return filename

def create_stub_concept_and_claim(basename):
    # This is a stub for LLM extraction
    # Create a dummy concept
    concept_dir = os.path.join("wiki", "concepts")
    os.makedirs(concept_dir, exist_ok=True)
    with open(os.path.join(concept_dir, f"AutoConcept-{basename}.md"), 'w') as f:
        f.write(f"---\ntitle: AutoConcept-{basename}\n---\n# AutoConcept-{basename}\n\nGenerated from [{basename}](../sources/{basename}.md)")

    # Create a dummy claim
    claim_dir = os.path.join("wiki", "claims")
    os.makedirs(claim_dir, exist_ok=True)
    with open(os.path.join(claim_dir, f"Claim-{basename}.md"), 'w') as f:
        f.write(f"---\ntitle: Claim-{basename}\n---\n# Claim-{basename}\n\nEvidence missing? No, cited from [{basename}](../sources/{basename}.md)")

def append_log(filename):
    from scripts.wiki_merge import append_log_ingest
    append_log_ingest(filename)

def update_index(filename):
    # Basic index update, rebuild_index.py handles more comprehensive rebuilds
    index_path = os.path.join("wiki", "index.md")
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

    source_dir = os.path.join("wiki", "sources")
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
    manifest_path = os.path.join("data", "image_manifest.json")
    os.makedirs("data", exist_ok=True)
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
    
    if not register_source(args.filepath, file_hash):
        print(f"Source '{args.filepath}' has already been ingested.")
        return

    print(f"Ingesting {args.filepath}...")
    text = extract_text(args.filepath)
    filename = create_source_page(args.filepath, file_hash, text)
    create_stub_concept_and_claim(os.path.basename(args.filepath))
    append_log(filename)
    update_index(filename)
    
    print(f"Successfully ingested {args.filepath}")

if __name__ == "__main__":
    main()
