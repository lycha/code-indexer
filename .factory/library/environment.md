# Environment

Environment variables, external dependencies, and setup notes.

**What belongs here:** Required env vars, external API keys/services, dependency quirks, platform-specific notes.
**What does NOT belong here:** Service ports/commands (use `.factory/services.yaml`).

---

## Python Environment
- Python 3.13.9 at `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`
- pip 24.3.1 available
- pytest 9.0.1 available globally
- click 8.3.1 available globally
- sqlite3 3.50.4 bundled with Python

## External Tools
- ripgrep 13.0.0 at `/Users/kjackowski/.factory/bin/rg`
- tree-sitter: NOT installed globally — will be installed via pyproject.toml in T3b
- anthropic SDK: NOT installed globally — will be installed via pyproject.toml in T6

## Environment Variables
- `ANTHROPIC_API_KEY` — required for `index enrich` (not for build/query)
- `CODEINDEX_DB` — optional, overrides default DB path

## Platform
- macOS (darwin 24.6.0)
- 48 GB RAM, 12 CPU cores
- No Docker required
