import os
import argparse
import hashlib
import datetime
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
