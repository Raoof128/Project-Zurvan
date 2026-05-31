# Stale Decisions

You can use Zurvan to find old or unresolved decisions across your federation.

```bash
zurvan project decisions-stale --days 90
```

## Rules
A decision is flagged as stale if:
- Its `status` is `pending` or `proposed` and it is older than 30 days.
- It has had no updates for more than the threshold (default 90 days), regardless of status (unless it is already `rejected`, `deprecated`, or `superseded`).
- It has missing date information.
