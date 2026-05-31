import json
from datetime import datetime, timezone
import hashlib

def create_manifest(pack_id: str, topic: str, projects: list[str], options: dict, items: list[dict], files_created: list[str]) -> dict:
    content_hashes = []
    for it in items:
        if "content_hash" in it:
            content_hashes.append(it["content_hash"])
        elif "excerpt" in it and it["excerpt"]:
            content_hashes.append(hashlib.sha256(it["excerpt"].encode("utf-8")).hexdigest())
            
    manifest = {
        "pack_id": pack_id,
        "topic": topic,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "projects_scanned": projects,
        "command_options": options,
        "item_count": len(items),
        "files_created": files_created,
        "content_hashes": content_hashes,
        "redaction_status": "redacted" if options.get("redact", True) else "unredacted",
        "generator_version": "0.4.0",
        "warnings": [
            "This evidence pack contains heuristic outputs and localized excerpts.",
            "Do not commit evidence packs to public repositories."
        ]
    }
    return manifest
