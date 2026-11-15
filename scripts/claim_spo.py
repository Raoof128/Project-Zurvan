"""Subject / predicate / object for claims (IOU-Z2-1; design 2026-11-15-z2-iou-1-4 §3).

- subject: an entity id, `ent:<slug>`, where slug is the entity page's filename stem
  (`sanitize_filename(name)`, the rule `wiki_merge` names entity pages by);
- predicate: one of the closed vocabulary in `claim_predicates.json`;
- object: exactly one of {"entity": <entity id>}, {"value": <text>, "unit"?: <text>},
  {"date": "YYYY-MM-DD"}, stored as canonical JSON.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from functools import lru_cache
from pathlib import Path

from scripts.filename_utils import sanitize_filename

_VOCAB = Path(__file__).with_name("claim_predicates.json")


@lru_cache(maxsize=1)
def predicates() -> dict:
    return dict(json.loads(_VOCAB.read_text(encoding="utf-8"))["predicates"])


def entity_id(name) -> str:
    """`ent:<slug>` from a name or an existing id."""
    raw = str(name or "").strip()
    slug = raw[len("ent:"):] if raw.startswith("ent:") else sanitize_filename(raw)
    # The entity page stem keeps any Unicode letter or digit (`sanitize_filename`),
    # so "Müller AG" and Persian names are entities too (final review I3).
    if not slug or not all(c.isalnum() or c in "_-" for c in slug) or not slug.strip("_-"):
        raise ValueError(f"subject/entity must name an entity, got {name!r}")
    return f"ent:{slug}"


def _object(obj) -> dict:
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except ValueError:
            raise ValueError(f"object must be JSON, got {obj!r}") from None
    if not isinstance(obj, dict):
        raise ValueError("object must be a JSON object")
    kinds = [k for k in ("entity", "value", "date") if k in obj]
    if len(kinds) != 1:
        raise ValueError("object must have exactly one of entity, value, date")
    extra = set(obj) - {kinds[0]} - ({"unit"} if kinds[0] == "value" else set())
    if extra:
        raise ValueError(f"object has unexpected keys {sorted(extra)}")
    kind = kinds[0]
    if kind == "entity":
        return {"entity": entity_id(obj["entity"])}
    if kind == "date":
        try:
            date.fromisoformat(str(obj["date"]))
        except ValueError:
            raise ValueError(f"object date must be YYYY-MM-DD, got {obj['date']!r}") from None
        if len(str(obj["date"])) != 10:
            raise ValueError(f"object date must be YYYY-MM-DD, got {obj['date']!r}")
        return {"date": str(obj["date"])}
    value = str(obj["value"]).strip()
    if not value:
        raise ValueError("object value is empty")
    out = {"value": value}
    if obj.get("unit") is not None:
        unit = str(obj["unit"]).strip()
        if unit:
            out["unit"] = unit
    return out


def normalise(subject, predicate, obj) -> tuple:
    """(subject_id, predicate_id, object_json); ValueError on anything invalid."""
    if predicate not in predicates():
        raise ValueError(f"predicate {predicate!r} is not in the vocabulary "
                         f"(scripts/claim_predicates.json); use related_to when nothing fits")
    return (entity_id(subject), predicate,
            json.dumps(_object(obj), sort_keys=True, separators=(",", ":")))


def from_extraction(claim: dict):
    """A model-emitted `spo` as a (subject, predicate, object) triple, or None.
    An invalid triple is dropped with a warning; the claim itself is kept."""
    raw = claim.get("spo")
    if not raw:
        return None
    try:
        triple = (raw.get("subject"), raw.get("predicate"), raw.get("object"))
        normalise(*triple)
        return triple
    except (ValueError, AttributeError, TypeError) as exc:
        print(f"Warning: dropped the S-P-O of claim {claim.get('claim_id', '?')}: {exc}", file=sys.stderr)
        return None
