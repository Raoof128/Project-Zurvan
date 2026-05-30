import os
import re

def audit_frontmatter(content, path):
    if not content.startswith("---"):
        return f"Missing YAML frontmatter in {path}"
    return None

def audit_uncited_claims(content, path):
    if "claims/" in path:
        if "cited from" not in content.lower() and "missing?" not in content.lower():
             # Basic heuristic for stub
             if not re.search(r'\[.*?\]\(.*?\)', content):
                 return f"Potentially uncited claim in {path} (say 'evidence is missing' if true)"
    return None

def audit_duplicate_titles(wiki_dir):
    titles = {}
    errors = []
    for root, _, files in os.walk(wiki_dir):
        for file in files:
            if file.endswith(".md"):
                path = os.path.join(root, file)
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    title_match = re.search(r'^title:\s*(.+)$', content, re.MULTILINE)
                    if title_match:
                        title = title_match.group(1).strip()
                        if title in titles:
                            errors.append(f"Duplicate title '{title}' in {path} and {titles[title]}")
                        else:
                            titles[title] = path
    return errors

def audit_orphan_pages(wiki_dir):
    # A simple graph builder to find unlinked pages
    links = set()
    all_pages = set()
    for root, _, files in os.walk(wiki_dir):
        for file in files:
            if file.endswith(".md"):
                path = os.path.join(root, file)
                rel_path = os.path.relpath(path, wiki_dir)
                all_pages.add(rel_path)
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # Find markdown links: [text](link)
                    for match in re.finditer(r'\[.*?\]\((.*?)\)', content):
                        link = match.group(1)
                        if not link.startswith("http"):
                            # Resolve relative to current file
                            target = os.path.normpath(os.path.join(os.path.dirname(rel_path), link))
                            links.add(target)
    
    errors = []
    for page in all_pages:
        if page not in links and page not in ['index.md', 'log.md', 'overview.md', 'open-questions.md']:
            errors.append(f"Orphan page found: {page}")
    return errors

def main():
    print("Auditing wiki...")
    wiki_dir = "wiki"
    errors = []
    
    errors.extend(audit_duplicate_titles(wiki_dir))
    errors.extend(audit_orphan_pages(wiki_dir))
    
    for root, _, files in os.walk(wiki_dir):
        for file in files:
            if file.endswith(".md"):
                path = os.path.join(root, file)
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                    err = audit_frontmatter(content, path)
                    if err: errors.append(err)
                    
                    err = audit_uncited_claims(content, path)
                    if err: errors.append(err)
                    
    if errors:
        print(f"Found {len(errors)} issues:")
        for err in errors:
            print(f" - {err}")
    else:
        print("Audit passed cleanly!")

if __name__ == "__main__":
    main()
