#!/usr/bin/env python3
"""orustrker — OpenRouter USage TRacKER.

Session-only usage reporting for an OpenRouter API key. Nothing is written
to disk; the API key is never logged, only masked for display.

Usage:
  orustrker.py              # snapshot: fetch + display usage once
  orustrker.py -w 30        # watch: live-refreshing screen, session deltas
  orustrker.py --tui        # interactive menu: browser / snapshot / watch
  orustrker.py -st          # run internal self-checks
"""

import argparse
import getpass
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

API_URL = "https://openrouter.ai/api/v1/auth/key"
API_BASE = "https://openrouter.ai/api/v1"
BULLET = "\u2022"  # U+2022
__version__ = "0.1.0"

# alternate-screen / cursor control (raw ANSI, btop's technique; stdlib only)
TUI_IN = "\033[?1049h\033[?25l"
TUI_OUT = "\033[?25h\033[?1049l"
TUI_HOME = "\033[H\033[2J"


def use_tui() -> bool:
    return sys.stdout.isatty()


def use_color() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


# ---------- pure display helpers (no I/O) ----------

def _c(text, code, color: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if color else text


def mask_key(key: str) -> str:
    """Mask key for display: first 8 chars, bullets, last 4."""
    if len(key) >= 12:
        return key[:8] + BULLET * 16 + key[-4:]
    return BULLET * 8


def fmt_usd(amount: float) -> str:
    return f"${amount:.4f}"


def fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}m {s % 60:02d}s"


def pretty_date(iso) -> str:
    """ISO 8601 timestamp -> 'YYYY-MM-DD' in UTC, or '' if absent."""
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return ""


def progress(used: float, limit: float | None, width: int = 30) -> str:
    """Progress bar scaled to limit. 'no limit' shows an unstuffed bar."""
    if limit is None or limit <= 0:
        return f"[{'\u2591' * width}] no limit set"
    frac = min(used / limit, 1.0)
    pct = used / limit * 100
    filled = int(width * frac)
    return (f"[{'\u2588' * filled}{'\u2591' * (width - filled)}] "
            f"{pct:.1f}%")


def derive(data: dict) -> dict:
    """Normalize the /auth/key payload into a flat display-ready dict."""
    info = data.get("data", {})
    usage = info.get("usage") or 0.0
    byok = info.get("byok_usage") or 0.0
    spend = usage + byok  # total this key has consumed, BYOK-aware
    limit = info.get("limit")
    lr = info.get("limit_remaining")
    if lr is not None:
        remaining = lr
    elif limit is not None:
        remaining = limit - spend
    else:
        remaining = None
    regions = info.get("allowed_data_regions") or []
    return {
        "is_free_tier": bool(info.get("is_free_tier")),
        "tier_text": "free" if info.get("is_free_tier") else "paid",
        "is_management_key": bool(info.get("is_management_key")),
        "regions": regions,
        "usage": usage,
        "usage_daily": info.get("usage_daily") or 0.0,
        "usage_weekly": info.get("usage_weekly") or 0.0,
        "usage_monthly": info.get("usage_monthly") or 0.0,
        "byok_usage": byok,
        "include_byok_in_limit": bool(info.get("include_byok_in_limit")),
        "spend": spend,
        "limit": limit,
        "limit_reset": info.get("limit_reset"),
        "remaining": remaining,
        "expires": pretty_date(info.get("expires_at")),
        "free_reqs": info.get("free_model_daily_requests"),
        "rate_limit": info.get("rate_limit") or {},
        "progress_bar": progress(spend, limit),
        "percent": (spend / limit * 100) if limit else None,
    }


def rate_text(rate_limit: dict) -> str:
    """'200 req / 10s', 'unlimited req / 10s', or '' if absent."""
    if not rate_limit:
        return ""
    req = rate_limit.get("requests")
    if req == -1:
        req = "unlimited"
    return f"{req} req / {rate_limit.get('interval', '?')}"


def fmt_per_m(price) -> str:
    """Per-token price string -> $/1M tokens ('$0.15', '$0.016', '$0')."""
    try:
        m = float(price) * 1_000_000
    except (TypeError, ValueError):
        return "\u2014"
    if m == 0:
        return "$0"
    return f"${m:,.2f}" if m >= 1 else f"${m:.4g}"


def fmt_ctx(n) -> str:
    """1000000 -> '1M', 256000 -> '256K'."""
    if not n:
        return "\u2014"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1000:
        return f"{n // 1000}K"
    return str(n)


def search_models(models: list, query: str) -> list:
    """Rank models: exact id > id prefix > id substring > name substring.
    Empty query returns all sorted by id."""
    q = (query or "").strip().lower()
    if not q:
        return sorted(models, key=lambda m: m.get("id", ""))
    scored = []
    for m in models:
        mid = (m.get("id") or "").lower()
        nm = (m.get("name") or "").lower()
        if mid == q:
            s = 0
        elif mid.startswith(q):
            s = 1
        elif q in mid:
            s = 2
        elif q in nm:
            s = 3
        else:
            continue
        scored.append((s, mid, m))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in scored]


def parse_author_slug(model_id: str) -> tuple:
    """'~qwen/x:free' -> ('qwen','x'); safe for URL paths."""
    mid = model_id.split(":", 1)[0].lstrip("~")
    author, _, slug = mid.partition("/")
    return author, slug


# ---------- screen renderer (pure: given now_ts, deterministic) ----------

def _col(label, value, label2=None, value2=None):
    base = f"  {label:<11}{value}"
    if label2 is None:
        return base
    pad = max(1, 30 - len(base))
    return base + " " * pad + f"{label2:<10}{value2}"


def _rule(name="", color=False):
    text = "  " + ("─ " + name + " " if name else "─" * 56)
    return _c(text.ljust(58, "─"), "2", color)


def build_screen(api_key: str, d: dict | None, now_ts: float,
                 session: dict | None = None, color: bool = False) -> str:
    """Render the full status screen as one string (no trailing newline)."""
    lines = []
    title = f"orustrker v{__version__} · OpenRouter Usage Tracker"
    lines.append(_c(" " + title + ("   [live]" if session else ""), "1;36", color))
    lines.append(_rule(color=color))

    if d is None:
        lines.append("  waiting for first successful poll…")
        lines.append(_rule(color=color))
        return "\n".join(lines)

    lines.append(_col("key", mask_key(api_key)))
    tier = d["tier_text"]
    if d["is_management_key"]:
        tier += " · mgmt"
    lines.append(_col("tier", tier, "expires", d["expires"] or "never"))
    if d["regions"]:
        lines.append(_col("regions", ", ".join(d["regions"])))

    lines.append(_rule("usage", color))
    lines.append(_col("total", fmt_usd(d["usage"]), "day", fmt_usd(d["usage_daily"])))
    lines.append(_col("week", fmt_usd(d["usage_weekly"]), "month", fmt_usd(d["usage_monthly"])))
    if d["byok_usage"]:
        note = " (counts toward limit)" if d["include_byok_in_limit"] else ""
        lines.append(_col("byok", fmt_usd(d["byok_usage"]) + note))
    lim = fmt_usd(d["limit"]) if d["limit"] is not None else "not set"
    lines.append(_col("limit", lim, "reset", d["limit_reset"] or "—"))
    rem = fmt_usd(d["remaining"]) if d["remaining"] is not None else "—"
    lines.append(_col("remaining", rem))
    bar = d["progress_bar"]
    if d["percent"] is not None:
        code = "32" if d["percent"] < 50 else "33" if d["percent"] < 80 else "31"
        barside = d["progress_bar"].rpartition("] ")[0]
        bar = barside + "] " + _c(f"{d['percent']:.1f}% used", code, color)
    lines.append(f"  {'progress':<11}{bar}")

    fr = d["free_reqs"]
    if fr and fr.get("limit"):
        lines.append(_rule("free models", color))
        lines.append(_col("requests",
                          f"{fr.get('used', 0)} / {fr['limit']} today",
                          "left", str(fr.get("remaining", "?"))))

    if session is not None:
        lines.append(_rule("session", color))
        delta = (session["spend"] - session["baseline"]) \
            if session.get("baseline") is not None and session.get("spend") is not None else 0.0
        lines.append(_col("session Δ", f"${delta:+.4f}",
                          "key total", fmt_usd(session["spend"]) if session.get("spend") is not None else "—"))
        lines.append(_col("started", datetime.fromtimestamp(session["start_ts"]).strftime("%H:%M:%S"),
                          "elapsed", fmt_elapsed(now_ts - session["start_ts"])))
        lines.append(_col("updated", datetime.fromtimestamp(now_ts).strftime("%H:%M:%S"),
                          "status", _c(session["status"], "31" if "fail" in session["status"] else "32", color)))
    else:
        checked = datetime.fromtimestamp(now_ts).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(_col("checked", checked))

    lines.append(_rule(color=color))
    foot = "Ctrl-C quits · all state in memory · nothing written to disk" \
        if session else "single poll · nothing stored · session-only"
    lines.append(_c("  " + foot, "2", color))
    return "\n".join(lines)


# ---------- network ----------

class ApiError(Exception):
    """User-presentable fetch failure."""


def _http_open(req: urllib.request.Request) -> bytes:
    return urllib.request.urlopen(req, timeout=10).read()


def fetch_key_info(api_key: str) -> dict:
    """GET /auth/key; returns parsed JSON or raises ApiError."""
    return _get(API_URL, api_key)


def _get(url: str, api_key: str) -> dict:
    """Authenticated GET -> parsed JSON, or ApiError with a clear message."""
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        body = _http_open(req)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ApiError("bad or revoked API key (HTTP 401)") from e
        if e.code == 429:
            raise ApiError("rate limited (HTTP 429)") from e
        raise ApiError(f"unexpected HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise ApiError(f"network error: {e.reason}") from e
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ApiError(f"unparseable response: {e}") from e


def fetch_models_user(api_key: str) -> list:
    """Models this key can actually serve (account provider settings apply)."""
    return _get(API_BASE + "/models/user", api_key).get("data", [])


def fetch_model_endpoints(api_key: str, model_id: str) -> list:
    """Per-provider endpoint records for one model."""
    author, slug = parse_author_slug(model_id)
    data = _get(f"{API_BASE}/models/{author}/{slug}/endpoints", api_key)
    return data.get("data", {}).get("endpoints", [])


# ---------- key + modes ----------

def get_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        key = getpass.getpass("OpenRouter API key: ")
    key = key.strip()
    if not key:
        raise ApiError("no API key provided")
    return key


def read_key(prompt: str = "") -> str:
    """Single keypress, instant (cbreak keeps Ctrl-C live). Non-tty fallback:
    type + Enter, first char used. Returns a lowercase char."""
    if sys.stdin.isatty():
        try:
            import termios
            import tty
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                if prompt:
                    sys.stdout.write(prompt)
                    sys.stdout.flush()
                tty.setcbreak(fd)
                ch = sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            return ch.lower()
        except (ImportError, OSError, ValueError):
            pass  # e.g. Windows: fall back to line input
    try:
        return (input(prompt)[:1] or "q").lower()
    except EOFError:
        return "q"


def read_line(prompt: str = "") -> str:
    try:
        return input(prompt)
    except EOFError:
        return ""


def bar_width() -> int:
    cols = shutil.get_terminal_size(fallback=(60, 20)).columns
    return max(20, min(40, cols - 22))


def run_snapshot(api_key: str, color: bool) -> int:
    data = fetch_key_info(api_key)
    d = derive(data)
    d["progress_bar"] = progress(d["spend"], d["limit"], bar_width())
    print(build_screen(api_key, d, time.time(), session=None, color=color))
    return 0


def run_watch(api_key: str, interval: int, tui: bool, color: bool,
              owns_alt: bool = True) -> tuple:
    """Live watch; returns (exit_code, one_line_summary).
    When owns_alt is False the caller already holds the alternate screen
    (menu mode); this just redraws and returns on Ctrl-C."""
    start = time.time()
    baseline = None
    last_spend = None
    d = None
    polls = 0
    consec_fail = 0
    status = "ok"
    out = sys.stdout.write

    def render():
        if not tui:
            return
        out(TUI_HOME + build_screen(
            api_key, d, time.time(),
            session={"baseline": baseline, "spend": last_spend,
                     "start_ts": start, "status": status}, color=color)
            + "\n")
        sys.stdout.flush()

    if tui and owns_alt:
        out(TUI_IN)
    try:
        while True:
            try:
                d = derive(fetch_key_info(api_key))
                d["progress_bar"] = progress(d["spend"], d["limit"], bar_width())
                polls += 1
                last_spend = d["spend"]
                if baseline is None:
                    baseline = last_spend
                status = "ok"
                consec_fail = 0
            except ApiError as e:
                consec_fail += 1
                status = f"fetch failed ({consec_fail}/5): {e}"
                if not tui:
                    print(f"  warn: {status}", file=sys.stderr)
                if consec_fail >= 5:
                    if tui and owns_alt:
                        out(TUI_OUT)
                    if tui:
                        out(TUI_HOME + "  orustrker: 5 consecutive failures, giving up\n")
                        sys.stdout.flush()
                    else:
                        print(f"orustrker: error: {status}", file=sys.stderr)
                    return 1, f"gave up: {status}"
            if not tui:  # plain log fallback when stdout is not a tty
                if d is not None:
                    when = datetime.fromtimestamp(time.time()).strftime("%H:%M:%S")
                    dl = f" Δ ${last_spend - baseline:+.4f}" if baseline is not None else ""
                    print(f"  [{when}] spend ${last_spend:.4f}{dl}  [{status}]")
            render()
            time.sleep(interval)
    except KeyboardInterrupt:
        if tui and owns_alt:
            out(TUI_OUT)
        el = time.time() - start
        acc = (last_spend - baseline) if (last_spend is not None and baseline is not None) else 0.0
        summary = f"session: {fmt_elapsed(el)}, polls: {polls}, usage accrued: ${acc:.4f}"
        if owns_alt:
            print(f"\n  {summary}")
        return 0, summary
    finally:
        if tui and owns_alt:
            out(TUI_OUT)


# ---------- menu + model browser ----------

RESULT_CAP = 9


def clamp_page(page, total, size):
    """(page, pages) with page clamped into range."""
    pages = max(1, (total + size - 1) // size)
    return max(0, min(page, pages - 1)), pages


def render_menu(api_key, kd, model_count, last_action, color) -> str:
    lines = [_c(f" orustrker v{__version__} · OpenRouter Usage Tracker",
                "1;36", color), _rule(color=color),
             _col("key", mask_key(api_key) + f" · {kd['tier_text']}"),
             _col("usage", fmt_usd(kd["spend"]) +
                  (f" of {fmt_usd(kd['limit'])} (resets {kd['limit_reset']})"
                   if kd["limit"] is not None else "")),
             "",
             _c("  [S] ", "1", color) + "Search available models" +
             (f" ({model_count})" if model_count is not None else ""),
             _c("  [N] ", "1", color) + "Snapshot",
             _c("  [W] ", "1", color) + "Watch · poll interval prompted (5s)",
             _c("  [Q] ", "1", color) + "Quit"]
    if last_action:
        lines += ["", _c(f"  last: {last_action}", "2", color)]
    lines.append(_c("  press a key · Ctrl-C quits", "2", color))
    return "\n".join(lines)


def render_model_rows(models) -> str:
    lines = [f"  {'':<3}{'model':<38}{'ctx':>7}   {'$/M in':>9} {'$/M out':>9}"]
    for i, m in enumerate(models, 1):
        pr = m.get("pricing") or {}
        lines.append(f"  {i:<3}{(m.get('id') or '')[:38]:<38}"
                     f"{fmt_ctx(m.get('context_length')):>7}   "
                     f"{fmt_per_m(pr.get('prompt')):>9} "
                     f"{fmt_per_m(pr.get('completion')):>9}")
    return "\n".join(lines)


def render_model_detail(model, endpoints, color) -> str:
    lines = [_c(f" {model.get('id')}", "1", color)]
    if not endpoints:
        pr = model.get("pricing") or {}
        lines.append("  no provider data · model pricing: "
                     f"in {fmt_per_m(pr.get('prompt'))}/M · "
                     f"out {fmt_per_m(pr.get('completion'))}/M · "
                     f"cache r {fmt_per_m(pr.get('input_cache_read'))}/M · "
                     f"cache w {fmt_per_m(pr.get('input_cache_write'))}/M")
        return "\n".join(lines)
    lines.append(f"  {'provider':<16}{'ctx':>8} {'maxout':>8}   "
                 f"{'$/M in':>9} {'$/M out':>9} {'cache r':>9} {'cache w':>9}")
    for e in sorted(endpoints, key=lambda x: x.get("provider_name", "")):
        pr = e.get("pricing") or {}
        lines.append(
            f"  {(e.get('provider_name') or '?')[:16]:<16}"
            f"{fmt_ctx(e.get('context_length')):>8} "
            f"{fmt_ctx(e.get('max_completion_tokens')):>8}   "
            f"{fmt_per_m(pr.get('prompt')):>9} {fmt_per_m(pr.get('completion')):>9} "
            f"{fmt_per_m(pr.get('input_cache_read')):>9} "
            f"{fmt_per_m(pr.get('input_cache_write')):>9}")
    return "\n".join(lines)


def run_browser(api_key, models, tui, color) -> str:
    """Model-search sub-loop; returns 'menu' when done. Each view clears
    the screen (tui) so output never piles up."""
    out = sys.stdout.write

    def clear():
        if tui:
            out(TUI_HOME)

    while True:
        clear()
        q = read_line("  model / id (empty = all): ")
        if q.strip().lower() == "q":
            return "menu"
        results = search_models(models, q)
        if not results:
            clear()
            print(f"  no matches for {q!r}")
            continue
        page = 0
        while True:  # results view
            page, pages = clamp_page(page, len(results), RESULT_CAP)
            lo = page * RESULT_CAP
            shown = results[lo:lo + RESULT_CAP]
            clear()
            print(render_model_rows(shown))
            nav = " \u00b7 [N]ext \u00b7 [P]rev" if pages > 1 else ""
            print(f"  {lo + 1}-{lo + len(shown)} of {len(results)}{nav}")
            k = read_key("  [1-9] details \u00b7 [C] new search \u00b7 [B] menu: ")
            if k == "b":
                return "menu"
            if k == "c":
                break  # new query
            if k == "n":
                page += 1
                continue
            if k == "p":
                page -= 1
                continue
            if k in "123456789":
                idx = int(k) - 1
                if idx < len(shown):
                    m = shown[idx]
                    warn = ""
                    try:
                        eps = fetch_model_endpoints(api_key, m["id"])
                    except ApiError as e:
                        eps = []
                        warn = f"  warn: {e}"
                    clear()
                    if warn:
                        print(warn)
                    print(render_model_detail(m, eps, color))
                    kk = read_key("  [B] results \u00b7 [C] new search \u00b7 [M] menu: ")
                    if kk == "m":
                        return "menu"
                    if kk == "c":
                        break
                    # b / other: fall through, results view redraws
        continue
    return "menu"


def run_tui(api_key, tui, color) -> int:
    """Interactive menu loop; owns the alternate screen while tui."""
    out = sys.stdout.write
    if tui:
        out(TUI_IN)
    try:
        try:
            kd = derive(fetch_key_info(api_key))
        except ApiError as e:
            print(f"orustrker: error: {e}", file=sys.stderr)
            return 1
        models = None
        last_action = ""
        while True:
            menu = render_menu(api_key, kd,
                               len(models) if models is not None else None,
                               last_action, color if tui else False)
            if tui:
                out(TUI_HOME + menu + "\n")
                sys.stdout.flush()
            else:
                print(menu)
            k = read_key("  > ")
            if k in ("q", "\x1b", ""):
                return 0
            elif k == "s":
                if models is None:
                    try:
                        models = fetch_models_user(api_key)
                    except ApiError as e:
                        last_action = f"model fetch failed: {e}"
                        continue
                run_browser(api_key, models, tui, color)
                last_action = ""
            elif k == "n":
                try:
                    kd = derive(fetch_key_info(api_key))
                    kd["progress_bar"] = progress(kd["spend"], kd["limit"],
                                                  bar_width())
                    if tui:
                        out(TUI_HOME + build_screen(api_key, kd, time.time(),
                                                    None, color) + "\n\n")
                    else:
                        print(build_screen(api_key, kd, time.time(), None, False))
                    last_action = "snapshot ok"
                except ApiError as e:
                    last_action = f"snapshot failed: {e}"
                read_key("  press any key to return: ")
            elif k == "w":
                raw = read_line("  poll in sec (default 5): ").strip()
                interval = int(raw) if raw.isdigit() and int(raw) > 0 else 5
                rc, last_action = run_watch(api_key, interval, tui, color,
                                            owns_alt=False)
                try:
                    kd = derive(fetch_key_info(api_key))
                except ApiError:
                    pass
        return 0
    finally:
        if tui:
            out(TUI_OUT)


# ---------- self-check ----------

def run_selftest() -> int:
    failures = []

    def check(name, got, want):
        if got == want:
            print(f"  ok  {name}")
        else:
            failures.append(name)
            print(f"  FAIL {name}: got {got!r}, want {want!r}")

    check("mask_key normal", mask_key("sk-or-v1-1234567890abcdef"),
          "sk-or-v1" + BULLET * 16 + "cdef")
    check("mask_key short", mask_key("short"), BULLET * 8)
    check("fmt_usd", fmt_usd(12.3456), "$12.3456")
    check("fmt_elapsed", fmt_elapsed(125), "2m 05s")
    check("pretty_date", pretty_date("2026-12-31T23:59:59.000Z"), "2026-12-31")
    check("pretty_date none", pretty_date(None), "")
    check("progress half", progress(50, 100, 10),
          "[" + "\u2588" * 5 + "\u2591" * 5 + "] 50.0%")
    check("progress overlimit", progress(150, 100, 10),
          "[" + "\u2588" * 10 + "] 150.0%")
    check("progress no limit", progress(1.0, None, 10),
          "[" + "\u2591" * 10 + "] no limit set")

    d = derive({"data": {"label": "my-app", "is_free_tier": False,
                         "usage": 12.34, "limit": 100.0,
                         "rate_limit": {"requests": 200, "interval": "10s"}}})
    check("derive remaining", d["remaining"], 87.66)
    check("derive percent", round(d["percent"], 2), 12.34)
    check("derive tier text", d["tier_text"], "paid")
    check("derive rate", d["rate_limit"]["interval"], "10s")

    d2 = derive({"data": {"usage": 5.0}})
    check("derive no limit", (d2["limit"], d2["remaining"], d2["percent"]),
          (None, None, None))
    check("derive missing data", derive({})["usage"], 0.0)
    check("derive limit reset",
          derive({"data": {"usage": 0, "limit": 100, "limit_reset": "monthly"}})["limit_reset"],
          "monthly")

    # BYOK correctness: usage=0 but limit_remaining is the real signal
    db = derive({"data": {"usage": 0, "byok_usage": 0.5, "limit": 10,
                          "limit_remaining": 9.5, "include_byok_in_limit": True}})
    check("derive BYOK spend", round(db["spend"], 4), 0.5)
    check("derive BYOK remaining", round(db["remaining"], 4), 9.5)
    check("derive BYOK percent", round(db["percent"], 2), 5.0)

    check("rate_text unlimited", rate_text({"requests": -1, "interval": "10s"}),
          "unlimited req / 10s")
    check("rate_text normal", rate_text({"requests": 200, "interval": "10s"}),
          "200 req / 10s")
    check("rate_text empty", rate_text({}), "")

    check("fmt_per_m 0.15", fmt_per_m("0.00000015"), "$0.15")
    check("fmt_per_m cache", fmt_per_m("0.000000016"), "$0.016")
    check("fmt_per_m big", fmt_per_m("0.000003"), "$3.00")
    check("fmt_per_m free", fmt_per_m("0"), "$0")
    check("fmt_per_m none", fmt_per_m(None), "\u2014")
    check("fmt_ctx 1M", fmt_ctx(1000000), "1M")
    check("fmt_ctx 1.5M", fmt_ctx(1500000), "1.5M")
    check("fmt_ctx 256K", fmt_ctx(256000), "256K")
    check("fmt_ctx small", fmt_ctx(512), "512")

    ms = [{"id": "b/model", "name": "B"}, {"id": "a/model", "name": "A"},
          {"id": "a/model-x", "name": "Vendor: Model Extended"},
          {"id": "z/model", "name": "Contains model word"}]
    check("search empty sorts", [m["id"] for m in search_models(ms, "")],
          ["a/model", "a/model-x", "b/model", "z/model"])
    check("search exact first",
          [m["id"] for m in search_models(ms, "a/model")],
          ["a/model", "a/model-x"])
    check("search prefix", [m["id"] for m in search_models(ms, "a/")],
          ["a/model", "a/model-x"])
    check("search name", [m["id"] for m in search_models(ms, "extended")],
          ["a/model-x"])
    check("search substring", [m["id"] for m in search_models(ms, "model")],
          ["a/model", "a/model-x", "b/model", "z/model"])
    check("search none", search_models(ms, "nothing"), [])
    check("parse slug plain", parse_author_slug("qwen/qwen3.8-flash"),
          ("qwen", "qwen3.8-flash"))
    check("parse slug variant", parse_author_slug("openrouter/auto:free"),
          ("openrouter", "auto"))
    check("parse slug tilde", parse_author_slug("~deepseek/deepseek-pro"),
          ("deepseek", "deepseek-pro"))
    check("clamp first", clamp_page(0, 44, 9), (0, 5))
    check("clamp overflow", clamp_page(9, 44, 9), (4, 5))
    check("clamp single page", clamp_page(2, 8, 9), (0, 1))
    check("clamp last exact", clamp_page(1, 10, 9), (1, 2))

    menu = render_menu("sk-or-v1-1234567890abcdef", d, 93, "", False)
    check("menu has options", all(s in menu for s in
          ("[S] Search available models (93)", "[N] Snapshot",
           "[W] Watch", "[Q] Quit")), True)
    check("menu no ANSI plain", "\033[" in menu, False)
    fake_eps = [{"provider_name": "Alibaba", "context_length": 1000000,
                 "max_completion_tokens": 65536,
                 "pricing": {"prompt": "0.00000015", "completion": "0.00000047",
                             "input_cache_read": "0.000000016",
                             "input_cache_write": "0.0000002"}}]
    det = render_model_detail({"id": "qwen/qwen3.8-flash", "pricing": {}},
                              fake_eps, False)
    check("detail provider row",
          "Alibaba" in det and "$0.15" in det and "$0.47" in det
          and "$0.016" in det and "$0.2" in det and "1M" in det, True)
    rows = render_model_rows([{"id": "qwen/x", "context_length": 256000,
                               "pricing": {"prompt": "0.00000008",
                                           "completion": "0.0000003"}}])
    check("rows model line",
          "qwen/x" in rows and "256K" in rows and "$0.08" in rows, True)

    # screen: deterministic given a fixed now_ts; all lines share structure
    now = datetime(2026, 1, 2, 12, 34).timestamp()
    dd = derive({"data": {"label": "app", "usage": 25.0, "limit": 100.0,
                          "limit_remaining": 75.0, "limit_reset": "monthly",
                          "free_model_daily_requests": {"used": 3, "limit": 1000, "remaining": 997}}})
    dd["progress_bar"] = progress(dd["spend"], dd["limit"], 20)
    scr = build_screen("sk-or-v1-1234567890abcdef", dd, now, session=None, color=False)
    check("screen has title", f"orustrker v{__version__} · OpenRouter Usage Tracker" in scr, True)
    check("screen masks key", ("sk-or-v1" + BULLET * 16 + "cdef") in scr, True)
    check("screen no ANSI when color off", "\033[" in scr, False)
    check("screen shows remaining", "$75.0000" in scr, True)
    check("screen shows free", "3 / 1000" in scr, True)
    scr2 = build_screen("k", dd, now, session={"baseline": 10.0, "spend": 12.0,
                          "start_ts": now - 100, "status": "ok"}, color=False)
    check("screen live badge", "[live]" in scr2, True)
    check("screen drops label", "label" in scr, False)
    check("screen drops poll line", "poll" in scr2, False)
    check("screen session delta", "$+2.0000" in scr2, True)
    check("screen ansi when color on", "\033[" in build_screen("k", dd, now, color=True), True)

    # -- fetch layer: stub _http_open with canned bodies --
    import io
    global _http_open  # so fetch_key_info (separate fn) sees stubs
    orig_open = _http_open

    def fake_ok(req) -> bytes:
        return b'{"data": {"usage": 1.0}}'

    _http_open = fake_ok
    try:
        data = fetch_key_info("test-key")
        check("fetch parses json", data["data"]["usage"], 1.0)
    finally:
        _http_open = orig_open

    def fake_http_error(code):
        def raiser(req):
            raise urllib.error.HTTPError(
                req.full_url, code, "err", {}, io.BytesIO(b""))
        return raiser

    for code, fragment in ((401, "401"), (429, "429"), (500, "500")):
        _http_open = fake_http_error(code)
        try:
            try:
                fetch_key_info("k")
                check(f"fetch {code} raises", "no error", fragment)
            except ApiError as e:
                check(f"fetch {code} raises", fragment in str(e), True)
        finally:
            _http_open = orig_open

    if failures:
        print(f"selftest: {len(failures)} failure(s)")
        return 1
    print("selftest: all checks passed")
    return 0


def run() -> int:
    parser = argparse.ArgumentParser(
        prog="orustrker",
        description="Track OpenRouter API key usage (session-only, no disk).")
    parser.add_argument("-w", "--watch", type=int, metavar="SECONDS",
                        help="live refreshing watch every SECONDS with session deltas")
    parser.add_argument("--tui", action="store_true",
                        help="interactive menu: model browser, snapshot, watch")
    parser.add_argument("-st", "--selftest", action="store_true",
                        help="run internal checks and exit")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {__version__}")
    parser.add_argument("--plain", action="store_true",
                        help="force plain output (no TUI/color)")
    args = parser.parse_args()

    if args.selftest:
        return run_selftest()

    tui = use_tui() and not args.plain
    color = use_color() and not args.plain

    if args.watch:
        if args.watch <= 0:
            print("orustrker: error: watch interval must be positive", file=sys.stderr)
            return 1
        try:
            rc, _summary = run_watch(get_api_key(), args.watch, tui, color)
            return rc
        except ApiError as e:
            print(f"orustrker: error: {e}", file=sys.stderr)
            return 1

    if args.tui:
        try:
            return run_tui(get_api_key(), tui, color)
        except ApiError as e:
            print(f"orustrker: error: {e}", file=sys.stderr)
            return 1

    try:
        api_key = get_api_key()
        return run_snapshot(api_key, color)
    except ApiError as e:
        print(f"orustrker: error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(run())