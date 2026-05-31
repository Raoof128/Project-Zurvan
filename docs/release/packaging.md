# Zurvan Packaging Guide

Zurvan is designed to be easily portable between environments without complex setup.

## Philosophy

Zurvan avoids large external databases (like Postgres, Redis, or dedicated vector DBs). Instead, all data is stored either as raw Markdown (`wiki/`, `docs/`) or local SQLite databases (`data/`).

This means packaging and moving Zurvan is as simple as copying files.

## Dist Directory

The `dist/` directory holds all artifacts for backup or release. Snapshots and safety backups are created here automatically.
