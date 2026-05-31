# Cursor Workflow

When using Cursor's Composer or Chat:

## Getting Context
In the chat, you can say:
> `@zurvan agent preflight --topic "UI layout"`

Cursor will use the MCP server to fetch the bundle, or you can run the CLI command in Cursor's terminal and reference the output.

## Recording Work
After Cursor has generated and applied code across multiple files, you should record the session manually in the terminal:
```bash
python scripts/cli.py agent postedit --summary "Built the new UI layout" --files src/App.tsx src/Layout.tsx --checks "npm run lint"
```
