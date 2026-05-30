import os
import argparse
import subprocess

def run_gauntlet(sources):
    results = []
    
    for source in sources:
        print(f"\n{'='*50}\nTesting: {source}\n{'='*50}")
        
        # 1. Ingest
        print("[1] Ingesting...")
        ingest_res = subprocess.run(["python", "scripts/ingest.py", source], capture_output=True, text=True)
        if ingest_res.returncode != 0:
            print(f"Ingestion failed for {source}:\n{ingest_res.stderr}")
            results.append({"source": source, "status": "Failed Ingestion"})
            continue
            
        # 2. Extract
        basename = os.path.basename(source)
        source_md_name = f"{basename}.md"
        wiki_source_path = os.path.join("wiki", "sources", source_md_name)
        
        print(f"[2] Extracting from {wiki_source_path}...")
        extract_res = subprocess.run(["python", "scripts/extract.py", "--source", wiki_source_path], capture_output=True, text=True)
        if extract_res.returncode != 0:
            print(f"Extraction failed for {source}:\n{extract_res.stderr}")
            results.append({"source": source, "status": "Failed Extraction/Validation"})
            continue
            
        # 3. Audit
        print("[3] Auditing Wiki...")
        audit_res = subprocess.run(["python", "scripts/audit_wiki.py"], capture_output=True, text=True)
        if "Found" in audit_res.stdout and "issues:" in audit_res.stdout:
            # We don't fail the gauntlet script entirely on audit issues, but we log them
            print(f"Audit found issues after processing {source}:\n{audit_res.stdout}")
            results.append({"source": source, "status": "Audit Issues Found"})
        else:
            print(f"Audit passed cleanly for {source}.")
            results.append({"source": source, "status": "Passed"})
            
    # Write summary
    print(f"\n{'='*50}\nGauntlet Summary\n{'='*50}")
    for res in results:
        print(f"{res['source']}: {res['status']}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Extraction Reliability Gauntlet")
    parser.add_argument("sources", nargs="+", help="Paths to raw sources to test")
    args = parser.parse_args()
    
    # Ensure sources are in raw/
    for src in args.sources:
        if not os.path.abspath(src).startswith(os.path.abspath("raw")):
            print(f"Error: {src} must be located in the raw/ directory to protect data.")
            exit(1)
            
    run_gauntlet(args.sources)
