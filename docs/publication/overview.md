# Publication Export Overview

Phase 17 adds the ability to export and bundle local Zurvan reports for external sharing.

The process:
1. Review report locally.
2. Run `zurvan publish validate` to ensure no secrets or absolute paths are leaked.
3. Run `zurvan publish export` to generate a safe Markdown, JSON, or HTML file.
4. Run `zurvan publish bundle` to zip all components alongside the citation appendix.

By default, all publications are strictly written to `~/.zurvan/publications/` to ensure no data is leaked back into the public source tree.
