# Policy Radar Overview

Zurvan's Policy Radar is a local-only tool designed to analyze policies, decisions, claims, and rules across your federated workspaces. It uses heuristic analysis to detect patterns without relying on LLMs or cloud endpoints.

## Features

- **Policy Discovery**: Finds explicit policies and rules embedded in `AGENTS.md`, `README.md`, `docs/`, and `wiki/`.
- **Contradiction Detection**: Surfaces possible conflict candidates where projects or individual files express opposing rules.
- **Drift Analysis**: Highlights where one project enforces a rule that another project is missing.

## Privacy

The radar operates completely locally. It respects the workspace registry and never copies raw files between projects. Reports generated locally use temporary or local caches and are not committed to public repositories.
