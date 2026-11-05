#!/bin/sh
# Запуск всех проверок: sh run_tests.sh
command -v python3.11 >/dev/null && python3.11 -c 'import py_compile; py_compile.compile("tg_to_max.py", doraise=True)' && echo "py3.11 syntax ok"
cd "$(dirname "$0")"
for t in test_bot test_themes test_empty test_setup test_web test_login test_main test_main_web test_e2e test_cli test_tgmenu test_hosting test_sales test_files test_host test_cdn test_pool test_generate_video test_generate_promo test_characterization; do
  printf "%-16s" "$t"
  if ${PY:-python3} $t.py > /tmp/_$t.out 2>&1 && ! grep -q "AssertionError" /tmp/_$t.out; then echo ok; else echo FAIL; tail -8 /tmp/_$t.out; fi
done
