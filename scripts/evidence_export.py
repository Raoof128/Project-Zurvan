import json
import os
from pathlib import Path
from scripts.evidence_pack import inspect_evidence_pack, _get_evidence_dir
from scripts.evidence_redact import redact_evidence_pack_items

def _format_markdown(pack_data: dict) -> str:
    manifest = pack_data["manifest"]
    items = pack_data["items"]
    
    bundle = [
        "# Zurvan Evidence Pack",
        f"## Topic\n{manifest['topic']}\n",
        f"## Projects Scanned\n{', '.join(manifest['projects_scanned']) if manifest['projects_scanned'] else 'All available'}\n",
        "## Executive Evidence Summary",
        f"Generated on: {manifest['created_at']}",
        f"Total Evidence Items: {manifest['item_count']}",
        f"Redaction Status: {manifest['redaction_status']}\n"
    ]
    
    # Group items by source kind
    claims = []
    decisions = []
    sources = []
    radars = []
    graph = []
    others = []
    
    for it in items:
        kind = it.get("source_kind") or it.get("item_type")
        if kind == "claim":
            claims.append(it)
        elif kind == "decision":
            decisions.append(it)
        elif kind == "source":
            sources.append(it)
        elif kind == "radar_ping" or kind == "contradiction":
            radars.append(it)
        elif kind == "graph_neighbor":
            graph.append(it)
        else:
            others.append(it)
            
    def _add_section(title, lst):
        bundle.append(f"## {title}")
        if not lst:
            bundle.append("None found.\n")
            return
            
        for i, it in enumerate(lst):
            bundle.append(f"### {i+1}. {it['title']} ({it['project']})")
            if "status" in it and it["status"]:
                bundle.append(f"**Status**: {it['status']}")
            if "confidence" in it and it["confidence"]:
                bundle.append(f"**Confidence**: {it['confidence']}")
            if "reason" in it and it["reason"]:
                bundle.append(f"**Reason**: {it['reason']}")
            bundle.append(f"**Path**: `{it['source_path']}`")
            bundle.append(f"**Excerpt**:\n> {it['excerpt']}\n")
            
    _add_section("Claims", claims)
    _add_section("Decisions", decisions)
    _add_section("Sources", sources)
    _add_section("Contradictions / Radar Pings", radars)
    _add_section("Graph Context", graph)
    if others:
        _add_section("Other Evidence", others)
        
    bundle.append("## Warnings")
    for w in manifest.get("warnings", []):
        bundle.append(f"- {w}")
        
    bundle.append("\n## Manifest Summary")
    bundle.append(f"Pack ID: `{manifest['pack_id']}`")
    bundle.append(f"Generator Version: {manifest.get('generator_version', 'unknown')}")
    bundle.append(f"Item Hashes: {len(manifest.get('content_hashes', []))}")
    
    return "\n".join(bundle)

def export_evidence_pack(pack_id: str, fmt: str = "markdown", output_dir: str = None) -> str:
    pack_data = inspect_evidence_pack(pack_id)
    if not pack_data:
        raise ValueError(f"Pack {pack_id} not found.")
        
    if output_dir:
        out_dir = Path(output_dir)
        # Rudimentary safety check
        if ".git" in out_dir.resolve().parts or "zurvan" in out_dir.resolve().name.lower():
            print("WARNING: You are exporting an evidence pack inside a repository.")
    else:
        out_dir = Path(pack_data["path"])
        
    out_dir.mkdir(parents=True, exist_ok=True)
    
    if fmt == "markdown":
        out_file = out_dir / f"{pack_id}.md"
        content = _format_markdown(pack_data)
        out_file.write_text(content, encoding="utf-8")
        return out_file.as_posix()
    elif fmt == "json":
        out_file = out_dir / f"{pack_id}.json"
        content = json.dumps(pack_data, indent=2)
        out_file.write_text(content, encoding="utf-8")
        return out_file.as_posix()
    else:
        raise ValueError(f"Unsupported format: {fmt}")
        
def redact_existing_pack(pack_id: str) -> bool:
    pack_data = inspect_evidence_pack(pack_id)
    if not pack_data:
        return False
        
    items = pack_data["items"]
    manifest = pack_data["manifest"]
    
    if manifest.get("redaction_status") == "redacted":
        return True
        
    items = redact_evidence_pack_items(items)
    manifest["redaction_status"] = "redacted"
    
    pack_dir = Path(pack_data["path"])
    (pack_dir / "items.json").write_text(json.dumps(items, indent=2), encoding="utf-8")
    (pack_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    
    return True
