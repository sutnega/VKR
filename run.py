#!/usr/bin/env python3
"""
run.py — единая точка запуска всех модулей проекта.

Просто запусти:  python run.py
И выбери нужное действие из меню.
"""

import os
import subprocess
import sys


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
    print(f"    {GREEN('1')} gemini  — Google Gemini API {YELLOW('(бесплатно, нужен ключ GEMINI_API_KEY)')}")
    print(f"    {GREEN('2')} groq    — Groq API          {YELLOW('(бесплатно, нужен ключ GROQ_API_KEY)')}")
    print(f"    {GREEN('3')} ollama  — локальная модель  {GREEN('(полностью бесплатно, без ключа)')}")
    print()
    choice = ask("Выберите провайдер (1/2/3)", "3")
    provider_map = {"1": "gemini", "2": "groq", "3": "ollama"}
    provider = provider_map.get(choice, "ollama")

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


def menu_app() -> None:
    print()
    print(BOLD("  🌐 Запуск веб-интерфейса (Streamlit)"))
    print(GRAY("  Откроет браузер с аналитическим дашбордом."))
    print(GRAY("  Для остановки нажмите Ctrl+C в этом окне."))
    print()
    app_file = ask("Файл приложения", "news_app.py")
    port     = ask("Порт", "8501")
    run([sys.executable, "-m", "streamlit", "run", app_file, "--server.port", port])
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
    provider = ask("Провайдер AI (gemini/groq/ollama)", "ollama")
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

MENU_ITEMS = [
    ("1", "📥  Собрать новости",                    menu_collect),
    ("2", "📋  Показать список новостей",            menu_list),
    ("3", "💾  Экспортировать новости",              menu_export),
    ("4", "🤖  AI-резюмирование",                   menu_summarize),
    ("5", "📊  Частотный анализ слов",               menu_analyze),
    ("6", "🌐  Запустить веб-дашборд",               menu_app),
    ("7", "🔄  Полный цикл (сбор → резюме → дашборд)", menu_full_pipeline),
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