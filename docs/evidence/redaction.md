# Redaction in Evidence Packs

Zurvan strictly guards privacy when sharing knowledge. All Evidence Packs run through a redaction engine by default.

## What is Redacted?

1. **Absolute Paths**: E.g., `/Users/name/projects/` or `C:\Users\` are replaced with `[REDACTED_PATH]`.
2. **Emails**: Replaced with `[REDACTED_EMAIL]`.
3. **API Keys & Tokens**: Common high-entropy string patterns and AWS keys are replaced with `[REDACTED_TOKEN]`.
4. **Phone Numbers**: Replaced with `[REDACTED_PHONE]`.

## Disabling Redaction

If you are generating a pack strictly for internal personal use, you can bypass redaction:

```bash
zurvan evidence build --topic "internal review" --no-redact
```
