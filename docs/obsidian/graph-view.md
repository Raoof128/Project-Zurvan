# Obsidian Graph View

Zurvan's knowledge graph (built using `scripts/graph_build.py`) is compatible with Obsidian's native Graph View.

## Recommended Graph Settings

To make the graph more readable:

### Groups (Color Coding)
- `path:wiki/decisions/` ➔ Color: Orange (Decisions)
- `path:wiki/claims/` ➔ Color: Blue (Claims)
- `path:wiki/concepts/` ➔ Color: Purple (Concepts)
- `path:wiki/sessions/` ➔ Color: Green (Sessions)
- `path:wiki/contradictions/` ➔ Color: Red (Contradictions)
- `path:wiki/entities/` ➔ Color: Gold (Entities — Phase 18)
- `path:wiki/syntheses/` ➔ Color: Teal (Syntheses — Phase 18, written via `--save`)

### Display
- **Arrows**: ✅ Enabled (Shows direction of the relationship)
- **Text Fade Threshold**: Adjust so labels don't clutter the view.

*Note: Zurvan's internal graph parser (`graph_build.py`) understands standard Obsidian wikilinks (`[[link]]`), so the two graphs will mirror each other perfectly.*
