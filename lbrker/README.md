# lbrker

Find and clean AI-style hard line breaks in documentation files.

AI-assisted writing often wraps every clause or sentence onto its own line. `lbrker` lists all doc files with such breaks, lets you choose which to fix, backs each up to `<file>.bak`, then joins the wrapped lines into paragraphs.

## Usage

```sh
python3 lbrker.py                  # scan current dir, mode: both
python3 lbrker.py -d docs          # scan a subdir
python3 lbrker.py -m clauses       # only join clause wraps, keep sentence-per-line
python3 lbrker.py -f a.md b.txt    # check only specific files; replaces the crawl
python3 lbrker.py -f 'file4*'      # globs allowed
python3 lbrker.py -st              # internal checks
```

Long forms also work: `--dir`, `--mode`, `--file`, `--selftest`.

Flow: files are listed with their break counts → toggle which to clean (`3`, `2-4`, `all`, `none`, `done`, `q`) → each selected file is moved to `<file>.bak` → lines are joined with live progress → per-file summary.

## What it cleans

Within a paragraph block (consecutive non-blank lines), every wrapped line break is joined in `both` mode; in `clauses` mode only breaks where the line does not end in `.`, `!` or `?`.

Never touched: headings, list items, blockquotes, tables, horizontal rules, fenced/indented code, markdown hard breaks (lines ending in 2+ spaces or `\`), and `.bak` files themselves.

## Scope

**Included**

- `*.md`, `*.mdx`, `*.rst`, `*.txt`
- Extensionless files with known doc names, case-insensitive:
  `LICENSE`, `COPYING`, `NOTICE`, `README`, `CHANGELOG`, `CHANGES`, `AUTHORS`, `CONTRIBUTORS`, `INSTALL`, `SECURITY`, `CODE_OF_CONDUCT`, `CONTRIBUTING`, `VERSION`, `MANIFEST`
- `-f` accepts specific files or globs (`-f file1.md 'file4*'`); non-doc files
  in the match are ignored, never touched

**Excluded**

- `*.bak` files (and existing backups are never overwritten)
- Binary / non-UTF-8 files
- `.git`, `.hg`, `.svn`, `node_modules`, virtualenvs
- `.gitignore`d files (component-wise fnmatch; negation `!` not supported)

`-f` replaces the directory crawl entirely.

## Known limits

- Wrapped continuation lines inside list items / blockquotes are not joined.
- `.gitignore` negation is ignored.
- Backups are never overwritten; delete old `.bak` files manually.

Restore a file with: `mv file.bak file`