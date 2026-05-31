# Review Workbench Hardening

Phase 16 adds rigorous safety checks to the Review Workbench:

1. **Secret Scanning**: Reports are automatically scanned for email addresses, access tokens, and API keys.
2. **Absolute Path Blocking**: Audit will flag any exported content containing absolute paths (`/Users/`, `/home/`, `C:\`).
3. **Citation Validation**: Checks ensure every single claim maps directly to a verified evidence piece in the local vault.
4. **Local Index**: An isolated registry of reports and evidence packs is stored in `~/.zurvan/review-index.json`.
