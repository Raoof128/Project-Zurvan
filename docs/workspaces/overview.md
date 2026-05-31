# Workspace Overview

Zurvan supports managing multiple projects (vaults) from a single installation. 
Instead of maintaining multiple copies of the Zurvan codebase, you can install Zurvan globally or in a single directory, and use it to interact with multiple independent knowledge bases.

## Key Concepts

- **Project (Vault)**: A directory containing a Zurvan knowledge base (needs `AGENTS.md`, `README.md`, `wiki/`, `docs/`, `scripts/`).
- **Registry**: The central list of projects managed by your Zurvan installation. Stored in `~/.zurvan/projects.json`.
- **Current Project**: The project that Zurvan commands will target by default if no `--project` is specified.

## Privacy & Safety

To protect your private knowledge base structure and absolute paths, the registry is intentionally kept out of the main repository. 
When committing to public repositories, ensure you do not track your real `~/.zurvan/projects.json` or any local databases.
