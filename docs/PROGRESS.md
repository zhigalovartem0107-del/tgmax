# Refactor progress (release 1)

Design: `docs/superpowers/specs/2026-11-02-reloader-modular-design.md` (§5.1 = changes decided during step 0).

## Done
- 0a/0b: golden tests `test_characterization.py` + `fixtures/golden.json` (filters: 150 posts; queue: 6 scenarios).
- 0d: package skeleton `reloader/…`; facade in `tg_to_max.py` (`_Facade`, `_MOVED`): fresh package per load,
  `bot.X = fake` is forwarded to the module where X now lives.
- 1a `reloader/core/log.py` · 1b `reloader/keyboards/buttons.py` · 1c `reloader/core/timeutil.py` ·
  1d `reloader/core/files.py`

## How to do the next step
1. Move code unchanged: `python3 tools/move_defs.py <reloader/path.py> "<docstring>" "import a,import b" NAME …`
   (functions, classes, constants; it refuses if the module would use names it doesn't get).
2. Ship: `sh tools/ship.sh "Step N: …"` — runs all suites and commits/pushes only if everything is green.

## Next
- 1e `config.py` (CONFIG_FIELDS, wizard, DEFAULTS) — careful: module-level code reads env at import.
- Then step 2 (pure logic) needs state first in practice: filters use CFG → do step 3 (core/state, core/themes)
  before moving filters.
