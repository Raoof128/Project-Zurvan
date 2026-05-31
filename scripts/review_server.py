import os
import uvicorn
import webbrowser
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pathlib import Path

def create_app() -> FastAPI:
    app = FastAPI(title="Zurvan Local Review Workbench", version="0.1.0")
    
    # Mount static files
    root_dir = Path(__file__).parent.parent
    static_dir = root_dir / "static"
    static_dir.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    
    # Setup templates
    templates_dir = root_dir / "templates"
    templates_dir.mkdir(exist_ok=True)
    templates = Jinja2Templates(directory=str(templates_dir))
    
    # Include routes
    from scripts.review_routes import setup_routes
    setup_routes(app, templates)
    
    return app

def run_server(host: str = "127.0.0.1", port: int = 8765, allow_lan: bool = False, open_browser: bool = False):
    if host == "0.0.0.0" and not allow_lan:
        print("Error: Binding to 0.0.0.0 is blocked by default. Use --allow-lan to override.")
        import sys
        sys.exit(1)
        
    app = create_app()
    
    url = f"http://{host}:{port}"
    print(f"Starting Zurvan Local Review Workbench at {url}")
    print("WARNING: This server provides read-only access to local reports and evidence packs.")
    
    if open_browser:
        webbrowser.open(url)
        
    uvicorn.run(app, host=host, port=port, log_level="info")

if __name__ == "__main__":
    run_server()
