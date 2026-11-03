import os
import argparse
import hashlib
import json
import re
from scripts.llm import run_llm
from scripts.validate_extraction import validate_extraction_json, is_safe_filename
from scripts.ingest import extract_text # reuse extraction logic

from scripts.filename_utils import sanitize_filename


class ExtractionConflict(RuntimeError):
    """A claim page this extraction would write belongs to a different source."""


def source_identity(filepath: str, source_text: str) -> tuple[str, str]:
    """`(source_id, content_hash)`: the id names the content, not the path.

    Audit 2026-10-22 F1: ids derived from the basename alone, so two
    `source.md` files in different directories shared one extraction, one
    summary and one claim namespace. The hash of the extracted text is what
    makes two occurrences of the same bytes one identity and two different
    files two."""
    content_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    stem = sanitize_filename(os.path.splitext(os.path.basename(filepath))[0])
    return f"{stem}-{content_hash[:12]}", content_hash


def claim_page_name(source_id: str, claim_id: str) -> str:
    return f"{source_id}--{sanitize_filename(claim_id)}.md"


_SOURCE_HASH_LINE = re.compile(r"^source_hash:\s*(\S+)\s*$", re.MULTILINE)


def plan_claim_writes(data: dict, source_id: str, content_hash: str, claims_dir: str,
                      source_file: str) -> list[tuple[str, str]]:
    """Every claim page this extraction will write, as `(path, content)`, with
    nothing written yet. Raises `ExtractionConflict` if any target page exists
    and carries a different `source_hash` — the model may not pick a filename
    that destroys another source's evidence. A page with the same hash is the
    same bytes re-extracted and may be rewritten."""
    plan = []
    for claim in data.get("claims", []):
        cid = sanitize_filename(claim["claim_id"])
        name = claim_page_name(source_id, claim["claim_id"])
        if not is_safe_filename(name):
            continue
        claim_file = os.path.join(claims_dir, name)
        if os.path.exists(claim_file):
            with open(claim_file, "r", encoding="utf-8") as f:
                m = _SOURCE_HASH_LINE.search(f.read())
            owner = m.group(1) if m else None
            if owner != content_hash:
                raise ExtractionConflict(
                    f"claim page {os.path.basename(claim_file)} (claim '{claim['claim_id']}') "
                    f"already exists for a different source (source_hash {owner!r}); "
                    "nothing was written"
                )
        tags_yaml = "\n  - ".join([""] + claim.get("tags", [])) if claim.get("tags") else ""
        body = (
            f"---\ntype: claim\nclaim_id: {cid}\nsource_id: {source_id}\n"
            f"source_file: {source_file}\nsource_hash: {content_hash}\n"
            f"claim_type: {claim['claim_type']}\nconfidence: {claim['confidence']}\n"
            f"tags:{tags_yaml}\n---\n\n# {cid}\n\n## Claim\n\n{claim['text']}\n\n## Evidence\n\n"
        )
        for ev in claim["evidence"]:
            body += (f"> \"{ev['quote']}\"\n\nLocation: {ev.get('location', 'Unknown')}\n"
                     f"Source: [[sources/{source_file}]]\n\n")
        plan.append((claim_file, body))
    return plan


def extract_source(filepath):
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return

    from scripts.ingest import is_image_file
    if is_image_file(filepath):
        print(f"Skipping LLM extraction for image file: {filepath}")
        return

    basename = os.path.basename(filepath)

    # 1. Read the source Markdown/text
    source_text = extract_text(filepath)
    source_id, content_hash = source_identity(filepath, source_text)

    # Best-effort embedded image detection — never breaks ingestion
    if filepath.lower().endswith(".pdf"):
        try:
            from pypdf import PdfReader
            from scripts.ingest import log_embedded_image_refs
            reader = PdfReader(filepath)
            image_refs = []
            for page in reader.pages:
                if hasattr(page, "images") and page.images:
                    for img in page.images:
                        image_refs.append({"path": getattr(img, "name", "embedded"), "is_remote": False})
            if image_refs:
                log_embedded_image_refs(image_refs)
        except Exception:
            pass  # Best-effort: never break PDF text extraction

    if filepath.lower().endswith((".md", ".txt")):
        try:
            from scripts.ingest import scan_for_embedded_images, log_embedded_image_refs
            refs = scan_for_embedded_images(source_text)
            if refs:
                log_embedded_image_refs(refs)
        except Exception:
            pass

    # 2. Load scripts/prompts/extract_source.md
    prompt_path = os.path.join("scripts", "prompts", "extract_source.md")
    with open(prompt_path, "r", encoding="utf-8") as f:
        prompt_template = f.read()
        
    # 3. Insert source text into the prompt
    prompt = prompt_template.replace("{{SOURCE_TEXT}}", source_text)
    
    # 4. Call run_llm()
    print("Calling LLM extraction...")
    json_output = run_llm(prompt, temperature=0.0)
    
    # Strip markdown codeblocks if LLM returned them
    if json_output.startswith("```json"):
        json_output = json_output[7:]
    if json_output.endswith("```"):
        json_output = json_output[:-3]
    json_output = json_output.strip()

    # 5 & 6. Parse JSON and Validate schema
    try:
        data = validate_extraction_json(json_output, source_text)
    except ValueError as e:
        print(f"Validation failed: {e}")
        return
        
    # Override source_id from LLM with our deterministic one for safety
    data["source_id"] = source_id

    # Plan every claim page BEFORE anything is written: a cross-owner
    # collision aborts the whole extraction with nothing on disk.
    claims_dir = os.path.join("wiki", "claims")
    try:
        claim_writes = plan_claim_writes(data, source_id, content_hash, claims_dir, basename)
    except ExtractionConflict as e:
        print(f"Extraction conflict: {e}")
        return

    # 7. Save raw JSON
    os.makedirs(os.path.join("data", "extractions"), exist_ok=True)
    extraction_path = os.path.join("data", "extractions", f"{source_id}.json")
    with open(extraction_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Saved raw extraction to {extraction_path}")

    # 8. Generate Markdown pages
    
    # Summaries
    if data.get("summary"):
        os.makedirs(os.path.join("wiki", "summaries"), exist_ok=True)
        summary_file = os.path.join("wiki", "summaries", f"{source_id}_summary.md")
        if is_safe_filename(summary_file):
            with open(summary_file, "w", encoding="utf-8") as f:
                f.write(f"---\ntype: summary\nsource_id: {source_id}\n---\n# Summary: {source_id}\n\n## Short\n{data['summary']['short']}\n\n## Detailed\n{data['summary']['detailed']}\n")
    
    # Claims (planned and conflict-checked above)
    os.makedirs(claims_dir, exist_ok=True)
    for claim_file, body in claim_writes:
        with open(claim_file, "w", encoding="utf-8") as f:
            f.write(body)

    # Concepts and Entities: canonical writer is wiki_merge (compounding wiki)
    from scripts.wiki_merge import merge_extraction
    merge_extraction(data)
                
    # Open questions (append to list)
    if data.get("open_questions"):
        oq_file = os.path.join("wiki", "open-questions.md")
        with open(oq_file, "a", encoding="utf-8") as f:
            for oq in data["open_questions"]:
                f.write(f"\n- **{oq['question']}** (from {source_id}): {oq['reason']}\n")
                
    print("Markdown generation complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract knowledge from source")
    parser.add_argument("--source", required=True, help="Path to the source markdown file")
    args = parser.parse_args()
    # One extraction is one logical operation: the merged pages and the log
    # line commit as a single revision, and `safe_write` refuses them without
    # a boundary (2026-09-28).
    from scripts import knowledge_revision as _kr
    with _kr.operation(writer="extract", caller="scripts.extract:__main__", eager=True):
        extract_source(args.source)
