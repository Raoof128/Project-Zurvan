import os
import argparse
import json
from scripts.llm import run_llm
from scripts.validate_extraction import validate_extraction_json, is_safe_filename
from scripts.ingest import extract_text # reuse extraction logic

from scripts.filename_utils import sanitize_filename

def extract_source(filepath):
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return

    basename = os.path.basename(filepath)
    source_id = sanitize_filename(os.path.splitext(basename)[0])
    
    # 1. Read the source Markdown/text
    source_text = extract_text(filepath)
    
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

    # 7. Save raw JSON
    extraction_path = os.path.join("data", "extractions", f"{source_id}.json")
    with open(extraction_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Saved raw extraction to {extraction_path}")

    # 8. Generate Markdown pages
    
    # Summaries
    if data.get("summary"):
        summary_file = os.path.join("wiki", "summaries", f"{source_id}_summary.md")
        if is_safe_filename(summary_file):
            with open(summary_file, "w", encoding="utf-8") as f:
                f.write(f"---\ntype: summary\nsource_id: {source_id}\n---\n# Summary: {source_id}\n\n## Short\n{data['summary']['short']}\n\n## Detailed\n{data['summary']['detailed']}\n")
    
    # Claims
    claims_dir = os.path.join("wiki", "claims")
    os.makedirs(claims_dir, exist_ok=True)
    for claim in data.get("claims", []):
        cid = sanitize_filename(claim["claim_id"])
        claim_file = os.path.join(claims_dir, f"{cid}.md")
        if is_safe_filename(claim_file):
            tags_yaml = "\\n  - ".join([""] + claim.get("tags", [])) if claim.get("tags") else ""
            with open(claim_file, "w", encoding="utf-8") as f:
                f.write(f"---\ntype: claim\nclaim_id: {cid}\nsource_id: {source_id}\nclaim_type: {claim['claim_type']}\nconfidence: {claim['confidence']}\ntags:{tags_yaml}\n---\n\n# {cid}\n\n## Claim\n\n{claim['text']}\n\n## Evidence\n\n")
                for ev in claim["evidence"]:
                    f.write(f"> \"{ev['quote']}\"\n\nLocation: {ev.get('location', 'Unknown')}\nSource: [[sources/{basename}]]\n\n")

    # Concepts
    concepts_dir = os.path.join("wiki", "concepts")
    os.makedirs(concepts_dir, exist_ok=True)
    for concept in data.get("concepts", []):
        cname = sanitize_filename(concept["name"])
        concept_file = os.path.join(concepts_dir, f"{cname}.md")
        if is_safe_filename(concept_file):
            with open(concept_file, "w", encoding="utf-8") as f:
                f.write(f"---\ntype: concept\nsource_id: {source_id}\n---\n\n# {concept['name']}\n\n## Definition\n{concept['definition']}\n")

    # Entities
    entities_dir = os.path.join("wiki", "entities")
    os.makedirs(entities_dir, exist_ok=True)
    for ent in data.get("entities", []):
        ename = sanitize_filename(ent["name"])
        ent_file = os.path.join(entities_dir, f"{ename}.md")
        if is_safe_filename(ent_file):
            with open(ent_file, "w", encoding="utf-8") as f:
                f.write(f"---\ntype: entity\nentity_type: {ent.get('entity_type', 'other')}\nsource_id: {source_id}\n---\n\n# {ent['name']}\n\n{ent['description']}\n")
                
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
    extract_source(args.source)
