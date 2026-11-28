#!/bin/sh
# Запуск всех проверок: sh run_tests.sh
cd "$(dirname "$0")" || exit 1
status=0
if command -v python3.11 >/dev/null; then
  if python3.11 -c 'import py_compile; py_compile.compile("tg_to_max.py", doraise=True)'; then
    echo "py3.11 syntax ok"
  else
    status=1
  fi
fi
logs=$(mktemp -d) || exit 1
trap 'rm -rf -- "$logs"' 0
for t in test_bot test_themes test_empty test_setup test_web test_login test_main test_main_web test_e2e test_cli test_tgmenu test_telegram_reliability test_hosting test_sales test_files test_host test_cdn test_pool test_generate_video test_generate_promo test_strict_skip test_ai_keys test_persist test_analytics test_trends test_ad_poster test_legal test_nostash test_media_retry test_fwd_stats test_dashboard test_referral test_license test_menu_cleanup test_spam test_audit test_xpromo test_errors test_stress test_characterization; do
  printf "%-28s" "$t"
  if ${PY:-python3} "$t.py" > "$logs/$t.out" 2>&1 && ! grep -q "AssertionError" "$logs/$t.out"; then
    echo ok
  else
    echo FAIL
    tail -8 "$logs/$t.out"
    status=1
  fi
done
exit "$status"
