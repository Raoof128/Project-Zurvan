from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class ReportSummary(BaseModel):
    report_id: str
    topic: str
    template: str
    created_at: str
    source_pack_id: str

class EvidenceSummary(BaseModel):
    pack_id: str
    topic: str
    created_at: str
    redaction_status: str
    item_count: int
