# Cross-Project Decision Memory

Zurvan allows you to scan, group, and query decision records across multiple registered local projects. 

## Discovery

Decisions are automatically discovered if they meet either condition:
- Located inside `wiki/decisions/`
- Contain frontmatter `type: decision`

## Commands

List all decisions across federated projects:
```bash
zurvan project decisions-all
```

Find similar decisions across projects (e.g., when trying to reuse architecture choices):
```bash
zurvan project decisions-similar "use SQLite for knowledge engine"
```

Find possible cross-project contradictions:
```bash
zurvan project decisions-conflicts
```

Find stale decisions (pending for a long time):
```bash
zurvan project decisions-stale --days 90
```

## Privacy Model
- Decision memory scans only run locally.
- Full text of decisions is kept out of the central SQLite cache by default.
- Absolute paths are hidden by default to prevent leakage when sharing terminal output.
- `raw/`, `dist/`, and `.git/` directories are always excluded.
