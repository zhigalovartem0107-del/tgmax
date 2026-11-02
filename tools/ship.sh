#!/bin/sh
# Коммит и push ТОЛЬКО если все наборы тестов зелёные: sh tools/ship.sh "сообщение коммита"
cd "$(dirname "$0")/.." || exit 1
out=$(sh run_tests.sh 2>&1)
echo "$out" | grep -E "ok$|FAIL|py3.11" | tr '\n' ' '; echo
if echo "$out" | grep -q FAIL || ! echo "$out" | grep -q "py3.11 syntax ok"; then
  echo "НЕ ОТПРАВЛЕНО: есть красные тесты"; exit 1
fi
git add -A && git -c user.name="Claude" -c user.email="noreply@anthropic.com" commit -q -m "$1

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_017TGyxGyQ7evw4CVszy6aEj" && git push -q origin main && git log --oneline | head -1 && git status -sb | head -1
