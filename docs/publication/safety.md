# Publication Safety Model

Before any file is written:
1. Paths matching root folders (`/Users/`, `/home/`) are strictly blocked.
2. Token-like string entropy is blocked to prevent accidental secret leakage.
3. Raw file paths (`raw/`) are stripped or blocked.
4. Emails are blocked unless `--allow-emails` is flagged.

Publications are always generated inside `~/.zurvan/publications/` protecting the active working repository from cross-contamination.
