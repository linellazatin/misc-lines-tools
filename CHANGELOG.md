# Changelog

## 2025-09-17

- **lbrker**: short flags (`-d`, `-m`, `-f`, `-st`), `-f` checks
  specific files/globs (replaces crawl, doc files only), extensionless docs with known names (`LICENSE`, `COPYING`, ...) included in the crawl.
- **lbrker**: initial tool. Scans doc files (`*.md`, `*.mdx`, `*.rst`,
  `*.txt`) for AI-style hard line breaks, interactive toggle selection, `.bak` backup, join cleanup with progress, per-file summary. Modes: `both` (default) and `clauses`. Honors `.gitignore` (no negation).