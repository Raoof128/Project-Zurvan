import os
import argparse
import hashlib
import json
import re
import sys
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




def _source_ref(filepath: str) -> str:
    """The path the ledger records: store-relative when the file is inside the
    store, absolute otherwise (the same rule `claim add` accepts)."""
    from scripts import knowledge_scope as ks
    absolute = os.path.abspath(filepath)
    base = str(ks.store_root())
    if absolute == base or absolute.startswith(base + os.sep):
        return os.path.relpath(absolute, base).replace(os.sep, "/")
    return absolute


def plan_claim_records(data: dict, file_sha: str) -> list[dict]:
    """The claims this extraction will add, with nothing written yet (spec §7.9).

    Claim identity comes from the ledger, never from the model: the model's id
    only forms `extraction_key = sha256(source file bytes) + ":" + model id`.
    A key already in the ledger with the same text and quotes is a retry and is
    left out; the same key with different text raises `ExtractionConflict`
    (accepted evidence is never silently replaced - supersede it by hand with
    `zurvan claim supersede`). Raises before anything is written."""
    from scripts import claim_ledger
    records = []
    for claim in data.get("claims", []):
        key = f"{file_sha}:{claim['claim_id']}"
        quotes = [ev["quote"] for ev in claim["evidence"]]
        existing = claim_ledger.find_by_extraction_key(key)
        if existing is not None:
            have = sorted((e.get("selector") or {}).get("TextQuoteSelector", {}).get("exact", "")
                          for e in existing.evidence)
            if existing.text == claim["text"] and have == sorted(quotes):
                continue
            raise ExtractionConflict(
                f"claim '{claim['claim_id']}' of this source is already in the ledger as "
                f"{existing.claim_id} with different content; accepted evidence is not "
                "replaced. Nothing was written")
        records.append({"key": key, "claim": claim})
    return records


def summary_page(source_id: str, source_file: str, summary: dict) -> str:
    """The summary page. `source_file` is what links it to the ingested source page:
    `source_id` is content-identified (F1) and no longer equals that page's stem."""
    return (f"---\ntype: summary\nsource_id: {source_id}\nsource_file: {source_file}\n---\n"
            f"# Summary: {source_id}\n\n## Short\n{summary['short']}\n\n"
            f"## Detailed\n{summary['detailed']}\n")


def new_open_question_lines(oq_file: str, source_id: str, questions: list[dict]) -> list[str]:
    """The list items to append for `source_id`, skipping any (source_id,
    question) already in `oq_file` or repeated within `questions`. The line shape
    is the one this module has always written, so existing files stay valid."""
    existing = ""
    if os.path.exists(oq_file):
        with open(oq_file, "r", encoding="utf-8") as f:
            existing = f.read()
    seen_lines = set(existing.splitlines())
    lines = []
    for oq in questions:
        question = " ".join(str(oq["question"]).split())
        marker = f"- **{question}** (from {source_id}): "
        if any(l.startswith(marker) for l in seen_lines):
            continue
        reason = " ".join(str(oq["reason"]).split())
        seen_lines.add(marker + reason)
        lines.append(f"\n{marker}{reason}\n")
    return lines


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

    # Plan every claim BEFORE anything is written: a conflicting re-extraction
    # aborts the whole extraction with nothing on disk. The conflict propagates:
    # a caller (and the CLI's exit status) must tell "refused" from "done".
    with open(filepath, "rb") as f:
        file_sha = hashlib.sha256(f.read()).hexdigest()
    claim_records = plan_claim_records(data, file_sha)
    source_ref = _source_ref(filepath)

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
                f.write(summary_page(source_id, basename, data["summary"]))
    
    # Claims (planned and conflict-checked above), through the Z2 ledger
    from scripts import claim_ledger
    for rec in claim_records:
        claim = rec["claim"]
        evs = claim["evidence"]
        claim_ledger.add(
            claim["text"], source_ref, evs[0]["quote"],
            confidence=str(claim["confidence"]).strip().lower(),
            tags=list(claim.get("tags") or []), actor="extract", method="model_extraction",
            reader="extract_text@1", claim_type=claim.get("claim_type"),
            extraction_key=rec["key"], location_hint=evs[0].get("location"),
            extra_evidence=[(e["quote"], e.get("location")) for e in evs[1:]])

    # Concepts and Entities: canonical writer is wiki_merge (compounding wiki)
    from scripts.wiki_merge import merge_extraction
    merge_extraction(data)
                
    # Open questions (append to list), idempotent per (source_id, question)
    if data.get("open_questions"):
        oq_file = os.path.join("wiki", "open-questions.md")
        new_lines = new_open_question_lines(oq_file, source_id, data["open_questions"])
        if new_lines:
            with open(oq_file, "a", encoding="utf-8") as f:
                f.write("".join(new_lines))

    print("Markdown generation complete.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract knowledge from source")
    parser.add_argument("--source", required=True, help="Path to the source markdown file")
    args = parser.parse_args()
    # One extraction is one logical operation: the merged pages and the log
    # line commit as a single revision, and `safe_write` refuses them without
    # a boundary (2026-09-28).
    from scripts import knowledge_revision as _kr
    conflict = None
    with _kr.operation(writer="extract", caller="scripts.extract:__main__", eager=True):
        try:
            extract_source(args.source)
        except ExtractionConflict as e:
            # Caught INSIDE the operation: the refusal wrote nothing, so the
            # operation ends clean and mints no revision (an exception through
            # it would be recorded as a crash). The exit status is below.
            conflict = e
    if conflict is not None:
        print(f"Extraction conflict: {conflict}", file=sys.stderr)
        sys.exit(2)
