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


_SOURCE_HASH_LINE = re.compile(r"^source_hash:\s*(\S+)\s*$", re.MULTILINE)


def plan_claim_writes(data: dict, source_id: str, content_hash: str, claims_dir: str,
                      source_file: str) -> list[tuple[str, str]]:
    """Every claim page this extraction will write, as `(path, content)`, with
    nothing written yet. Raises `ExtractionConflict` if any target page exists
    and either carries a different `source_hash` (another source's evidence) or
    carries this one's but says something else: accepted evidence is never
    silently replaced (roadmap F1 gate: "either preserve prior evidence or
    return an explicit conflict"; reconcile/supersession is a separate act).
    A page whose planned body is byte-identical to the one on disk is a retry
    of the same extraction and is left out of the plan: no write at all."""
    plan = []
    for claim in data.get("claims", []):
        cid = sanitize_filename(claim["claim_id"])
        name = claim_page_name(source_id, claim["claim_id"])
        if not is_safe_filename(name):
            continue
        claim_file = os.path.join(claims_dir, name)
        existing = None
        if os.path.exists(claim_file):
            with open(claim_file, "r", encoding="utf-8") as f:
                existing = f.read()
            m = _SOURCE_HASH_LINE.search(existing)
            owner = m.group(1) if m else None
            if owner != content_hash:
                raise ExtractionConflict(
                    f"claim page {os.path.basename(claim_file)} (claim '{claim['claim_id']}') "
                    f"already exists for a different source (source_hash {owner!r}); "
                    "nothing was written"
                )
        # The heading is the claim id as the model wrote it (plan Task 2); only
        # line breaks are folded so it stays one Markdown heading line.
        heading = " ".join(str(claim["claim_id"]).split())
        tags_yaml = "\n  - ".join([""] + claim.get("tags", [])) if claim.get("tags") else ""
        body = (
            f"---\ntype: claim\nclaim_id: {cid}\nsource_id: {source_id}\n"
            f"source_file: {source_file}\nsource_hash: {content_hash}\n"
            f"claim_type: {claim['claim_type']}\nconfidence: {claim['confidence']}\n"
            f"tags:{tags_yaml}\n---\n\n# {heading}\n\n## Claim\n\n{claim['text']}\n\n## Evidence\n\n"
        )
        for ev in claim["evidence"]:
            body += (f"> \"{ev['quote']}\"\n\nLocation: {ev.get('location', 'Unknown')}\n"
                     f"Source: [[sources/{source_file}]]\n\n")
        if existing is not None:
            if existing == body:
                continue  # identical retry: nothing to write
            raise ExtractionConflict(
                f"claim page {os.path.basename(claim_file)} (claim '{claim['claim_id']}') "
                "already exists for this source with different content; accepted "
                "evidence is not replaced. Nothing was written"
            )
        plan.append((claim_file, body))
    return plan


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

    # Plan every claim page BEFORE anything is written: a cross-owner
    # collision aborts the whole extraction with nothing on disk.
    claims_dir = os.path.join("wiki", "claims")
    # The conflict propagates: a caller (and the CLI's exit status) must be
    # able to tell "refused" from "done".
    claim_writes = plan_claim_writes(data, source_id, content_hash, claims_dir, basename)

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
    
    # Claims (planned and conflict-checked above)
    os.makedirs(claims_dir, exist_ok=True)
    for claim_file, body in claim_writes:
        with open(claim_file, "w", encoding="utf-8") as f:
            f.write(body)

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
