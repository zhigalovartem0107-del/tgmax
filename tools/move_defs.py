"""Переносит функции/классы верхнего уровня из tg_to_max.py в модуль пакета reloader — без изменений тела.

python3 tools/move_defs.py reloader/core/timeutil.py "Описание модуля" "import re,import time" n_word fmt_min ...

Что делает: вырезает блоки (с декораторами) из tg_to_max.py, дописывает их в модуль (создаёт с docstring и
импортами), добавляет в tg_to_max.py `from reloader.x import имена` (старые вызовы работают) и регистрирует модуль
в фасаде (_MOVED), чтобы bot.X = подделка в тестах доходила до нового места.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "tg_to_max.py")


def block(lines, name):
    """(начало, конец) блока def/class name верхнего уровня, вместе с декораторами (границы — по ast)."""
    import ast
    tree = ast.parse("\n".join(lines))
    node = next((n for n in tree.body if (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                 and n.name == name) or (isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == name for t in n.targets))), None)
    if node is None:
        sys.exit(f"не нашёл {name}")
    start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])]) - 1
    return start, node.end_lineno


def undefined(text):
    """Имена, которые модуль использует, но нигде не получает (ни def, ни import, ни присваивание, ни builtins)."""
    import ast
    import builtins
    tree = ast.parse(text)
    have = set(dir(builtins))
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            have.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            have |= {a.asname or a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load):
            have.add(n.id)
        elif isinstance(n, ast.arg):
            have.add(n.arg)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return sorted(used - have)


def main(dst, doc, imports, names):
    lines = open(SRC, encoding="utf-8").read().split("\n")
    parts = []
    for name in names:
        i, j = block(lines, name)
        parts.append("\n".join(lines[i:j]))
        del lines[i:j]
        while i < len(lines) and i > 0 and not lines[i].strip() and not lines[i - 1].strip():
            del lines[i]
    mod = os.path.join(ROOT, dst)
    os.makedirs(os.path.dirname(mod), exist_ok=True)
    if os.path.exists(mod) and open(mod, encoding="utf-8").read().strip().count("\n") > 0:
        text = open(mod, encoding="utf-8").read().rstrip("\n") + "\n\n\n" + "\n\n\n".join(parts) + "\n"
    else:
        imp = "\n".join(x.strip() for x in imports.split(",") if x.strip())
        text = f'"""{doc}"""\n' + (imp + "\n" if imp else "") + "\n\n" + "\n\n\n".join(parts) + "\n"
    missing = undefined(text)
    if missing:
        sys.exit(f"не переношу: в {dst} останутся неизвестные имена {missing} — добавь их в перенос")
    open(mod, "w", encoding="utf-8").write(text)
    dotted = dst[:-3].replace("/", ".")
    alias = "_" + dotted.split(".")[-1]
    src = "\n".join(lines)
    anchor = "from reloader.core import log as _log\n"
    add = ""
    if f"import {dotted.split('.')[-1]} as {alias}\n" not in src:
        add += f"from {dotted.rsplit('.', 1)[0]} import {dotted.split('.')[-1]} as {alias}\n"
        src = re.sub(r"^_MOVED = \[(.*)\]", lambda m: f"_MOVED = [{m.group(1)}, {alias}]", src, count=1, flags=re.M)
    add += f"from {dotted} import {', '.join(names)}  # noqa: E402\n"
    src = src.replace(anchor, anchor + add, 1)
    open(SRC, "w", encoding="utf-8").write(src)
    print(f"{dst}: перенесено {len(names)} — {', '.join(names)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4:])
