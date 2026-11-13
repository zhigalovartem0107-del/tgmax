#!/bin/sh
# Запуск всех проверок: sh run_tests.sh
command -v python3.11 >/dev/null && python3.11 -c 'import py_compile; py_compile.compile("tg_to_max.py", doraise=True)' && echo "py3.11 syntax ok"
cd "$(dirname "$0")"
# 🔒 BOT_SECRET для тестов запуска main.py: из окружения или из gitignore-файла .bot_secret.
[ -z "${BOT_SECRET:-}" ] && [ -f .bot_secret ] && BOT_SECRET="$(cat .bot_secret)"
export BOT_SECRET
for t in test_bot test_themes test_empty test_setup test_web test_login test_main test_main_web test_e2e test_cli test_tgmenu test_hosting test_sales test_files test_host test_cdn test_pool test_generate_video test_generate_promo test_strict_skip test_ai_keys test_persist test_analytics test_trends test_legal test_nostash test_media_retry test_fwd_stats test_dashboard test_referral test_license test_menu_cleanup test_stress test_characterization; do
  printf "%-16s" "$t"
  if ${PY:-python3} $t.py > /tmp/_$t.out 2>&1 && ! grep -q "AssertionError" /tmp/_$t.out; then echo ok; else echo FAIL; tail -8 /tmp/_$t.out; fi
done
