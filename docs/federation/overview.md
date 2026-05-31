# Federation Overview

Zurvan Federation (Phase 10) allows you to search and build context across multiple isolated local knowledge vaults.

Instead of putting all your notes into a single massive graph, you can keep projects separated (e.g. `work`, `personal`, `research`) and run cross-project queries when you need insights that span across them.

## Core Commands
- `zurvan project search-all <query>`: Searches all registered projects.
- `zurvan project context-all --topic <query>`: Gathers deep context and graph neighbours from all projects.
- `zurvan project federation stats`: View health metrics of your federation.
- `zurvan project federation doctor`: Check all registered projects for missing indexes or corruption.

## Safety First
Federation commands are strictly **read-only**. They do not copy files between projects, they do not mutate data, and they respect the local registry path isolation.
