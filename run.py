#!/usr/bin/env python3
"""
run.py — единая точка запуска всех модулей проекта.

Просто запусти:  python run.py
И выбери нужное действие из меню.
"""

import os
import subprocess
import sys


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()


# ─────────────────────────────────────────────
# Цвета для терминала
# ─────────────────────────────────────────────

def c(text, code): return f"\033[{code}m{text}\033[0m"
BLUE   = lambda t: c(t, "94")
GREEN  = lambda t: c(t, "92")
YELLOW = lambda t: c(t, "93")
RED    = lambda t: c(t, "91")
BOLD   = lambda t: c(t, "1")
GRAY   = lambda t: c(t, "90")


# ─────────────────────────────────────────────
# Запуск команды
# ─────────────────────────────────────────────

def run(cmd: list[str]) -> None:
    """Запускает команду и выводит результат в реальном времени."""
    print()
    print(GRAY("─" * 60))
    print(GRAY(f"$ {' '.join(cmd)}"))
    print(GRAY("─" * 60))
    result = subprocess.run(cmd)
    print(GRAY("─" * 60))
    if result.returncode == 0:
        print(GREEN("✓ Выполнено успешно"))
    else:
        print(RED(f"✗ Завершено с ошибкой (код {result.returncode})"))


def ask(prompt: str, default: str = "") -> str:
    """Запрашивает ввод с подсказкой и дефолтным значением."""
    hint = f" [{default}]" if default else ""
    val = input(f"  {prompt}{hint}: ").strip()
    return val if val else default


def ask_int(prompt: str, default: int) -> int:
    val = ask(prompt, str(default))
    try:
        return int(val)
    except ValueError:
        print(RED(f"  Неверное число, используем {default}"))
        return default


def pause() -> None:
    input(GRAY("\n  Нажмите Enter чтобы вернуться в меню..."))


# ─────────────────────────────────────────────
# Разделы меню
# ─────────────────────────────────────────────

def menu_collect() -> None:
    print()
    print(BOLD("  📥 Сбор новостей"))
    print(GRAY("  Загружает новости из всех источников и сохраняет в хранилище."))
    print()
    max_age = ask_int("Максимальный возраст новостей в днях (−1 = без ограничений)", 365)
    store   = ask("Файл хранилища", "news_store.json")
    run([sys.executable, "main.py", "--store", store, "collect", "--max-age-days", str(max_age)])
    pause()


def menu_list() -> None:
    print()
    print(BOLD("  📋 Список новостей в хранилище"))
    print()
    store   = ask("Файл хранилища", "news_store.json")
    verbose = ask("Показывать аннотации? (y/n)", "n").lower() == "y"
    cmd = [sys.executable, "main.py", "--store", store, "list"]
    if verbose:
        cmd.append("-v")
    run(cmd)
    pause()


def menu_export() -> None:
    print()
    print(BOLD("  💾 Экспорт новостей"))
    print()
    fmt   = ask("Формат (json / csv / xlsx)", "xlsx")
    out   = ask("Имя файла для сохранения", f"news_export.{fmt}")
    store = ask("Файл хранилища", "news_store.json")
    run([sys.executable, "main.py", "--store", store, "export", "--format", fmt, "--output", out])
    pause()


def menu_summarize() -> None:
    print()
    print(BOLD("  🤖 AI-резюмирование новостей"))
    print()
    print(f"  Провайдеры:")
    print(f"    {GREEN('1')} gigachat — GigaChat (Сбер)   {GREEN('(бесплатно, лучший русский, нужен ключ GIGACHAT_CREDENTIALS)')}")
    print(f"    {GREEN('2')} gemini   — Google Gemini API  {YELLOW('(бесплатно, нужен ключ GEMINI_API_KEY)')}")
    print(f"    {GREEN('3')} groq     — Groq API           {YELLOW('(бесплатно, нужен ключ GROQ_API_KEY)')}")
    print(f"    {GREEN('4')} ollama   — локальная модель   {GREEN('(полностью бесплатно, без ключа)')}")
    print()
    choice = ask("Выберите провайдер (1/2/3/4)", "1")
    provider_map = {"1": "gigachat", "2": "gemini", "3": "groq", "4": "ollama"}
    provider = provider_map.get(choice, "gigachat")

    # Проверяем ключи и предлагаем ввести если не заданы
    key_env_map = {
        "gigachat": "GIGACHAT_CREDENTIALS",
        "gemini":   "GEMINI_API_KEY",
        "groq":     "GROQ_API_KEY",
    }
    if provider in key_env_map:
        env_var = key_env_map[provider]
        if not os.environ.get(env_var):
            print()
            print(YELLOW(f"  Переменная {env_var} не задана."))
            key_val = ask(f"Введите ключ (или Enter чтобы пропустить)", "")
            if key_val:
                os.environ[env_var] = key_val
                print(GREEN(f"  ✓ {env_var} установлен на время сессии"))
            else:
                print(RED("  Ключ не введён — запуск может завершиться ошибкой."))

    limit = ask_int("Сколько новостей обработать за раз (0 = все)", 0)
    store = ask("Файл хранилища", "news_store.json")

    print()
    print(f"  Дополнительные опции:")
    fix_trunc = ask("Перегенерировать обрезанные резюме? (y/n)", "y").lower() == "y"
    force     = ask("Перезаписать ВСЕ резюме заново? (y/n)", "n").lower() == "y"
    dry_run   = ask("Режим просмотра без запросов к API? (y/n)", "n").lower() == "y"

    cmd = [sys.executable, "summarize.py", "--provider", provider, "--store", store]
    if limit > 0:
        cmd += ["--limit", str(limit)]
    if fix_trunc:
        cmd.append("--fix-truncated")
    if force:
        cmd.append("--force")
    if dry_run:
        cmd.append("--dry-run")

    run(cmd)
    pause()


def _find_free_port(start: int = 8501) -> int:
    """Ищет свободный порт начиная с start."""
    import socket
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start  # fallback


def _kill_port(port: int) -> None:
    """Завершает процесс занимающий порт (Windows/Linux)."""
    import socket
    # Проверяем что порт реально занят
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", port)) != 0:
            return  # порт свободен
    if os.name == "nt":
        # Windows: найти PID через netstat и убить
        import subprocess as sp
        result = sp.run(
            ["netstat", "-ano"],
            capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            if f":{port}" in line and "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                sp.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
                print(GREEN(f"  ✓ Завершён процесс PID {pid} на порту {port}"))
                return
    else:
        import subprocess as sp
        sp.run(["fuser", "-k", f"{port}/tcp"], capture_output=True)
        print(GREEN(f"  ✓ Завершён процесс на порту {port}"))


def menu_app() -> None:
    print()
    print(BOLD("  🌐 Запуск веб-интерфейса (Streamlit)"))
    print(GRAY("  Откроет браузер с аналитическим дашбордом."))
    print(GRAY("  Для остановки нажмите Ctrl+C в этом окне."))
    print()
    app_file = ask("Файл приложения", "news_app.py")
    port_str = ask("Порт", "8501")
    port = int(port_str)

    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        port_busy = s.connect_ex(("127.0.0.1", port)) == 0

    if port_busy:
        print()
        print(YELLOW(f"  ⚠ Порт {port} уже занят. Завершаю старый процесс..."))
        _kill_port(port)
        import time; time.sleep(1)

    run([sys.executable, "-m", "streamlit", "run", app_file, "--server.port", str(port)])
    pause()


def menu_analyze() -> None:
    print()
    print(BOLD("  📊 Частотный анализ слов"))
    print()
    store  = ask("Файл хранилища", "news_store.json")
    top    = ask_int("Топ N слов", 30)
    by_src = ask("Разбить по источникам? (y/n)", "n").lower() == "y"
    cmd = [sys.executable, "analyze_words.py", "--store", store, "--top", str(top)]
    if by_src:
        cmd.append("--by-source")
    run(cmd)
    pause()


def menu_full_pipeline() -> None:
    """Полный цикл: собрать → резюмировать → открыть дашборд."""
    print()
    print(BOLD("  🔄 Полный цикл: сбор → резюме → дашборд"))
    print(GRAY("  Запустит все этапы последовательно."))
    print()
    store    = ask("Файл хранилища", "news_store.json")
    max_age  = ask_int("Максимальный возраст новостей (дней)", 365)
    provider = ask("Провайдер AI (gigachat/gemini/groq/ollama)", "gigachat")
    limit    = ask_int("Лимит резюме за раз (0 = все)", 20)

    print()
    print(YELLOW("  Шаг 1/3: Сбор новостей..."))
    run([sys.executable, "main.py", "--store", store, "collect", "--max-age-days", str(max_age)])

    print()
    print(YELLOW("  Шаг 2/3: Резюмирование..."))
    cmd = [sys.executable, "summarize.py", "--provider", provider, "--store", store, "--fix-truncated"]
    if limit > 0:
        cmd += ["--limit", str(limit)]
    run(cmd)

    print()
    print(YELLOW("  Шаг 3/3: Запуск дашборда..."))
    print(GRAY("  (Ctrl+C чтобы остановить)"))
    run([sys.executable, "-m", "streamlit", "run", "news_app.py"])
    pause()


# ─────────────────────────────────────────────
# Главное меню
# ─────────────────────────────────────────────


def menu_clean_store() -> None:
    print()
    print(BOLD("  🧹 Очистка HTML из хранилища"))
    print(GRAY("  Убирает HTML-теги из полей summary (актуально для RSS-источников)."))
    print()
    store = ask("Файл хранилища", "news_store.json")

    import re
    from html import unescape

    def strip_html(text):
        if not text:
            return text
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        text = unescape(text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    def looks_like_html(text):
        return bool(re.search(r"<[a-zA-Z][^>]*>", text or ""))

    import json, os
    if not os.path.exists(store):
        print(RED(f"  Файл не найден: {store}"))
        pause()
        return

    with open(store, "r", encoding="utf-8") as f:
        data = json.load(f)

    items = data.get("items", [])
    cleaned = 0
    for item in items:
        summary = item.get("summary") or ""
        if looks_like_html(summary):
            clean = strip_html(summary)
            if len(clean) > 600:
                clean = clean[:600] + "…"
            item["summary"] = clean
            cleaned += 1

    if cleaned == 0:
        print(GREEN("  ✓ HTML не найден — хранилище уже чистое."))
    else:
        tmp = store + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, store)
        print(GREEN(f"  ✓ Очищено записей: {cleaned} из {len(items)}"))

    pause()

MENU_ITEMS = [
    ("1", "📥  Собрать новости",                    menu_collect),
    ("2", "📋  Показать список новостей",            menu_list),
    ("3", "💾  Экспортировать новости",              menu_export),
    ("4", "🤖  AI-резюмирование",                   menu_summarize),
    ("5", "📊  Частотный анализ слов",               menu_analyze),
    ("6", "🌐  Запустить веб-дашборд",               menu_app),
    ("7", "🧹  Очистить HTML в хранилище",             menu_clean_store),
    ("8", "🔄  Полный цикл (сбор → резюме → дашборд)", menu_full_pipeline),
    ("0", "❌  Выход",                               None),
]


def print_menu() -> None:
    os.system("cls" if os.name == "nt" else "clear")
    print()
    print(BOLD(BLUE("  ╔═══════════════════════════════════════╗")))
    print(BOLD(BLUE("  ║   Система мониторинга новостей ЦБП    ║")))
    print(BOLD(BLUE("  ╚═══════════════════════════════════════╝")))
    print()
    for key, label, _ in MENU_ITEMS:
        if key == "0":
            print()
        color = RED if key == "0" else GREEN
        print(f"  {color(BOLD(key))}  {label}")
    print()


def main() -> None:
    # Убеждаемся что запускаемся из нужной директории
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    while True:
        print_menu()
        choice = input(BOLD("  Выберите действие: ")).strip()

        handler = None
        for key, _, fn in MENU_ITEMS:
            if choice == key:
                handler = fn
                break

        if handler is None:
            print(RED("  Неверный выбор, попробуйте снова."))
            import time; time.sleep(1)
            continue

        if choice == "0":
            print(GREEN("\n  До свидания!\n"))
            break

        handler()


if __name__ == "__main__":
    main()
