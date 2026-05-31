# Review Workbench Safety Model

1. **Local Only**: The UI uses FastAPI/Jinja and binds to localhost.
2. **No Traversal**: Pack IDs and Report IDs are validated as slugs. Path traversal (`../`) is blocked.
3. **No Raw Access**: The server strictly reads from `~/.zurvan/reports/` and `~/.zurvan/evidence-packs/`.
4. **No LLMs**: The UI is deterministic and read-only.
