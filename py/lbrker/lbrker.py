#!/usr/bin/env python3
"""lbrker — list and clean AI-style hard line breaks in documentation files.

Pass 1 lists every doc file that has joinable line breaks. You toggle which
to clean, each selected file is backed up to <file>.bak, then the wrapped
lines are joined. Clean files don't show up on later runs.

Usage:
  lbrker.py                  # scan CWD, default mode
  lbrker.py -d docs          # scan a subdir
  lbrker.py -m clauses       # keep sentence-per-line style (only join clause wraps)
  lbrker.py -f a.md b.txt    # check only specific files (globs allowed, e.g. 'file4*')
  lbrker.py -st              # run internal checks

Long flags also work: --dir, --mode, --file, --selftest.
"""

import argparse
import fnmatch
import glob
import os
import re
import sys

__version__ = "0.1.0"

TEXT_EXTS = {".md", ".mdx", ".rst", ".txt"}
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv"}

# Extensionless doc files ("LICENSE", "COPYING", ...), matched case-insensitively.
NO_EXT_DOC_NAMES = {
    "license", "copying", "notice", "readme", "changelog", "changes",
    "authors", "contributors", "install", "security", "code_of_conduct",
    "contributing", "version", "manifest",
}


def is_doc_file(name):
    if name.endswith(".bak"):
        return False
    ext = os.path.splitext(name)[1].lower()
    base = os.path.splitext(name)[0].lower()
    return ext in TEXT_EXTS or base in NO_EXT_DOC_NAMES

FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
HEADING_RE = re.compile(r"^#{1,6}\s")
LIST_RE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|>\s?|\|)")
HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
INDENT_RE = re.compile(r"^(?: {4}|\t)")
TERMINAL_RE = re.compile(r"[.!?]\s*$")


def is_structural(line):
    """Markdown lines that must never be joined into their neighbors."""
    return bool(FENCE_RE.match(line) or HEADING_RE.match(line)
                or LIST_RE.match(line) or HR_RE.match(line) or INDENT_RE.match(line))


def _hard_end(s):
    """Line ends with a markdown hard break (2+ spaces or backslash)."""
    if re.search(r" {2,}$", s):
        return True
    return s.rstrip().endswith("\\")


def _joinable_break(a, mode):
    if _hard_end(a):
        return False
    return mode == "both" or not TERMINAL_RE.search(a.rstrip())


def _join_block(block, mode):
    """Join a plain-paragraph block. Returns (joined_text, breaks_removed)."""
    joins = 0
    for k in range(len(block) - 1):
        if _joinable_break(block[k], mode):
            joins += 1
    if joins == 0:
        return None, 0
    text = block[0]
    for k in range(1, len(block)):
        if _joinable_break(block[k - 1], mode):
            text = text.rstrip() + " " + block[k].strip()
        else:
            text += "\n" + block[k].strip()
    return text, joins


def analyze(text, mode):
    """Return (breaks, fixed_text_or_None). fixed is None when nothing changes."""
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    out = []
    block = []
    breaks = 0
    changed = False
    in_fence = False

    def flush():
        nonlocal breaks, changed
        if not block:
            return
        t, j = _join_block(block, mode)
        if t is None:
            out.extend(block)
        else:
            out.append(t)
            breaks += j
            changed = True
        block.clear()

    for line in lines:
        if in_fence:
            out.append(line)
            if FENCE_RE.match(line):
                in_fence = False
            continue
        if not line.strip():
            flush()
            out.append(line)
        elif FENCE_RE.match(line):
            flush()
            out.append(line)
            in_fence = True
        elif is_structural(line):
            flush()
            out.append(line)
        else:
            block.append(line)
    flush()

    if not changed:
        return 0, None
    return breaks, nl.join(out)


# --- crawling --------------------------------------------------------------

def load_gitignore(root):
    """Approximation of .gitignore: fnmatch against path components. No negation."""
    rules = []
    ipath = os.path.join(root, ".gitignore")
    if os.path.isfile(ipath):
        try:
            with open(ipath, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith("!"):
                        rules.append(line)
        except OSError:
            pass
    return rules


def ignored(relpath, rules):
    parts = relpath.split("/")
    for rule in rules:
        base = rule.rstrip("/")
        if any(fnmatch.fnmatch(p, base) for p in parts):
            return True
        if "/" in rule and fnmatch.fnmatch(relpath, rule):
            return True
    return False


def scan_docs(root):
    rules = load_gitignore(root)
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not is_doc_file(name):
                continue
            fp = os.path.join(dirpath, name)
            rel = os.path.relpath(fp, root)
            if ignored(rel, rules):
                continue
            files.append((rel, fp))
    files.sort()
    return files


def read_text(fp):
    try:
        with open(fp, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    if b"\0" in raw:  # binary
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


# --- interactive -----------------------------------------------------------

def expand_files(patterns):
    """Expand -f args (globs allowed); non-matching patterns warn and are dropped.
    Only doc files (ext or allowlist name) are kept — explicit paths never touch
    code or other non-doc files."""
    files = []
    for pat in patterns:
        matches = glob.glob(pat) if glob.has_magic(pat) else ([pat] if os.path.exists(pat) else [])
        if not matches:
            print(f"  ? no files match: {pat}")
            continue
        for m in sorted(matches):
            if os.path.isfile(m) and is_doc_file(os.path.basename(m)):
                files.append(os.path.normpath(m))
    seen, out = set(), []
    for f in files:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out

def show_list(info, selected):
    print(f"{len(info)} files with line breaks found:")
    for i, (rel, _fp, breaks, _text) in enumerate(info, 1):
        mark = "x" if selected[i - 1] else " "
        print(f"  [{mark}] {i:<3} {rel:<40} {breaks} breaks")
    print("\nToggle: numbers/ranges (3, 2-4), all, none, done/empty, q")


def apply_toggle(entry, selected):
    entry = entry.strip().lower()
    if entry in ("q", "quit"):
        return "quit"
    if entry in ("done", ""):
        return "done"
    if entry == "all":
        selected[:] = [True] * len(selected)
        return "ok"
    if entry == "none":
        selected[:] = [False] * len(selected)
        return "ok"
    for token in re.split(r"[\s,]+", entry):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not m:
            print(f"  ? unknown input: {token}")
            continue
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if a < 1 or b > len(selected) or a > b:
            print(f"  ? out of range: {token}")
            continue
        for idx in range(a, b + 1):
            selected[idx - 1] = not selected[idx - 1]
    return "ok"


def write_atomic(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


# --- selftest --------------------------------------------------------------

def selftest():
    def eq(desc, got, want):
        if got != want:
            raise AssertionError(f"{desc}: got {got!r}, want {want!r}")
        print(f"  ok {desc}")

    t = "This is a thought that continues\nonto the next line without pause.\n"
    eq("both: join wrap", analyze(t, "both"), (1, "This is a thought that continues onto the next line without pause.\n"))

    t = "First sentence here.\nSecond sentence here.\n"
    eq("clauses: keep sentence-per-line", analyze(t, "clauses"), (0, None))
    eq("both: join sentences too", analyze(t, "both"), (1, "First sentence here. Second sentence here.\n"))

    t = "Prose before.\n```\ncode line one\ncode line two\n```\nProse after\nstill prose.\n"
    _, fixed = analyze(t, "both")
    eq("both: fence protected", fixed,
       "Prose before.\n```\ncode line one\ncode line two\n```\nProse after still prose.\n")

    t = "a  \nb\nc\n"
    eq("both: 2-space hard break kept", analyze(t, "both"), (1, "a  \nb c\n"))

    t = "# Title\n- item one\n- item two\nplain start\nplain end\n"
    eq("both: structure untouched", analyze(t, "both"), (1, "# Title\n- item one\n- item two\nplain start plain end\n"))

    t = "no newline at end"
    eq("both: missing trailing NL preserved", analyze(t, "both"), (0, None))

    t = "first line\r\nsecond line\r\n"
    eq("both: CRLF preserved", analyze(t, "both"), (1, "first line second line\r\n"))

    eq("structural heading", is_structural("# Hi"), True)
    eq("structural bullet", is_structural("- item"), True)
    eq("structural number", is_structural("2. item"), True)
    eq("structural code indent", is_structural("    code"), True)
    eq("emphasis not structural", is_structural("*emphasized* text"), False)

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        sub = os.path.join(td, "docs")
        os.makedirs(sub)
        open(os.path.join(td, "README.md"), "w").write("one\ntwo\nthree\n")
        open(os.path.join(td, "notes.txt"), "w").write("plain single line\n")
        open(os.path.join(td, "bin.dat"), "w").write("not\0doc\n")
        open(os.path.join(td, "LICENSE"), "w").write("MIT license\nsecond line\n")
        open(os.path.join(td, "script"), "w").write("#!/bin/sh\necho hi\n")
        os.makedirs(os.path.join(td, "node_modules"))
        open(os.path.join(td, "node_modules", "lib.md"), "w").write("a\nb\n")
        open(os.path.join(td, "skip.bak"), "w").write("a\nb\n")
        found = scan_docs(td)
        eq("scan: finds docs + LICENSE, skips junk/script/bak",
           [p for p, _ in found], ["LICENSE", "README.md", "notes.txt"])
        open(os.path.join(sub, "guide.md"), "w").write("x\ny\n")
        found = scan_docs(td)
        eq("scan: nested doc", [p for p, _ in found],
           ["LICENSE", "README.md", "docs/guide.md", "notes.txt"])
        files = expand_files([os.path.join(td, "*.md"), os.path.join(td, "LICENSE")])
        eq("expand: globs + explicit, script excluded", files,
           [os.path.join(td, "README.md"), os.path.join(td, "LICENSE")])
        eq("expand: unmatched glob returns empty",
           expand_files([os.path.join(td, "nope*.md")]), [])

    a = build_parser().parse_args(["-f", "a.md", "b.txt"])
    eq("parser: -f nargs", a.file, ["a.md", "b.txt"])
    eq("parser: -d", build_parser().parse_args(["-d", "x"]).dir, "x")
    eq("parser: -m", build_parser().parse_args(["-m", "clauses"]).mode, "clauses")
    eq("parser: -st", build_parser().parse_args(["-st"]).selftest, True)
    print("selftest: all checks passed")
    return 0


# --- main -------------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(prog="lbrker", description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version=f"lbrker {__version__}")
    ap.add_argument("-d", "--dir", default=".", help="directory to scan (default: .)")
    ap.add_argument("-m", "--mode", choices=["both", "clauses"], default="both",
                    help="both: join any wrapped paragraph (default); clauses: only join lines not ending in .!?")
    ap.add_argument("-f", "--file", nargs="+", metavar="FILE",
                    help="check only these specific files (glob patterns allowed, e.g. 'file4*'); replaces the directory crawl")
    ap.add_argument("-st", "--selftest", action="store_true", help="run internal checks and exit")
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()

    print(f"lbrker v{__version__}")

    info = []
    if args.file:
        for p in expand_files(args.file):
            text = read_text(p)
            if text is None:
                continue
            breaks, _ = analyze(text, args.mode)
            if breaks:
                info.append([p, p, breaks, text])
    else:
        root = args.dir
        for rel, fp in scan_docs(root):
            text = read_text(fp)
            if text is None:
                continue
            breaks, _ = analyze(text, args.mode)
            if breaks:
                info.append([rel, fp, breaks, text])

    if not info:
        print("No doc files with line breaks found.")
        return 0

    selected = [True] * len(info)
    show_list(info, selected)
    while True:
        try:
            entry = input("toggle [done]: ")
        except EOFError:
            break
        act = apply_toggle(entry, selected)
        if act == "quit":
            print("Quit, nothing changed.")
            return 0
        if act == "done":
            break
        show_list(info, selected)

    chosen = [i for i, s in enumerate(selected) if s]
    if not chosen:
        print("No files selected. Nothing to do.")
        return 0

    total = sum(info[i][2] for i in chosen)
    tty = sys.stdout.isatty()
    done_breaks = 0
    results = []
    lines_out = 0  # lines printed below the header (tracked so the header can be refreshed in place)
    print(f"Cleaning: 0/{total} breaks ...", flush=True)
    for pos, i in enumerate(chosen, 1):
        rel, fp, breaks, original = info[i]
        bak = fp + ".bak"
        if os.path.exists(bak):
            print(f"  ! {rel}: existing backup kept (not overwritten)")
            lines_out += 1
        else:
            os.rename(fp, bak)
        _, fixed = analyze(original, args.mode)
        try:
            write_atomic(fp, fixed)
        except OSError as e:
            print(f"  ! {rel}: write failed ({e}); original kept as {os.path.basename(bak)}")
            lines_out += 1
            continue
        done_breaks += breaks
        results.append((rel, breaks))
        if tty:
            # refresh the header on its own line, then append the file line below it
            print(f"\x1b[{lines_out + 1}A\x1b[2K\rCleaning: {done_breaks}/{total} breaks ...",
                  end="", flush=True)
            print(f"\x1b[{lines_out + 1}B\r[{pos}/{len(chosen)}] {rel}", flush=True)
        else:
            print(f"[{pos}/{len(chosen)}] {rel}", flush=True)
        lines_out += 1

    print(f"\nDone: {len(results)} files cleaned, {done_breaks} breaks cleared.")
    for rel, n in results:
        print(f"  {rel}: {n} breaks cleared")
    print("Backups left as <file>.bak next to each cleaned file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())