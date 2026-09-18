# misc-tools

A monorepo of small command-line tools.

## Tools

| Tool | Version | What it does | Language |
|------|---------|--------------|----------|
| [lbrker](py/lbrker/README.md) | v0.1.0 | Finds and cleans AI-style hard line breaks in doc files | Python |
| [orustrker](py/orustrker/README.md) | v0.1.0 | Tracks OpenRouter API key usage (session-only) | Python |

Versions track each tool's `__version__` constant (the single source of truth in a plain script, no packaging); bump both together when releasing.