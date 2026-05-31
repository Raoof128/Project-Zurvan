# Evidence Pack Workflows

Evidence packs are the predecessor to structured reports. 

## Audit Workflow

1. **Build the Pack**: Gather context about security or architecture.
   ```bash
   zurvan evidence build --topic "security architecture" --graph --include-policy-radar
   ```
2. **Review Manifest**:
   ```bash
   zurvan evidence inspect pack-12345
   ```
3. **Export and Share**: Export the redacted pack for distribution.
   ```bash
   zurvan evidence export pack-12345 --format markdown --output-dir ~/Desktop/
   ```
