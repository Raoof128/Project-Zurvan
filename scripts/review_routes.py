from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import json
import os
from pathlib import Path
from scripts.review_safety import get_safe_evidence_path, get_safe_report_path, get_base_dir
from scripts.report_compose import list_reports, inspect_report
from scripts.evidence_pack import inspect_evidence_pack, list_evidence_packs

# We will initialize this router and templates in review_server
from fastapi import APIRouter

router = APIRouter()

# Global template reference set by server setup
templates = None

def setup_routes(app: FastAPI, tmpl: Jinja2Templates):
    global templates
    templates = tmpl
    app.include_router(router)

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    from scripts.review_index import get_index
    idx = get_index()
    return templates.TemplateResponse(request, "index.html", {"index": idx})

@router.get("/evidence", response_class=HTMLResponse)
async def list_evidence(request: Request):
    packs = list_evidence_packs()
    return templates.TemplateResponse(request, "evidence_pack.html", {"packs": packs, "selected": None})

@router.get("/evidence/{pack_id}", response_class=HTMLResponse)
async def view_evidence(request: Request, pack_id: str):
    packs = list_evidence_packs()
    pack_data = inspect_evidence_pack(pack_id)
    if not pack_data:
        raise HTTPException(status_code=404, detail="Pack not found")
    
    # Redact absolute paths for display just in case
    for item in pack_data.get("items", []):
        if "source_path" in item:
            item["source_path"] = os.path.basename(item["source_path"])
            
    return templates.TemplateResponse(request, "evidence_pack.html", {
        "packs": packs,
        "selected": pack_data
    })

@router.get("/reports", response_class=HTMLResponse)
async def view_reports(request: Request):
    reports = list_reports()
    return templates.TemplateResponse(request, "report.html", {"reports": reports, "selected": None})

@router.get("/reports/{report_id}", response_class=HTMLResponse)
async def view_report_detail(request: Request, report_id: str):
    reports = list_reports()
    report = inspect_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
        
    from scripts.review_audit import audit_report
    audit = audit_report(report_id)
    
    return templates.TemplateResponse(request, "report.html", {
        "reports": reports,
        "selected": report,
        "audit": audit
    })

@router.get("/reports/{report_id}/citations", response_class=HTMLResponse)
async def view_citations(request: Request, report_id: str):
    report = inspect_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
        
    return templates.TemplateResponse(request, "citation_check.html", {
        "report": report
    })

@router.get("/reports/{report_id}/warnings", response_class=HTMLResponse)
async def view_warnings(request: Request, report_id: str):
    report = inspect_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
        
    # We can validate it on the fly to show empty section warnings
    from scripts.report_compose import validate_report
    val = validate_report(report_id)
    
    return templates.TemplateResponse(request, "warnings.html", {
        "report": report,
        "validation": val
    })

@router.get("/reports/{report_id}/export")
async def export_report_endpoint(report_id: str, format: str = "markdown"):
    from scripts.report_export import export_report
    try:
        # We export to a temp location inside the report dir, or just read the content
        path = export_report(report_id, fmt=format)
        if format == "json":
            return FileResponse(path, media_type="application/json", filename=f"{report_id}.json")
        else:
            return FileResponse(path, media_type="text/markdown", filename=f"{report_id}.md")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/audit", response_class=HTMLResponse)
async def view_audit_all(request: Request):
    from scripts.review_audit import audit_all_reports
    audits = audit_all_reports()
    return templates.TemplateResponse(request, "audit_all.html", {"audits": audits})

@router.get("/audit/{report_id}", response_class=HTMLResponse)
async def view_audit(request: Request, report_id: str):
    from scripts.review_audit import audit_report
    audit = audit_report(report_id)
    return templates.TemplateResponse(request, "audit.html", {"audit": audit, "report_id": report_id})

@router.get("/reports/{report_id}/checklist", response_class=HTMLResponse)
async def view_checklist(request: Request, report_id: str):
    report = inspect_report(report_id)
    from scripts.review_audit import audit_report
    audit = audit_report(report_id)
    return templates.TemplateResponse(request, "checklist.html", {"report": report, "audit": audit})

@router.get("/index/rebuild", response_class=JSONResponse)
async def rebuild_index_route():
    from scripts.review_index import rebuild_index
    idx = rebuild_index()
    return JSONResponse(idx)

