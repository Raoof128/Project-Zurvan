import os
import glob
import hashlib
from datetime import datetime

def hash_content(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def extract_chunks_from_markdown(filepath: str):
    """
    Splits markdown by headings.
    Returns list of dicts: chunk_id, source_path, heading, text, content_hash, indexed_at
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    lines = content.split('\n')
    chunks = []
    
    current_heading = "root"
    current_text = []
    
    def finalize_chunk():
        text = "\n".join(current_text).strip()
        if text:
            # Deterministic chunk ID
            chunk_id_str = f"{filepath}::{current_heading}::{text}"
            chunk_id = hash_content(chunk_id_str)
            
            chunks.append({
                "chunk_id": chunk_id,
                "source_path": filepath,
                "heading": current_heading,
                "text": text,
                "content_hash": hash_content(text),
                "indexed_at": datetime.now().isoformat()
            })
            
    for line in lines:
        if line.startswith('#'):
            finalize_chunk()
            current_heading = line.lstrip('#').strip()
            current_text = [line]
        else:
            current_text.append(line)
            
    finalize_chunk()
    return chunks

def scan_markdown_files():
    """Scans wiki/ and docs/, explicitly ignoring raw/"""
    files = []
    for directory in ["wiki", "docs"]:
        if os.path.exists(directory):
            for filepath in glob.glob(f"{directory}/**/*.md", recursive=True):
                # Ensure no raw/ leaks
                if "raw" not in filepath.split(os.sep):
                    files.append(filepath)
    return files

def chunk_all_markdown():
    files = scan_markdown_files()
    all_chunks = []
    for f in files:
        all_chunks.extend(extract_chunks_from_markdown(f))
    return all_chunks
