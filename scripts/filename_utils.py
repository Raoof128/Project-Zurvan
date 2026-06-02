def sanitize_filename(name: str) -> str:
    """Canonical filename sanitiser. Keep alphanumerics, hyphens, underscores; replace all else with _."""
    return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in name)
