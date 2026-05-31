# Federation Privacy Model

Zurvan Federation operates under strict local-first privacy rules.

## 1. No Data Bleed
Files are never copied between projects. When you run `context-all`, the generated markdown bundle is assembled in memory and printed to stdout. It is up to you (or your LLM client) what to do with that bundle.

## 2. No Absolute Path Leakage
By default, commands like `federation stats` and `search-all` hide absolute paths (using `~` shortcuts or relative paths). This is to ensure that if you copy-paste terminal output into an LLM or issue tracker, your private directory structure isn't leaked.
Use `--verbose` to see full absolute paths.

## 3. Local Registry Only
The registry of projects is kept in `~/.zurvan/projects.json`. It is never committed to the public Zurvan repository.

## 4. No Cloud
Federation does not use cloud sync or remote databases. All cross-project searches are done locally by querying each project's SQLite databases sequentially.
