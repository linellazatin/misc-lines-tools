# orustrker

OpenRouter USage TRacKER — check an OpenRouter API key's usage, credits, and limits, or watch them on a live-refreshing terminal screen. Session-only: nothing is written to disk, no analytics, no storage, and the key is never logged — only masked for display. Zero dependencies (Python stdlib only).

## Usage

```sh
orustrker.py                  # snapshot: one poll, render the status screen
orustrker.py -w 5             # watch: live-refreshing screen, session deltas
orustrker.py --tui            # interactive menu: browser / snapshot / watch
orustrker.py --plain          # force plain output (no TUI/color)
orustrker.py -st              # internal checks
```

Long flags also work: `--watch`, `--selftest`.

The API key comes from the `OPENROUTER_API_KEY` environment variable, or an interactive (non-echoing) prompt if unset.

## Interactive menu (`--tui`)

Hotkey-driven (single press, no Enter; termios cbreak, stdlib). Header shows masked key + spend vs limit. Options:

- `[S]` model browser: searches the models **this key** can serve
  (`/models/user`, account provider settings apply; cached per session). Substring match on id/name, results paginated 9 per page (`[N]ext` / `[P]rev`), digit opens the per-provider table (provider, context, max out, $/M tokens: in, out, cache read, cache write). Each view clears the screen — output never piles up.
- `[N]` snapshot: the status screen, one poll.
- `[W]` watch: prompts for the poll interval (default 5), then the live
  screen; Ctrl-C returns to the menu (summary kept as a "last:" line).
- `[Q]` quit.

Omitted by design: time-of-day pricing overrides, per-model usage history (management-only), browsing all 445 models the key cannot serve.

## Snapshot

One call to the official `/api/v1/auth/key` endpoint, rendered as a status screen: tier, expiry, data regions, total/day/week/month usage, the BYOK ledger, limit + reset cadence, remaining, a progress bar, and the free-model daily request counter.

## Watch (`-w SECONDS`)

A btop-style live screen using the terminal's alternate buffer (raw ANSI, no curses). It redraws in place on each poll, showing session delta vs the first poll's baseline, elapsed time, and fetch status. A failed poll keeps the last-good screen and reports the failure; 5 consecutive failures stop the watch. Ctrl-C restores your terminal and prints a session summary — all state lives in memory and is discarded on exit.

When stdout is not a terminal (piping to a file, grep, etc.) the TUI is skipped and each poll prints a plain one-line log entry instead, so output stays script-friendly. Colors also honor `NO_COLOR`.

## Known limits

- The screen refreshes once per poll interval — usage data only changes when
  polled (OpenRouter has no push), so there is no value in redrawing between polls.
- OpenRouter exposes no per-key usage history API. Per-request breakdown
  needs the recording proxy (see "Extensions planned" below).
- The `/auth/key` `rate_limit` field is deprecated upstream (always reports
  `-1`); the live per-minute cap is enforced but not queryable per key.
- The key's `usage` field ignores BYOK spend; orustrker uses the API's
  `limit_remaining` and a `usage + byok_usage` spend metric so BYOK keys are not silently under-reported.
- Free-tier keys may report no limit; the screen then shows "no limit set".
- The tool makes one `/auth/key` request per snapshot, plus one per watch poll.

## Extensions planned (not implemented)

Recording proxy (per-request usage via X-Usage header + `/generation` enrichment), which unlocks a "last generation" screen panel — provider/model, token usage, cache_discount, total_cost, latency, generation_time, streamed/cancelled. A plain inference key cannot list generations, so the panel ships with the proxy, not before it. Also planned: JSON output, multi-key watch, limit-proximity alerts, org mode via a management key.