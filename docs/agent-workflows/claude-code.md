# Claude Code Workflow

When using Claude Code with Zurvan, follow this workflow for best results:

## 1. Start a Session
Tell Claude Code to start a session before you ask it to code:
```bash
claude "Start a Zurvan session for 'Refactoring the Auth module'"
```
Claude should use the MCP tool (if exposed) or run the CLI command:
```bash
python scripts/cli.py session start --topic "Refactoring the Auth module"
```

## 2. Preflight Context
Claude Code can pull context automatically using MCP, but you can force a preflight:
```bash
python scripts/cli.py agent preflight --topic "Authentication"
```
This gives Claude the decisions, logs, and open questions related to Auth.

## 3. Post-Edit Memory
When Claude is done, ask it to record what it did:
```bash
python scripts/cli.py agent postedit --summary "Updated OAuth flows" --files src/auth.py --checks "pytest"
```

## 4. Close Session
Close the session when the task is complete:
```bash
python scripts/cli.py session close --topic "Refactoring the Auth module" --summary "Finished." --checks "pytest"
```
