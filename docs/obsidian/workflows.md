# Obsidian Workflows

Here is how Obsidian integrates with Zurvan's agent workflows.

## The Session Workflow

1. Start a session from the CLI:
   ```bash
   zurvan session start --topic "Refactor authentication"
   ```
2. Open Obsidian. You will see a new file in `wiki/sessions/`.
3. Use this file as your daily scratchpad. Add wikilinks `[[like this]]` to link to decisions, claims, and open questions.
4. When finished, run the CLI tool to close it:
   ```bash
   zurvan session close --topic "Refactor authentication" --summary "Done" --checks "make test"
   ```

## Creating Nodes (Claims, Decisions, etc.)

1. In Obsidian, create a new note in the appropriate folder (e.g., `wiki/decisions/`).
2. Insert the corresponding template from `wiki/templates/`.
3. Fill in the YAML frontmatter and the content.
4. Add wikilinks to connect it to the rest of the graph.
5. The next time `zurvan graph rebuild` runs, your new node and edges will be ingested into the SQLite graph.

## Reviewing Agent Work

When an AI agent (like Claude Code or Cursor) completes a task, it logs its work using `zurvan agent postedit`.
1. Open `wiki/log.md` in Obsidian.
2. Review the structured post-edit logs directly in the editor.
