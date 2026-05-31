# Obsidian Setup for Zurvan

Zurvan is designed to be fully compatible with Obsidian without requiring it. Obsidian simply acts as a powerful glass cockpit for Zurvan's underlying engine.

## Option 1: The Developer Vault (Recommended)

This gives you full visibility of the source code and the knowledge base.

1. Open Obsidian.
2. Click **Open folder as vault**.
3. Select the root `Zurvan/` directory.

### Hiding Noisy Folders
To keep your vault clean, go to **Settings > Files & Links > Excluded files** and add:
```text
data/
scripts/
tests/
__pycache__/
.venv/
.git/
raw/
```

## Option 2: The Clean Knowledge Vault

If you only want to see the knowledge base without the code:

1. Open Obsidian.
2. Click **Open folder as vault**.
3. Select the `Zurvan/wiki/` directory.

*Note: Links pointing to the repo root (like `README.md`) will not resolve in this mode.*

## Recommended Settings
Go to **Settings > Files & Links**:
- **Use Wikilinks**: ✅ Enabled
- **Automatically update internal links**: ✅ Enabled
- **Default location for new notes**: `wiki/` (if using Option 1) or Vault folder (if using Option 2).
- **New link format**: Relative path to file
