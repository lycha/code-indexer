# User Testing

Testing surface, required testing skills/tools, resource cost classification per surface.

---

## Validation Surface

**Primary surface:** CLI commands executed via terminal (subprocess)
- `index init`, `index build`, `index enrich`, `index query`, `index status`, `index reset`
- All commands testable via `Execute` tool with exit code + stdout/stderr capture
- SQLite queries via `sqlite3` CLI for database state verification

**Tools:**
- Execute tool for CLI invocation
- sqlite3 CLI for DB inspection
- File system checks for .codeindex/ directory, .gitignore, lock files

**Limitations:**
- `index enrich` real API calls require ANTHROPIC_API_KEY — test --dry-run mode instead
- TTY-dependent behavior (reset interactive prompt, text output format) may require special handling

## Validation Concurrency

**Surface: CLI (Execute tool)**
- Each validator runs CLI commands sequentially within its flow
- CLI tool is lightweight (no servers, no ports)
- Resource per validator: ~50MB Python process + temporary SQLite DB
- Machine: 48GB RAM, 12 cores, ~6GB baseline usage
- Usable headroom: 42GB * 0.7 = ~29GB
- 5 validators * 50MB = 250MB — well within budget
- **Max concurrent validators: 5**

## Flow Validator Guidance: CLI

- Use isolated temporary working directories and DB paths per validator to avoid cross-test interference.
- Do not reuse `.codeindex/` directories across validators.
- Set `PATH="/Users/kjackowski/.factory/bin:$PATH"` for flows that require ripgrep.
- Keep all command progress/warnings verification scoped to each validator’s own stdout/stderr captures.
