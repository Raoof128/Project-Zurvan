# Federation Workflows

## Gathering Context for an Agent

When you are starting a new coding task that touches concepts from multiple projects, you can use `context-all` to generate a federated context bundle.

```bash
# Gather context from all projects, expanding the knowledge graph up to depth 1
zurvan project context-all --topic "authentication flow" --hybrid --graph > auth_context.md
```

You can then provide `auth_context.md` to Claude Code or Cursor as dense, cross-project memory.

## Checking Federation Health

Run the doctor command periodically to ensure all your registered vaults have valid search and graph indexes.

```bash
zurvan project federation doctor
```

If a project is missing an index, switch to it and rebuild:
```bash
zurvan project use my-vault
zurvan index search
zurvan graph rebuild
```
