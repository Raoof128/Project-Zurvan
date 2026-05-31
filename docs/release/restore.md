# Restore Snapshot

Restoring a snapshot overwrites your current knowledge graph. For safety, Zurvan will **never** restore without explicit confirmation and always takes a pre-restore backup.

## Restore Process

```bash
zurvan snapshot restore <snapshot_name> --force
```

### Safety Guarantees

1. **Explicit Confirmation**: Omitting `--force` will cause the command to abort.
2. **Safety Backup**: Before extracting the snapshot, Zurvan creates a backup of your current `wiki/` and `data/` in `dist/backups/`.
3. **No Raw Writes**: The restore process actively blocks any files attempting to extract into the `raw/` directory.
4. **Path Traversal Protection**: Any paths attempting to write outside the Zurvan root (e.g., `../../etc/passwd`) are caught and blocked immediately.
