#!/bin/sh
# 🔒 Сборка защищённой (обфусцированной) версии бота для выкладки на хостинг (PyArmor).
# Исходный код превращается в нечитаемый — скопировать и разобрать бота становится намного труднее.
# Запуск защищённой версии ВСЁ РАВНО требует правильный BOT_SECRET (bot_app/services/license.py).
#
# Как пользоваться:
#   pip install pyarmor            # один раз
#   sh build_obfuscated.sh         # соберёт папку dist_obf/
#   # загрузи ВСЁ содержимое dist_obf/ на хостинг и задай BOT_SECRET и BOT_SECRET_SHA256
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

# Обфусцируем точки входа и весь пакет bot_app (рекурсивно). main.py остаётся рабочим:
# в нём замок BOT_SECRET, который PyArmor тоже защитит.
pyarmor gen -O "$OUT" -r main.py login.py tg_to_max.py bot_app

# Нешифруемое, что нужно рядом: зависимости (хостинг ставит их по requirements.txt) и документация.
# Данные бота (config, очередь, вход) создаются сами в папке data при первом запуске.
cp -f requirements.txt "$OUT"/ 2>/dev/null || true
cp -f README.txt "$OUT"/ 2>/dev/null || true
mkdir -p "$OUT/docs"
cp docs/BOTHOST.md "$OUT/docs/"

echo "✅ Готово: $OUT/"
echo "   1) Загрузи ВСЁ содержимое $OUT/ на хостинг (Bothost)."
echo "   2) Задай BOT_SECRET и BOT_SECRET_SHA256 по инструкции docs/BOTHOST.md."
echo "   3) Запуск: python main.py (как обычно)."
