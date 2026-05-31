# Snapshots

You can capture a full, point-in-time state of the Zurvan knowledge graph using the `snapshot` command.

## Create a Snapshot
```bash
zurvan snapshot create
```

This will bundle `wiki/`, `docs/`, `eval/`, the core `.md` files, and the SQLite databases into a `tar.gz` archive in `dist/snapshots/`. It also generates a `manifest.json` with SHA-256 hashes for all included files.

## Excluding Raw Data
By default, the `raw/` directory is **excluded** from snapshots. This prevents accidental leakage of private, sensitive, or massive original documents. If you explicitly want to include it, use:
```bash
zurvan snapshot create --include-raw
```

## List Snapshots
To see all available snapshots:
```bash
zurvan snapshot list
```
