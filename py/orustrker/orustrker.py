#!/usr/bin/env python3
"""orustrker — OpenRouter USage TRacKER.

Session-only usage reporting for an OpenRouter API key. Nothing is written
to disk; the API key is never logged, only masked for display.

Usage:
  orustrker.py              # snapshot: fetch + display usage once
  orustrker.py -w 30        # watch: poll every 30s, show session deltas
  orustrker.py -st          # run internal self-checks
"""

import argparse
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

API_URL = "https://openrouter.ai/api/v1/auth/key"
BULLET = "\u2022"  # U+2022


# ---------- pure display helpers (no I/O) ----------

def mask_key(key: str) -> str:
    """Mask key for display: first 8 chars, bullets, last 4."""
    if len(key) >= 12:
        return key[:8] + BULLET * 16 + key[-4:]
    return BULLET * 8


def fmt_usd(amount: float) -> str:
    return f"${amount:.4f}"


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
    limit = info.get("limit")
    rate = info.get("rate_limit") or {}
    return {
        "label": info.get("label", "N/A") or "N/A",
        "is_free_tier": bool(info.get("is_free_tier")),
        "usage": usage,
        "limit": limit,
        "limit_reset": info.get("limit_reset"),
        "remaining": (limit - usage) if limit is not None else None,
        "rate_limit": rate,
        "free_tier_text": "yes" if info.get("is_free_tier") else "no",
        "progress_bar": progress(usage, limit),
        "percent": (usage / limit * 100) if limit else None,
    }


# ---------- network ----------

class ApiError(Exception):
    """User-presentable fetch failure."""


def _http_open(req: urllib.request.Request) -> bytes:
    return urllib.request.urlopen(req, timeout=10).read()


def fetch_key_info(api_key: str) -> dict:
    """GET /auth/key; returns parsed JSON or raises ApiError."""
    req = urllib.request.Request(
        API_URL, headers={"Authorization": f"Bearer {api_key}"})
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


# ---------- key + snapshot ----------

def get_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        key = getpass.getpass("OpenRouter API key: ")
    key = key.strip()
    if not key:
        raise ApiError("no API key provided")
    return key


def rate_text(rate_limit: dict) -> str:
    """'200 req / 10s', 'unlimited req / 10s', or '' if absent."""
    if not rate_limit:
        return ""
    req = rate_limit.get("requests")
    if req == -1:
        req = "unlimited"
    return f"{req} req / {rate_limit.get('interval', '?')}"


def render_snapshot(api_key: str, data: dict) -> str:
    d = derive(data)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"  key:       {mask_key(api_key)}",
        f"  label:     {d['label']}",
        f"  free tier: {d['free_tier_text']}",
        f"  used:      {fmt_usd(d['usage'])}",
    ]
    if d["limit"] is not None:
        resets = f" (resets {d['limit_reset']})" if d["limit_reset"] else ""
        lines.append(f"  limit:     {fmt_usd(d['limit'])}{resets}")
        lines.append(f"  remaining: {fmt_usd(d['remaining'])}")
        lines.append(f"  progress:  {d['progress_bar']}")
    else:
        lines.append("  limit:     not set")
    if d["rate_limit"]:
        lines.append(f"  rate:      {rate_text(d['rate_limit'])}")
    lines.append(f"  checked:   {now}")
    return "\n".join(lines)


# ---------- watch ----------

def render_tick(now_ts, usage, delta, remaining):
    when = datetime.fromtimestamp(now_ts).strftime("%H:%M:%S")
    d = f" delta ${delta:+.4f}"
    rem = f"  remaining ${remaining:.4f}" if remaining is not None else ""
    return f"  [{when}] usage ${usage:.4f}{d}{rem}"


def run_watch(api_key: str, interval: int) -> int:
    start = time.time()
    baseline = None       # usage at session origin
    last_usage = None
    requests_made = 0
    consecutive_failures = 0

    try:
        while True:
            try:
                d = derive(fetch_key_info(api_key))
                requests_made += 1
                consecutive_failures = 0
                usage = d["usage"]
                if baseline is None:
                    baseline = usage
                    print(f"  baseline: usage ${baseline:.4f} (session origin)\n")
                else:
                    print(render_tick(time.time(), usage,
                                      usage - baseline, d["remaining"]))
                last_usage = usage
            except ApiError as e:
                consecutive_failures += 1
                print(f"  warn: {e} ({consecutive_failures}/5)", file=sys.stderr)
                if consecutive_failures >= 5:
                    print("orustrker: 5 consecutive failures, giving up",
                          file=sys.stderr)
                    return 1
            time.sleep(interval)
    except KeyboardInterrupt:
        elapsed = time.time() - start
        acc = (last_usage - baseline) if (last_usage is not None
                                          and baseline is not None) else 0.0
        print(f"\n  session: {int(elapsed // 60)}m {int(elapsed % 60)}s, "
              f"requests: {requests_made}, usage accrued: ${acc:.4f}")
        return 0


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
    check("derive percent", d["percent"], 12.34)
    check("derive free text", d["free_tier_text"], "no")
    check("derive rate", d["rate_limit"]["interval"], "10s")

    d2 = derive({"data": {"usage": 5.0}})
    check("derive no limit", (d2["limit"], d2["remaining"], d2["percent"]),
          (None, None, None))
    check("derive missing data", derive({})["usage"], 0.0)
    check("derive limit reset",
          derive({"data": {"usage": 0, "limit": 100, "limit_reset": "monthly"}})["limit_reset"],
          "monthly")

    check("rate_text unlimited", rate_text({"requests": -1, "interval": "10s"}),
          "unlimited req / 10s")
    check("rate_text normal", rate_text({"requests": 200, "interval": "10s"}),
          "200 req / 10s")
    check("rate_text empty", rate_text({}), "")

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

    ts = datetime(2026, 1, 2, 12, 34).timestamp()  # local-time roundtrip, TZ-safe
    check("render_tick", render_tick(ts, 12.5, 2.0, 87.5),
          "  [12:34:00] usage $12.5000 delta $+2.0000  remaining $87.5000")

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
                        help="poll every SECONDS and show session deltas")
    parser.add_argument("-st", "--selftest", action="store_true",
                        help="run internal checks and exit")
    args = parser.parse_args()

    if args.selftest:
        return run_selftest()

    if args.watch:
        if args.watch <= 0:
            print("orustrker: error: watch interval must be positive",
                  file=sys.stderr)
            return 1
        try:
            return run_watch(get_api_key(), args.watch)
        except ApiError as e:
            print(f"orustrker: error: {e}", file=sys.stderr)
            return 1

    try:
        api_key = get_api_key()
        print(f"  using key: {mask_key(api_key)}\n", file=sys.stderr)
        data = fetch_key_info(api_key)
        print(render_snapshot(api_key, data))
        return 0
    except ApiError as e:
        print(f"orustrker: error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(run())