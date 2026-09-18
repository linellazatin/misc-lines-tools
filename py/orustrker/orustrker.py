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
        "remaining": (limit - usage) if limit is not None else None,
        "rate_limit": rate,
        "free_tier_text": "yes" if info.get("is_free_tier") else "no",
        "progress_bar": progress(usage, limit),
        "percent": (usage / limit * 100) if limit else None,
    }


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

    if failures:
        print(f"selftest: {len(failures)} failure(s)")
        return 1
    print("selftest: all checks passed")
    return 0


def main() -> int:
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


if __name__ == "__main__":
    sys.exit(main())