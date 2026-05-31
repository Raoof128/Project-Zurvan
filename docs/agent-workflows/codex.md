# Codex-Style Agent Workflow

For automated agents running in a loop (like a custom Devin/Codex clone):

## System Prompt Addition
Add this to your agent's system prompt:
> Before editing code, run `python scripts/cli.py agent preflight --topic <task>`.
> After editing code, run `python scripts/cli.py agent postedit ...`.
> Wrap large tasks in `python scripts/cli.py session start` and `close`.

## The Loop
1. Agent receives task: "Fix the bug in the graph parser"
2. Agent runs: `python scripts/cli.py session start --topic "Bugfix graph parser"`
3. Agent runs: `python scripts/cli.py agent preflight --topic "graph parser"`
4. Agent reads the output, edits the code.
5. Agent runs tests.
6. Agent runs: `python scripts/cli.py agent postedit --summary "Fixed edge regex" --files scripts/graph_build.py --checks "pytest"`
7. Agent runs: `python scripts/cli.py session close --topic "Bugfix graph parser" --summary "Success" --checks "pytest"`
