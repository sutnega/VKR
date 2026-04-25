#!/usr/bin/env python3
import argparse
import json
import os
import re
from collections import Counter, defaultdict
from typing import List, Dict, Any


# stop-слова
STOPWORDS = {
    # русские
    "и", "в", "во", "на", "по", "из", "от", "до", "за", "для", "над", "под",
    "о", "об", "про", "при", "без", "что", "это", "как", "так", "к", "ко",
    "же", "у", "не", "но", "с", "со", "а", "или", "бы", "мы", "вы", "они",
    "он", "она", "оно", "их", "наш", "ваш", "та", "тот", "эта", "этот",
    "такой", "такое", "такая", "также", "же", "еще", "уже",
    # английские
    "the", "and", "of", "in", "on", "for", "to", "a", "an",
}


WORD_RE = re.compile(r"[A-Za-zА-Яа-яёЁ]+")


def load_items(store_path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(store_path):
        raise SystemExit(f"Файл хранилища не найден: {store_path}")
    with open(store_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("items", [])


def tokenize(text: str) -> List[str]:
    words = WORD_RE.findall(text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 2]


def build_global_freq(items: List[Dict[str, Any]]) -> Counter:
    cnt = Counter()
    for it in items:
        parts = [
            it.get("title") or "",
            it.get("summary") or "",
        ]
        text = " ".join(parts)
        tokens = tokenize(text)
        cnt.update(tokens)
    return cnt


def build_freq_by_source(items: List[Dict[str, Any]]) -> Dict[str, Counter]:
    by_source: Dict[str, Counter] = defaultdict(Counter)
    for it in items:
        src = it.get("source", "unknown")
        text = (it.get("title") or "") + " " + (it.get("summary") or "")
        tokens = tokenize(text)
        by_source[src].update(tokens)
    return by_source


def print_top(counter: Counter, top_n: int, header: str) -> None:
    print("=" * 80)
    print(header)
    print("=" * 80)
    for word, freq in counter.most_common(top_n):
        print(f"{word:20s} {freq:5d}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Лёгкая аналитика по словам в новостях (частотные списки)"
    )
    parser.add_argument(
        "--store",
        default="news_store.json",
        help="Путь к JSON-хранилищу новостей (по умолчанию news_store.json)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=30,
        help="Сколько наиболее частых слов показывать (по умолчанию 30)",
    )
    parser.add_argument(
        "--by-source",
        action="store_true",
        help="Печатать отдельный топ слов для каждого источника",
    )
    args = parser.parse_args()

    items = load_items(args.store)
    print(f"[analyze] Загружено элементов: {len(items)}")

    global_freq = build_global_freq(items)
    print_top(global_freq, args.top, "Глобальный топ слов по всем источникам")

    if args.by_source:
        by_src = build_freq_by_source(items)
        for src, cnt in sorted(by_src.items(), key=lambda x: x[0]):
            print_top(cnt, args.top, f"Топ слов для источника: {src}")


if __name__ == "__main__":
    main()

"""# просто общий топ слов
python analyze_words.py

# топ-50 слов
python analyze_words.py --top 50

# общий топ + по каждому источнику отдельно
python analyze_words.py --by-source --top 20"""