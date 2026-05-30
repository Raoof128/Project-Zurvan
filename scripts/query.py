import os
import argparse

def keyword_search(query, wiki_dir="wiki"):
    results = []
    for root, _, files in os.walk(wiki_dir):
        for file in files:
            if file.endswith(".md"):
                path = os.path.join(root, file)
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if query.lower() in content.lower():
                        results.append(path)
    return results

def vector_search(query):
    # Placeholder for future vector DB implementation
    raise NotImplementedError("Vector search is not yet implemented.")

def graph_search(query):
    # Placeholder for future graph DB implementation
    raise NotImplementedError("Graph search is not yet implemented.")

def main():
    parser = argparse.ArgumentParser(description="Query the LLM Wiki")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--mode", choices=["keyword", "vector", "graph"], default="keyword", help="Search mode")
    
    args = parser.parse_args()
    
    if args.mode == "keyword":
        print(f"Searching for '{args.query}'...")
        results = keyword_search(args.query)
        if results:
            print("Found matches in:")
            for r in results:
                print(f" - {r}")
        else:
            print("No matches found.")
    else:
        print(f"Mode {args.mode} not supported yet.")

if __name__ == "__main__":
    main()
