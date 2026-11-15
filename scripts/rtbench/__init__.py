"""RTBench: a system-neutral record-time benchmark (IOU-Z2-5).

Spec: docs/superpowers/specs/2026-11-15-z2-record-time-benchmark-design.md
Everything here but `zurvan_adapter.py` uses the standard library only.
"""

GENERATOR_VERSION = "rtbench-1"
# Every published generator, by version number. rtbench-1 is frozen (its dev split
# is registered by digest); rtbench-2 adds erasure of already-dead history.
GENERATOR_VERSIONS = {1: "rtbench-1", 2: "rtbench-2"}
