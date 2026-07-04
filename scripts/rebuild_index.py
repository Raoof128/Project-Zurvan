import os
import subprocess


def _gitignored(rel_paths, cwd):
    """Return the subset of rel_paths that git ignores. Used so the nav index
    never enumerates ingested/private pages (wiki/sources, AutoConcept-*,
    Claim-*, note-*), which are gitignored — leaking their *filenames* into a
    tracked index.md is exactly how private document titles reached the public
    remote. Fails open to an empty set (lists nothing extra) if git is absent."""
    if not rel_paths:
        return set()
    try:
        res = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            input="\n".join(rel_paths),
            capture_output=True, text=True, cwd=cwd,
        )
        return set(res.stdout.splitlines())
    except Exception:
        return set()


def rebuild_index(wiki_dir="wiki"):
    index_path = os.path.join(wiki_dir, "index.md")

    categories = {
        "sources": [],
        "concepts": [],
        "entities": [],
        "claims": [],
        "contradictions": [],
        "experiments": [],
        "decisions": []
    }

    # Collect candidate pages, then drop any that git ignores (private/ingested)
    # so the index only ever references trackable pages.
    candidates = []  # (cat, file, rel_path)
    for cat in categories.keys():
        cat_dir = os.path.join(wiki_dir, cat)
        if os.path.exists(cat_dir):
            for file in os.listdir(cat_dir):
                if file.endswith(".md"):
                    candidates.append((cat, file, os.path.join(wiki_dir, cat, file)))

    repo_root = os.path.dirname(os.path.abspath(wiki_dir))
    ignored = _gitignored([c[2] for c in candidates], repo_root)
    for cat, file, rel in candidates:
        if rel not in ignored:
            categories[cat].append(file)
                    
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write("# Wiki Index\n\nWelcome to the local LLM Wiki.\n\n## Navigation\n- [Overview](overview.md)\n- [Open Questions](open-questions.md)\n- [Log](log.md)\n\n")
        for cat, files in categories.items():
            if files:
                f.write(f"## {cat.capitalize()}\n")
                for file in sorted(files):
                    f.write(f"- [{file}]({cat}/{file})\n")
                f.write("\n")
                
    print(f"Rebuilt {index_path} successfully.")

if __name__ == "__main__":
    rebuild_index()
