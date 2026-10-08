# Watching the run from outside (slice H)

The sealed year runs on your PC. Anyone you send a link to can watch it in their browser, from
anywhere, without an account and without being able to change anything. This page says how
that works and how to set it up. The PowerShell steps are also in the
[runbook](sealed-trial-runbook.md) (step 6b).

## How it fits together

```
run (window 1) ──writes──▶ work\trial ◀──reads── observe --public (window 2, 127.0.0.1:8766)
                                                         ▲
                                     cloudflared tunnel (window 3) ◀── HTTPS ── viewers
```

- `run` is the only writer of the run, as before.
- `observe work\trial --public` serves the run read-only on this PC only (127.0.0.1). It refuses
  `--run-days` (it runs no world) and any other `--host`.
- `cloudflared tunnel --url http://127.0.0.1:8766` gives it a public `https://` address. No
  port is opened on your router: the tunnel connects out to Cloudflare.

## Who may do what

| | The link `observe` prints for you | The shared link (viewer token) |
|---|---|---|
| Watch the map, days, people, chronicle, council views, seal | yes | yes |
| Pause, resume or set the lookahead | no (a public observer has no controls) | no (403) |
| Works through the tunnel | no: your token is refused (401) on any request that came through a proxy | yes |
| Token stays in the address after loading | no (dropped, as before) | yes: the link is the viewer's pass, so a reload or a bookmark works |

The page shows **VIEWING ONLY · shared** on a public observer.

The viewer token is made fresh each time `observe --public` starts (or taken from
`SOVEREIGN_WORLD_VIEWER_TOKEN`; it must be at least 32 characters and differ from the
observer's own token). Restarting `observe --public` therefore cancels every link you have
sent: that is how you take a link back.

## What viewers can and cannot see

They see what the observer serves today and nothing more: the recorded days (map, settlements,
people, travellers), the chronicle, each civilization's council view, and the seal's public
parts (fingerprint, hashes, base addresses and the names of token variables).

They never see prompts, model replies, provider errors, keys or tokens, or any path on your PC
(`test_a_viewer_sees_no_path_from_this_machine` walks every route to check this).

## Limits and headers

Every request passes one guard before any route:

| What | Limit | Answer when over |
|---|---|---|
| Requests from one visitor | a burst of 600, then 20 a second | 429 with `Retry-After` |
| Requests from all visitors together | a burst of 3,000, then 150 a second | 429 with `Retry-After` |
| Request body | 4 KB, with a declared length | 413 (too large), 411 (no length) |

Requests straight from this PC (your own window, its page and files, not through the tunnel)
are never held back. Visitors are told apart by the address
Cloudflare forwards (the server trusts that header only from the tunnel on 127.0.0.1); visitors
behind one shared connection share an allowance.

Every answer carries:
- a strict `Content-Security-Policy` (scripts, styles, images and connections from the observer
  itself only; no framing);
- `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`,
  a `Permissions-Policy` that turns off camera, microphone and location, and same-origin
  `Cross-Origin-Opener-Policy` and `Cross-Origin-Resource-Policy`;
- no `Server` header.

Under that policy PixiJS cannot build its drawing functions with `eval`, so the page loads
Pixi's own no-eval polyfills (`observer/vendor/pixi/unsafe-eval.min.js`, unmodified) before it
draws.

## The tunnel

The quick tunnel needs no account:

```powershell
winget install --id Cloudflare.cloudflared
cloudflared tunnel --url http://127.0.0.1:8766
```

It prints an address like `https://some-random-words.trycloudflare.com`. Put it in front of the
path `observe` printed for viewers (`/?run=live#token=...`) and send that link.

Things to know (check Cloudflare's current documentation; not confirmed here):
- Cloudflare offers quick tunnels for testing and development, with its own limits.
- The address changes every time `cloudflared` restarts, so a reboot means sending a new link.
- For an address that stays the same, use a named tunnel instead: a free Cloudflare account
  and a domain you own (`cloudflared tunnel login`, `cloudflared tunnel create`,
  `cloudflared tunnel route dns`, `cloudflared tunnel run`). The observer side is the same.
- If only your own devices need to watch, a private network such as Tailscale is simpler and
  keeps the run off the public internet.

## Stopping and taking links back

- Stop sharing: Ctrl+C in the `cloudflared` window. The run and your own window go on.
- Take every link back: Ctrl+C in the `observe --public` window, then start it again; it prints a
  new viewer link.
- Neither touches the run: `run` keeps going in its own window.

## Checks only your PC can make

- A phone off your home Wi-Fi opens the shared link, shows **VIEWING ONLY · shared**, steps a
  day, and still opens after a reload.
- Your own printed link, with the tunnel address in front of it, is refused.
- `curl -sI https://<your tunnel address>/` shows the security headers above.
- Cloudflare's forwarded header names: `curl -s -H "Authorization: Bearer <viewer token>"
  https://<your tunnel address>/api/status` answers with `"role":"viewer"`.
