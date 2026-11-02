#!/bin/sh
# Добавить нового бота на сервер:  sh /root/bots/add_bot.sh имя
# Спросит токен бота MAX и ссылку на канал, запустит бота и покажет код привязки владельца.
NAME="$1"
if [ -z "$NAME" ] || ! echo "$NAME" | grep -Eq '^[a-zA-Z0-9_-]+$'; then
  echo "Напиши имя латиницей, например:  sh /root/bots/add_bot.sh vasya"
  exit 1
fi
DIR="/root/bots/$NAME"
if [ -e "$DIR" ]; then
  echo "Бот $NAME уже есть. Посмотреть код:  grep код $DIR/bot.log | tail -2"
  exit 1
fi

while :; do
  printf "Токен бота MAX: "; read -r TOKEN
  case "$TOKEN" in *" "*|"") echo "  Токен — длинная строка без пробелов." ;; *) [ ${#TOKEN} -ge 8 ] && break; echo "  Слишком короткий." ;; esac
done
while :; do
  printf "Ссылка на канал MAX (https://max.ru/...): "; read -r LINK
  case "$LINK" in *max.ru/*) break ;; *) echo "  Нужна ссылка вида https://max.ru/название" ;; esac
done

mkdir -p "$DIR"
/root/bots/venv/bin/python -I -c 'import json, sys; json.dump({"max_token": sys.argv[2], "max_channel_link": sys.argv[3]}, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=2)' "$DIR/config.json" "$TOKEN" "$LINK"
chmod 600 "$DIR/config.json"
systemctl enable --now "tg_to_max@$NAME" >/dev/null 2>&1

echo "Запускаю бота $NAME..."
sleep 10
if systemctl is-active --quiet "tg_to_max@$NAME"; then
  echo "✅ Бот $NAME работает."
  grep -E "код" "$DIR/bot.log" | tail -2
  echo "Открой этого бота в MAX, нажми «Старт» и отправь ему код из строки выше."
  echo "Потом добавь бота админом в канал MAX."
else
  echo "❌ Бот не запустился. Последние строки:"
  tail -n 15 "$DIR/bot.log" 2>/dev/null || journalctl -u "tg_to_max@$NAME" -n 15 --no-pager
fi
