# Reloader: modular refactor — design (release 1 of 2)

Date: 2026-11-02 · Status: **waiting for owner approval** · Bot version at start: 2026110351

## 1. Goal and success criteria

Turn the Reloader monolith (`tg_to_max.py`, 11,823 lines) into a modular codebase that is easy to change
without breaking something unrelated, **with zero behavior change** for buyers and the owner.

Release 1 is done when:
1. The code lives in a package with `handlers/`, `services/`, `keyboards/`, `config.py`, `main.py` (layout in §5).
   No module is longer than about 600 lines, and the 7 giant dispatchers are split into small functions (§6.3).
2. **All 16 existing test suites pass** after every step, plus the new characterization tests (§7). Their test logic
   stays unchanged; the only edits are the single-copy fix for `bot2`/`b2` (step 0) and the file path line when the
   suites move into `tests/` (step 10).
3. Every feature in the inventory (§9) still works and keeps the same test coverage.
4. The bot deploys to Bothost from its own GitHub repository, and existing data (buyers, queues, payments,
   Telegram login) survives the switch (§10).

## 2. Decisions made with the owner

| Question | Decision |
|---|---|
| How Bothost gets the code | **Own GitHub repo** (like the music bot): push, then redeploy. No more uploading `main.py`. |
| MAX layer | **Keep Reloader's own MAX HTTP layer** for now; it moves to `services/max_api.py` unchanged. `maxapi` can be a later, separate step. |
| Database | **Release 2**, separate. Release 1 keeps the JSON files but puts them behind a storage interface, so release 2 only swaps that interface. |

## 3. Current state (measured)

- `tg_to_max.py`: 11,823 lines, 482 top-level functions, 23 classes, about 100 module-level mutable globals.
- 7 dispatcher functions hold most menu logic: `_screen` (468 lines, 40 screens), `do_action` (276, 59 actions),
  `apply_text` (224), `host_screen` (176), `handle_message` (162), `run_command` (128), `host_action` (113).
- Concurrency: an asyncio loop (Telethon reading, per-theme schedulers, background loop) plus threads
  (MAX menu polling, the client-bot supervisor, the hub, broadcasts).
- State: about 15 kinds of JSON files in the data folder (settings, themes, tenants, queue/pub/history per
  theme, cards, ad model, autodel, consent, social, clients), plus `session.session` (Telegram login).
- Tests: 16 suites, about 5,500 lines. 15 of them load the whole file as one module `bot` and replace
  **56 distinct names** with fakes (`bot.api_send = …`, `bot.notify = …`, `bot.publish_item = …`).
  Two suites load a second independent copy (`bot2`, `b2`).
- `main.py` (34 lines) picks the newest `BOT_VERSION` file to support phone uploads; sales mode starts client bots
  as subprocesses of the bot file. `legacy_v2.py` is a fixture for the old-data migration test.

## 4. Approaches considered

**A. Rewrite into new modules in one go.** Rejected: months of edge-case fixes would be retyped by hand, and with
the tests needing a rewrite too there is no safety net while it happens.

**B. Mechanical extraction behind a compatibility facade (chosen).** Move code section by section into modules,
unchanged, and run all suites after each move. A thin facade keeps `bot.X` working for the existing tests, including
`bot.X = fake` (§7.2), so the existing 5,500 lines of tests stay the contract during the whole migration. The giant
dispatchers are converted to registries in their own steps, each branch becoming a function with identical body.

**C. Rewrite the tests against new module names first, then move code.** Rejected: doubles the work and loses the
"tests unchanged" guarantee that proves nothing changed.

## 5. Target layout

```
reloader-bot/                       # new GitHub repo
  main.py                           # entry: data folder, logging, start app (replaces the version picker)
  app.py                            # wiring: Telegram client, MAX polling thread, schedulers, background loops
  config.py                         # env + config.json + first-run wizard; constants (limits, tariffs, defaults)
  bothost.json                      # {"main": "main.py", "language": "python", "python_version": "3.11"}
  requirements.txt                  # unchanged in release 1
  core/
    log.py                          # print -> bot.log, journal, log_exc
    files.py                        # data folder, load_json/save_json, session discovery
    state.py                        # runtime registries: PENDING, FLASH, BIND, codes, DENIED, NET, ... (one home)
    themes.py                       # Theme, CUR context, CFG/QUEUE/PUB proxies, theme_ctx
    timeutil.py                     # night mode, fmt_min/fmt_date/n_word, schedule time helpers
    clean_chat.py                   # bot messages vanish after an hour, one menu message (AUTODEL)
  storage/
    repo.py                         # storage interface over today's JSON files (release 2: SQLAlchemy behind it)
  services/
    max_api.py                      # all HTTP to MAX: send, uploads, files, chats, members, pins, deletes
    cards.py                        # approval cards in admin chats: add, resolve, purge
    filters/clean.py                # clean_text, fix_markdown, signatures, footers, replacements
    filters/ads.py                  # ad rules + learning ad model
    filters/dedupe.py               # duplicate detection
    smart.py                        # smart time, smart tags, post quality, AI editor
    telegram/client.py              # Telethon client, keys, auth checks, reconnect
    telegram/cdn.py                 # CDN downloader (handshake, keys, server list, self-test)
    telegram/media.py               # download photo/video/doc, tg_media, retries
    telegram/reader.py              # process_channel, sync_channels, poll, network errors
    telegram/web.py                 # reading public channels via t.me
    telegram/shared.py              # shared account / hub for client bots
    telegram/login.py               # Telegram login from the MAX menu (+ qr.py)
    social.py                       # TikTok, VK, YouTube, Rutube, RSS
    publisher.py                    # compose, split, publish_item, fetch_files, to_stuck
    queue.py                        # approve, batches, publish_due, last_slot, urgent, series
    analytics/stats.py · analytics/dashboard.py · analytics/report.py
    tenants.py                      # cabinets, free plan, limits, terms reminders
    payments/tariffs.py · payments/custom.py · payments/promos.py · payments/yookassa.py · payments/host_pay.py
    broadcast.py · force_sub.py · beta.py · sales.py · diagnostics.py · qr.py
  handlers/
    router.py                       # handle_update: gates (force-sub, lock), message/callback/bot_started
    screens/                        # one function per screen, registered by name (§6.3)
      channels.py · schedule.py · filters.py · media.py · themes.py · admins.py · smart.py · status.py
    actions/                        # one function per button action, same split by area
    text_input.py                   # PENDING text answers, registered by action name
    commands.py                     # /commands
    approval.py                     # ap: callbacks, fix_stuck
    payments.py                     # py/pk/pd/pc callbacks, tariff screens
    telegram_login.py               # tg_* callbacks
    admin/panel.py · admin/cabinets.py · admin/tariffs.py · admin/promos.py · admin/sales.py
  keyboards/
    buttons.py                      # B(), LB(), CONTACT_BTN, common rows (back, cancel)
  workers/
    scheduler.py                    # per-theme scheduler, background_loop
    reader_loop.py                  # the main read loop
  tests/                            # the 16 suites (paths updated), fakes/fake_max.py, fixtures/legacy_v2.py
    test_characterization.py        # new: golden results for filtering and queue (§7.1)
  tg_to_max.py                      # compatibility facade for the tests only (§7.2); removed in release 2
  docs/
```

About 50 modules, 100 to 600 lines each.

### 5.1 Changes decided during step 0
- The folders live inside one package, `reloader/` (`reloader/core`, `reloader/services`, `reloader/handlers`,
  `reloader/keyboards`, `reloader/workers`, `reloader/storage`). That way generic names like `services` can't clash
  with installed libraries, and the facade can load a fresh copy of the whole package.
- Step 0c is dropped: the tests that load a second copy (`bot2`, `b2`) stay unchanged. Loading `tg_to_max.py` under a
  new name imports a fresh `reloader` package, so each copy has its own state (§7.2).

## 6. Design rules

### 6.1 No behavior change in release 1
Moves keep function bodies identical. The only edits allowed are import lines and module-qualified names, plus the
dispatcher conversion (§6.3), which keeps each branch's body. Bug fixes and improvements found along the way go to
a list and become separate small releases after release 1.

### 6.2 Import modules, not names
Cross-module calls use `max_api.api_send(...)`, never `from services.max_api import api_send`. That keeps a single
source of truth for each function (patchable by tests, replaceable at runtime) and avoids stale copies of values
that change at runtime (for example `SELLER_CONTACT`, `HOST_CHAT`). No package imports inside functions.

### 6.3 Dispatchers become registries
`_screen`, `do_action`, `apply_text`, `host_screen`, `host_action`, `run_command` and `handle_message`'s action
branches become small functions registered by name:

```python
@screen("ch")
def channels(uid): ...            # the old `if name == "ch":` body, unchanged
```

The router keeps today's common logic (FLASH note on top of a screen, `name.startswith("cl")` routing to the owner
panel, the force-subscription and lock gates) and looks the handler up in the registry. That gives one small
function per screen or button, testable on its own (the ub-mode "simple functions" goal), without changing what any
screen shows.

### 6.4 State has one home
The about 100 module-level registries move to `core/state.py` (runtime) and `core/themes.py` (per-theme proxies).
Modules refer to them as `state.PENDING`. No logic change; it just stops state from being scattered.

### 6.5 Storage behind an interface
`storage/repo.py` exposes the operations the code already performs (load/save settings, themes, tenants, a theme's
queue/pub/history, cards, consent). It writes exactly the same JSON files to the same places. Release 2 swaps
its implementation to SQLAlchemy without touching handlers or services.

## 7. Test strategy (the safety net)

### 7.1 Before moving anything: characterization tests
New `test_characterization.py` records today's results as golden data:
- **Filtering:** a corpus of about 150 real-shaped posts (ads with and without marking, giveaways, footers, signatures,
  links, formatting, duplicates and near-duplicates) run through `clean_text`, `ad_verdict`, `find_duplicate`,
  `fix_markdown`. Results are saved to `tests/fixtures/golden_filters.json`.
- **Queue:** scripted scenarios on a fake clock (schedule on/off, night mode, urgent posts, batches after restart,
  manual "now"/"later", waiting-for-file posts, free-plan pause) record the publish order and timing.

The golden files are generated once from the current code and must stay identical after every step.

### 7.2 Compatibility facade, so the 16 suites stay unchanged
`tg_to_max.py` becomes a facade module object: reading `bot.X` finds `X` in the module that now owns it; **setting**
`bot.X = fake` sets it on that owning module. Because of §6.2, every caller sees the fake, just as today. The facade
is only for tests (the bot runs from `main.py`) and is deleted once the suites are moved to direct imports in
release 2.

The two suites that load a second copy (`bot2`, `b2`) are changed in step 0 to reset state on the single copy
instead. That is the only test change before the move, and it is reviewed on its own.

### 7.3 After every step
All 16 suites, the characterization tests, and the Python 3.11 compile check must pass. A step that doesn't pass is
reverted, not patched.

## 8. Migration steps (each is one green step)

| # | Step | Moves |
|---|---|---|
| 0 | Safety net | characterization tests + golden data; single-copy fix for `bot2`/`b2`; empty package skeleton + facade |
| 1 | Foundations | `core/log`, `core/files`, `core/timeutil`, `config`, `keyboards/buttons` |
| 2 | Pure logic | `filters/clean`, `filters/ads`, `filters/dedupe`, `smart`, `qr`, `analytics/*` |
| 3 | State | `core/state`, `core/themes`, `storage/repo`, `services/tenants` |
| 4 | MAX layer | `services/max_api`, `services/cards`, `core/clean_chat` |
| 5 | Telegram | `telegram/client`, `cdn`, `media`, `web`, `reader`, `shared`, `login`; `social` |
| 6 | Publishing | `publisher`, `queue`, `workers/scheduler` |
| 7 | Business features | `payments/*`, `promos`, `force_sub`, `broadcast`, `beta`, `sales`, `diagnostics` |
| 8 | Handlers | router + registries; screens, actions, text input, commands, approval, admin panel (several sub-steps) |
| 9 | Entry point | `app.py`, `main.py`, `workers/reader_loop.py`; client bots spawn `main.py` with their env |
| 10 | Repo + deploy | repo layout, `bothost.json`, README; deploy checklist (§10) |

Steps 1–7 go leaves-first, so each module only depends on ones already moved. Step 8 is the largest and is split into
sub-steps per screen area.

## 9. Feature inventory (nothing may be lost)

Each line names the new module(s) and the existing tests that keep proving it works:
sources and reading (`telegram/reader`, `web`, `social`: test_web, test_host h38/h44/h57); filters and ads
(`filters/*`: test_host h30/h41/h46, characterization); duplicates (`dedupe`); queue, schedule, night, urgent, series,
batches (`queue`: test_bot 1–15, test_host h53/h56); approval cards and stuck posts (`approval`, `cards`: test_bot
9–9e, h42); media and files including CDN (`media`, `cdn`: test_cdn c1–c11, test_files); analytics, daily
report and dashboard (`analytics/*`: h37/h48); themes (test_themes); host cabinets, free plan, limits (`tenants`: h1–h8,
h58); payments, YooKassa, promos, custom tariff (`payments/*`: h3/h31/h40/h62); ad broadcast (h59); force subscription
(h60/h61); beta features (h36); Telegram login from MAX (test_login, test_tgmenu); sales and client bots
(test_sales, test_hosting); clean chat; diagnostics (h51); commands (test_bot); setup wizard and CLI (test_cli,
test_setup, test_empty, test_main).

## 10. Deployment and data safety

1. The owner creates an empty GitHub repo (for example `reloader-bot`) and connects it; the code is pushed there.
2. **Test bot first:** deploy the repo on Bothost as a second, test bot (its own MAX token, a copy of the data
   folder). Check that the data survives a redeploy: put a marker file in `data/`, redeploy, confirm it's still
   there.
3. Back up the live `data/` folder (including `session.session`) before switching the real bot.
4. Switch the real bot to the repo. Rollback: the old single `main.py` + `tg_to_max.py` still run on the same
   data folder, because release 1 doesn't change the data format.

## 11. Release 2 (database) — outline only, separate design later

SQLAlchemy 2 with SQLite (WAL). A **sync** engine, since Reloader uses threads as well as asyncio. Models: Tenant,
Theme (settings as a JSON column), SourceChannel, QueueItem, PublishedPost (history), Card, HeldAd, Promo,
PaymentLog, Consent. On first start it imports the existing JSON files and keeps them untouched as a backup.
`storage/repo.py` switches implementation; handlers and services don't change. In that release the test suites move
to direct module imports and the facade is deleted.

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| A fake in tests stops reaching the real caller, so tests silently hit the network | §6.2 rule + facade setattr forwarding; a check in step 0 that fails if any test opens a real connection |
| Hidden coupling through globals | state centralized in step 3; leaves-first order |
| Dispatcher conversion changes a screen | branch bodies copied unchanged; golden test of every screen's text and buttons (render all 40+ screens before and after) |
| Bothost wipes data on git redeploy | test bot + marker file check before switching (§10) |
| Client bots (sales) spawn the old file | step 9 spawns `main.py` with the same env; test_sales/test_hosting cover it |
| A long migration blocks urgent fixes | each step is small and releasable; urgent fixes go to the old file and are replayed |

## 13. Out of scope for release 1

Switching to `maxapi`; the database (release 2); pydantic-settings; new features; bug fixes not required by a move
(they are listed and done right after).
