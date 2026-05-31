# Radar Workflows

Use the Policy Radar during project reviews or when bootstrapping a new workspace to ensure safety guidelines are applied consistently.

## Recommended Workflow

1. Scan for all policies:
   ```bash
   zurvan project radar scan
   ```
2. Check for missing policies:
   ```bash
   zurvan project radar drift
   ```
3. Look for explicit contradictions:
   ```bash
   zurvan project radar contradictions
   ```
4. Generate a full report for your logs:
   ```bash
   zurvan project radar report --format markdown
   ```
   The report will be saved to your local `~/.zurvan/reports/` directory.
