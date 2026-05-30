# Setup Guide

This guide covers how to set up the Zurvan Local-first LLM Wiki Knowledge Engine on your local machine.

## Prerequisites

- Python 3.10+
- `pip` package manager
- (Optional) `sentence-transformers` for local semantic embeddings (requires PyTorch)

## Installation

1. **Clone the repository** (if you haven't already):
   ```bash
   git clone https://github.com/Raoof128/Project-Zurvan.git
   cd Project-Zurvan
   ```

2. **Set up a virtual environment** (recommended):
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```

3. **Install core dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Install development and testing dependencies**:
   If you plan to run the test suite or contribute:
   ```bash
   pip install -r requirements-dev.txt
   ```

5. **(Optional) Install Local Embeddings**:
   To use actual local embeddings instead of the mock testing embeddings, install `sentence-transformers`:
   ```bash
   pip install sentence-transformers
   ```

## Initializing the Wiki

Zurvan does not require complex database initialisation because it uses local SQLite databases (`data/registry.sqlite`, `data/search.sqlite`, `data/graph.sqlite`) which are automatically created when needed.

To verify your installation, you can run the master quality gate script:
```bash
bash scripts/check.sh
```
If it prints `🎉 All Zurvan checks passed successfully.`, you are ready to proceed!

## Environment Variables
Before running ingestion, you must configure your LLM provider. See [ENVIRONMENT.md](ENVIRONMENT.md) for details on available environment variables.
