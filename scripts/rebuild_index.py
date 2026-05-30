import os

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
    
    for cat in categories.keys():
        cat_dir = os.path.join(wiki_dir, cat)
        if os.path.exists(cat_dir):
            for file in os.listdir(cat_dir):
                if file.endswith(".md"):
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
