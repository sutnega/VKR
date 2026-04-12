#!/usr/bin/env python3

"""
summarize.py — модуль автоматического резюмирования новостей.

Поддерживает три провайдера (переключение через --provider):
  • gemini   — Google Gemini API (бесплатно, 1500 запросов/день)
  • groq     — Groq API (бесплатно, ~14 400 запросов/день)
  • ollama   — локальная модель без интернета (полностью бесплатно)

Быстрый старт:
    # Gemini (рекомендуется — бесплатно)
    export GEMINI_API_KEY="AIza..."
    python summarize.py --provider gemini

    # Groq (бесплатно)
    export GROQ_API_KEY="gsk_..."
    python summarize.py --provider groq

    # Ollama (локально, без ключа)
    python summarize.py --provider ollama

Дополнительные флаги:
    --limit 20       не более 20 новостей за запуск
    --force          перезаписать уже существующие резюме
    --dry-run        показать список без вызовов API
    --store path     путь к хранилищу (по умолчанию news_store.json)
    --model name     переопределить модель провайдера
"""

import argparse
import json
import os
import time
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Системный промпт (общий для всех провайдеров)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "Ты — аналитический ассистент, специализирующийся на новостях "
    "целлюлозно-бумажной и упаковочной промышленности. "
    "Твоя задача — кратко изложить суть новости в 2–3 предложениях на русском языке. "
    "Пиши нейтрально и по существу: укажи ключевое событие, участников (если есть) "
    "и его значение для отрасли. Не используй вводные фразы вроде «В данной новости...»."
)

# Минимальная длина резюме — если короче, считается отсутствующим
MIN_SUMMARY_LENGTH = 80

# Пауза между запросами (сек) — защита от превышения rate limit
REQUEST_DELAY = 1.0

# Таймаут HTTP-запроса (сек)
REQUEST_TIMEOUT = 30

# ---------------------------------------------------------------------------
# Конфигурация провайдеров
# ---------------------------------------------------------------------------

PROVIDER_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "gemini": {
        "model": "gemini-1.5-flash",
        "api_key_env": "GEMINI_API_KEY",
        "description": "Google Gemini API — бесплатно, 1500 запросов/день",
    },
    "groq": {
        "model": "llama3-8b-8192",
        "api_key_env": "GROQ_API_KEY",
        "description": "Groq API — бесплатно, ~14 400 запросов/день",
    },
    "ollama": {
        "model": "llama3",
        "api_key_env": None,  # ключ не нужен
        "description": "Ollama — локальная модель, полностью бесплатно и офлайн",
    },
}

# ---------------------------------------------------------------------------
# Хранилище (повторяет логику из main.py)
# ---------------------------------------------------------------------------

def load_store(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise SystemExit(f"[summarize] Файл хранилища не найден: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_store(path: str, store: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# HTTP-хелпер (без сторонних библиотек)
# ---------------------------------------------------------------------------

def http_post(url: str, payload: Dict, headers: Dict) -> Dict:
    import urllib.request
    import urllib.error

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {e.code}: {body[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Сетевая ошибка: {e}")


# ---------------------------------------------------------------------------
# Провайдеры
# ---------------------------------------------------------------------------

def call_gemini(title: str, summary: Optional[str], api_key: str, model: str) -> str:
    """
    Google Gemini API.
    Gemini не разделяет system/user сообщения в базовом формате,
    поэтому объединяем промпт и текст в одно поле.
    Документация: https://ai.google.dev/api/generate-content
    """
    user_text = _build_user_text(title, summary)
    combined = f"{SYSTEM_PROMPT}\n\n{user_text}"

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = {
        "contents": [{"parts": [{"text": combined}]}],
        "generationConfig": {"maxOutputTokens": 300, "temperature": 0.3},
    }
    result = http_post(url, payload, {"Content-Type": "application/json"})
    return result["candidates"][0]["content"]["parts"][0]["text"].strip()


def call_groq(title: str, summary: Optional[str], api_key: str, model: str) -> str:
    """
    Groq API — полностью совместим с форматом OpenAI Chat Completions.
    Документация: https://console.groq.com/docs/openai
    """
    return _call_openai_compatible(
        url="https://api.groq.com/openai/v1/chat/completions",
        title=title,
        summary=summary,
        api_key=api_key,
        model=model,
    )


def call_ollama(title: str, summary: Optional[str], model: str) -> str:
    """
    Ollama — локальный сервер на порту 11434.
    Перед запуском: ollama serve (в отдельном терминале)
    Установка модели: ollama pull llama3
    Документация: https://github.com/ollama/ollama/blob/main/docs/api.md
    """
    user_text = _build_user_text(title, summary)
    payload = {
        "model": model,
        "prompt": f"{SYSTEM_PROMPT}\n\n{user_text}",
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 300},
    }
    result = http_post(
        "http://localhost:11434/api/generate",
        payload,
        {"Content-Type": "application/json"},
    )
    return result["response"].strip()


# ---------------------------------------------------------------------------
# Внутренние хелперы
# ---------------------------------------------------------------------------

def _build_user_text(title: str, summary: Optional[str]) -> str:
    """Формирует текст пользовательского сообщения из заголовка и аннотации."""
    if summary and len(summary.strip()) > 20:
        return f"Заголовок: {title}\n\nТекст: {summary}"
    return f"Заголовок: {title}"


def _call_openai_compatible(
    url: str, title: str, summary: Optional[str], api_key: str, model: str
) -> str:
    """Общая логика для OpenAI-совместимых API (OpenAI и Groq используют один формат)."""
    payload = {
        "model": model,
        "max_tokens": 300,
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_text(title, summary)},
        ],
    }
    result = http_post(
        url,
        payload,
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    return result["choices"][0]["message"]["content"].strip()


# ---------------------------------------------------------------------------
# Диспетчер вызовов
# ---------------------------------------------------------------------------

def call_provider(
    provider: str,
    title: str,
    summary: Optional[str],
    api_key: Optional[str],
    model: str,
) -> Optional[str]:
    """Вызывает нужный провайдер и возвращает резюме (или None при ошибке)."""
    try:
        if provider == "gemini":
            return call_gemini(title, summary, api_key, model)
        elif provider == "groq":
            return call_groq(title, summary, api_key, model)
        elif provider == "ollama":
            return call_ollama(title, summary, model)
    except RuntimeError as e:
        print(f"             ✗ Ошибка API: {e}")
    except (KeyError, IndexError) as e:
        print(f"             ✗ Неожиданный формат ответа: {e}")
    return None


# ---------------------------------------------------------------------------
# Основная логика
# ---------------------------------------------------------------------------

def needs_summary(item: Dict[str, Any], force: bool) -> bool:
    """Возвращает True, если новость нуждается в резюмировании."""
    if force:
        return True
    s = item.get("summary") or ""
    return len(s.strip()) < MIN_SUMMARY_LENGTH


def run(
    provider: str,
    store_path: str,
    limit: int,
    force: bool,
    dry_run: bool,
    model: Optional[str],
) -> None:
    cfg = PROVIDER_DEFAULTS[provider]
    active_model = model or cfg["model"]

    # Получаем API-ключ (для ollama не нужен)
    api_key: Optional[str] = None
    if cfg["api_key_env"]:
        api_key = os.environ.get(cfg["api_key_env"], "")
        if not dry_run and not api_key:
            raise SystemExit(
                f"[summarize] Не задан {cfg['api_key_env']}.\n"
                f"Установите: export {cfg['api_key_env']}='ваш-ключ'"
            )

    print(f"[summarize] Провайдер : {provider} — {cfg['description']}")
    print(f"[summarize] Модель    : {active_model}")
    print(f"[summarize] Хранилище: {store_path}")

    store = load_store(store_path)
    items: List[Dict[str, Any]] = store.get("items", [])
    to_process = [it for it in items if needs_summary(it, force)]

    print(f"[summarize] Всего новостей     : {len(items)}")
    print(f"[summarize] Нуждаются в резюме : {len(to_process)}")

    if limit > 0:
        to_process = to_process[:limit]
        print(f"[summarize] Лимит --limit      : {limit}")

    if not to_process:
        print("[summarize] Нечего обрабатывать. Завершение.")
        return

    if dry_run:
        print("\n[summarize] Режим --dry-run. Будут обработаны:")
        for i, it in enumerate(to_process, 1):
            print(f"  {i:3d}. [{it.get('source', '')}] {(it.get('title') or '')[:75]}")
        return

    # Индекс id → запись для быстрого обновления
    id_index: Dict[str, Dict[str, Any]] = {it["id"]: it for it in items}

    processed = 0
    errors = 0

    for i, it in enumerate(to_process, 1):
        title = it.get("title") or ""
        original = it.get("summary")
        source = it.get("source", "")

        print(f"\n[summarize] ({i}/{len(to_process)}) [{source}]")
        print(f"             {title[:80]}")

        new_summary = call_provider(provider, title, original, api_key, active_model)

        if new_summary:
            id_index[it["id"]]["summary"] = new_summary
            processed += 1
            print(f"             ✓ {len(new_summary)} символов")
            # Сохраняем сразу — чтобы не потерять прогресс при обрыве
            save_store(store_path, store)
        else:
            errors += 1

        if i < len(to_process):
            time.sleep(REQUEST_DELAY)

    print(f"\n[summarize] Готово. Обработано: {processed}, ошибок: {errors}")
    print(f"[summarize] Хранилище обновлено: {store_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    provider_help = "\n".join(
        f"  {k:8s} — {v['description']}"
        for k, v in PROVIDER_DEFAULTS.items()
    )
    parser = argparse.ArgumentParser(
        description="Резюмирование новостей через LLM (Gemini / Groq / Ollama)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Доступные провайдеры:\n{provider_help}",
    )
    parser.add_argument(
        "--provider",
        choices=list(PROVIDER_DEFAULTS.keys()),
        default="gemini",
        help="Провайдер LLM (по умолчанию: gemini)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Переопределить модель провайдера",
    )
    parser.add_argument(
        "--store",
        default="news_store.json",
        help="Путь к JSON-хранилищу новостей (по умолчанию: news_store.json)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Максимум новостей за запуск (0 = без ограничений)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Перезаписать существующие резюме",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Показать список без реальных запросов к API",
    )

    args = parser.parse_args()
    run(
        provider=args.provider,
        store_path=args.store,
        limit=args.limit,
        force=args.force,
        dry_run=args.dry_run,
        model=args.model,
    )


if __name__ == "__main__":
    main()
