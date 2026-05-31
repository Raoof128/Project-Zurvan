import os
import json
import shutil
import hashlib
from pathlib import Path

from scripts.publication_export import export_publication
from scripts.publication_safety import get_publications_dir
from scripts.report_compose import inspect_report
from scripts.review_audit import audit_report

def create_bundle(report_id: str, fmt: str = "directory", force: bool = False, output_dir: Path = None) -> Path:
    if not output_dir:
        output_dir = get_publications_dir()
        
    bundle_name = f"bundle_{report_id}"
    bundle_dir = output_dir / bundle_name
    
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True)
    
    # 1. Export HTML
    html_file = export_publication(report_id, "html", force, bundle_dir)
    
    # 2. Export MD
    md_file = export_publication(report_id, "markdown", force, bundle_dir)
    
    # 3. Export JSON
    json_file = export_publication(report_id, "json", force, bundle_dir)
    
    # 4. Create Manifest
    def hash_file(p: Path):
        return hashlib.sha256(p.read_bytes()).hexdigest()
        
    manifest = {
        "report_id": report_id,
        "bundle_format": fmt,
        "files": {
            html_file.name: hash_file(html_file),
            md_file.name: hash_file(md_file),
            json_file.name: hash_file(json_file)
        }
    }
    
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    
    if fmt == "zip":
        shutil.make_archive(str(bundle_dir), "zip", str(bundle_dir))
        shutil.rmtree(bundle_dir)
        return output_dir / f"{bundle_name}.zip"
        
    return bundle_dir
