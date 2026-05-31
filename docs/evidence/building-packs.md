# Building Evidence Packs

Building an evidence pack aggregates cross-project context using hybrid search, graph traversal, and policy radars.

## Basic Command

```bash
zurvan evidence build --topic "MCP security"
```

## Advanced Command

Combine multiple context streams into one robust pack:

```bash
zurvan evidence build \
  --topic "public repo safety" \
  --hybrid \
  --graph \
  --include-decisions \
  --include-policy-radar
```

## Exporting

Packs are created internally first. To use them, export them to Markdown or JSON:

```bash
zurvan evidence export <pack-id> --format markdown
```
