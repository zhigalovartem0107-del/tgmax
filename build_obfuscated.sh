#!/bin/sh
# 🔒 Сборка защищённой (обфусцированной) версии бота для выкладки на хостинг (PyArmor).
# Исходный код превращается в нечитаемый — скопировать и разобрать бота становится намного труднее.
# Запуск защищённой версии ВСЁ РАВНО требует правильный BOT_SECRET (reloader/services/license.py).
#
# Как пользоваться:
#   pip install pyarmor            # один раз
#   sh build_obfuscated.sh         # соберёт папку dist_obf/
#   # загрузи ВСЁ содержимое dist_obf/ на хостинг и задай переменную BOT_SECRET
#   # запуск на хостинге — как обычно: python main.py
set -eu

cd "$(dirname "$0")"
OUT="dist_obf"

if ! command -v pyarmor >/dev/null 2>&1; then
  echo "❌ PyArmor не установлен. Установи его и повтори:"
  echo "   pip install pyarmor"
  exit 1
fi

echo "🔒 Собираю защищённую версию в $OUT/ (PyArmor $(pyarmor --version 2>/dev/null | head -n1))"
rm -rf "$OUT"
mkdir -p "$OUT"

# Обфусцируем точки входа и весь пакет reloader (рекурсивно). main.py остаётся рабочим:
# в нём замок BOT_SECRET, который PyArmor тоже защитит.
pyarmor gen -O "$OUT" -r main.py login.py tg_to_max.py reloader

# Нешифруемое, что нужно рядом (документация). Данные бота (config, очередь) создаются сами в папке data.
cp -f README.txt "$OUT"/ 2>/dev/null || true

echo "✅ Готово: $OUT/"
echo "   1) Загрузи ВСЁ содержимое $OUT/ на хостинг (Bothost)."
echo "   2) Задай переменную окружения BOT_SECRET (значение — у владельца)."
echo "   3) Запуск: python main.py (как обычно)."
