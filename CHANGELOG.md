# Changelog

## 0.1.0 - 2026-09-18

### orustrker v0.1.0

**New**
- OpenRouter usage tracker: snapshot + live-refreshing watch, `--tui` menu,
  model browser (paginated 9/page, per-provider pricing). Stdlib-only, session-only.
- Tier-1 key fields: day/week/month usage, BYOK ledger, expiry, regions,
  free-model daily counter.
- `__version__` constant, `--version` flag.

**Fixed**
- BYOK keys under-reported spend; remaining now trusts `limit_remaining`.
- Deprecated `rate_limit` no longer shown as raw `-1`.

**Docs**
- Tool README; root table gains Version column.

**Tests**
- 96 `-st` self-checks; TUI and Ctrl-C paths verified over a live pty.

### lbrker v0.1.0

**New**
- Finds and cleans AI-style hard line breaks in doc files; interactive
  selection, `.bak` backups, gitignore-aware.
- `__version__` constant, `--version` flag, version in output header.

**Docs**
- Tool README (usage, scope, known limits).

## 2025-09-17

- **lbrker**: short flags (`-d`, `-m`, `-f`, `-st`), `-f` checks
  specific files/globs (replaces crawl, doc files only), extensionless docs with known names (`LICENSE`, `COPYING`, ...) included in the crawl.
- **lbrker**: initial tool. Scans doc files (`*.md`, `*.mdx`, `*.rst`,
  `*.txt`) for AI-style hard line breaks, interactive toggle selection, `.bak` backup, join cleanup with progress, per-file summary. Modes: `both` (default) and `clauses`. Honors `.gitignore` (no negation).