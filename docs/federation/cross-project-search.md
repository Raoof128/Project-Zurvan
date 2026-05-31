# Cross-Project Search

Cross-project search allows you to query multiple Zurvan projects simultaneously. 

## Usage

```bash
# Search all projects for a keyword
zurvan project search-all "security"

# Search all projects using hybrid search (semantic + keyword)
zurvan project search-all "agent memory" --hybrid

# Search specific projects only
zurvan project search-all "roadmap" --projects work-project side-hustle
```

## Results Format
Results are ranked collectively across all searched projects and labelled clearly with the project name. Source paths are kept relative to their respective project roots to maintain path privacy.

## Strict Mode
By default, if a registered project is missing its search index or has been deleted from your disk, `search-all` will skip it and print a warning. 
If you want the command to fail entirely when a project is unhealthy, use `--strict`.
