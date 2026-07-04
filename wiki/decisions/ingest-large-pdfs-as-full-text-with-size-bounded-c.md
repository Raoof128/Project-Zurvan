---
title: "Ingest large PDFs as full-text with size-bounded chunks; never mock-extract"
type: decision
status: "accepted"
tags:
  - "ingestion"
  - "chunking"
  - "provenance"
---

# Ingest large PDFs as full-text with size-bounded chunks; never mock-extract

## Reason
create_source_page truncated to 1000 chars and chunk.py split only on markdown headings, so a heading-less PDF became one 191k-char chunk the embedder truncates to ~256 tokens — stored but unsearchable. Fixed both (full text + MAX_CHUNK_CHARS=1000 sub-splitting, legacy chunk_id preserved for small sections). LLM extraction was deliberately NOT run under the mock provider: it would fabricate claims/concepts citing the source.

## Status
accepted
