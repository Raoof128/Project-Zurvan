# Contradiction Radar

The Contradiction Radar looks for heuristic signs of conflicting information. It is important to note that this tool raises **candidates** for review; it does not determine absolute truth.

## How it Works

1. **Policy Clashes**: It checks if two files claim opposite stances on a known policy category (e.g., one says "use cloud" while another says "no cloud apis").
2. **Self-Contradiction**: It identifies if a single file contains both positive and negative keywords for a rule.
3. **Status Conflicts**: For claims and decisions, it flags items with high semantic similarity but opposing statuses (e.g., one accepted, one rejected).

## Commands

```bash
zurvan project radar contradictions
```
