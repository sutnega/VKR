import argparse
import csv
import dataclasses
import datetime as dt
import email.utils
import json
import os
import re
from typing import List, Dict, Any, Optional
from urllib.request import urlopen, Request
from xml.etree import ElementTree as ET

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Safari/537.36"
)

# Источники новостей
SOURCES = {
    "sbo-paper": {
        "type": "rss",
        "url": "https://sbo-paper.ru/rss",
        "keywords": None,  # без фильтра
    },
    "rbc": {
        "type": "rss",
        "url": "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",
        "keywords": None,  # <-- ВАЖНО: убираем список ключевых слов
    },
}


@dataclasses.dataclass
class NewsItem:
    id: str
    source: str
    title: str
    url: str
    published: str  # ISO 8601
    summary: str
    collected_at: str  # ISO 8601

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


class JsonNewsStore:
    def __init__(self, path: str):
        self.path = path
        self._data: Dict[str, Any] = {"items": []}
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            self._data = {"items": []}
            return
        with open(self.path, "r", encoding="utf-8") as f:
            try:
                self._data = json.load(f)
            except json.JSONDecodeError:
                self._data = {"items": []}
        if "items" not in self._data or not isinstance(self._data["items"], list):
            self._data = {"items": []}

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    @property
    def items(self) -> List[Dict[str, Any]]:
        return self._data["items"]

    def add_items(self, new_items: List[NewsItem]) -> int:
        # не дублируем по (source, url)
        existing_keys = {(it["source"], it["url"]) for it in self.items}
        added = 0
        for item in new_items:
            key = (item.source, item.url)
            if key in existing_keys:
                continue
            self.items.append(item.to_dict())
            existing_keys.add(key)
            added += 1
        if added:
            self._save()
        return added


def _http_get(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        return resp.read()


def _parse_rss_datetime(raw: str) -> Optional[dt.datetime]:
    if not raw:
        return None
    # большинство RSS используют RFC822
    try:
        d = email.utils.parsedate_to_datetime(raw)
        if d is None:
            return None
        # нормализуем в UTC
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        else:
            d = d.astimezone(dt.timezone.utc)
        return d
    except Exception:
        return None


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def fetch_rss_source(name: str, cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    url = cfg["url"]
    print(f"[collector] Читаю RSS '{name}' из {url} ...")
    try:
        raw = _http_get(url)
    except Exception as e:
        print(f"[collector]   ошибка при чтении {url}: {e}")
        return []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        print(f"[collector]   ошибка парсинга XML: {e}")
        return []

    items = []
    # обычно структура: <rss><channel><item>...</item></channel></rss>
    for item_el in root.findall(".//item"):
        title = (item_el.findtext("title") or "").strip()
        link = (item_el.findtext("link") or "").strip()
        pub_raw = (item_el.findtext("pubDate") or "").strip()
        desc_raw = (item_el.findtext("description") or "").strip()

        if not title and not link:
            continue

        pub_dt = _parse_rss_datetime(pub_raw)
        items.append(
            {
                "source": name,
                "title": title,
                "url": link,
                "published_dt": pub_dt,
                "published_raw": pub_raw,
                "summary_raw": desc_raw,
            }
        )

    print(f"[collector]   найдено элементов: {len(items)}")
    # фильтр по ключевым словам (если они заданы)
    keywords = cfg.get("keywords")
    if keywords:
        kw_lower = [k.lower() for k in keywords]
        filtered = []
        for it in items:
            text = (it["title"] + " " + _strip_html(it["summary_raw"])).lower()
            if any(k in text for k in kw_lower):
                filtered.append(it)
        print(f"[collector]   после фильтра по ключевым словам: {len(filtered)}")
        items = filtered

    return items


def collect_news(max_age_days: int) -> List[NewsItem]:
    now = dt.datetime.now(dt.timezone.utc)
    now_iso = now.isoformat()
    all_raw: List[Dict[str, Any]] = []

    for name, cfg in SOURCES.items():
        if cfg.get("type") == "rss":
            items = fetch_rss_source(name, cfg)
        else:
            print(f"[collector]   неизвестный тип источника: {cfg.get('type')}")
            items = []
        all_raw.extend(items)

    print(f"[service] Всего собрали от всех источников: {len(all_raw)}")

    # фильтр по дате
    if max_age_days > 0:
        cutoff = now - dt.timedelta(days=max_age_days)
        filtered = []
        for it in all_raw:
            pub_dt = it.get("published_dt")
            if pub_dt is None:
                # без даты считаем "старьём" и выкидываем
                continue
            if pub_dt >= cutoff:
                filtered.append(it)
        print(
            f"[service] Фильтр по дате: оставлено {len(filtered)} "
            f"из {len(all_raw)} (max_age_days={max_age_days})"
        )
        if not filtered:
            print(
                "[service] Все новости оказались старше порога "
                "или без корректной даты. "
                "Можешь указать --max-age-days 0, чтобы убрать фильтр по дате."
            )
        all_raw = filtered
    else:
        print("[service] Фильтр по дате отключён (--max-age-days 0)")

    # преобразуем в NewsItem
    news_items: List[NewsItem] = []
    for idx, it in enumerate(all_raw, start=1):
        pub_dt = it.get("published_dt")
        if pub_dt is None:
            published_iso = now_iso
        else:
            published_iso = pub_dt.isoformat()

        # формируем простой id: source + порядковый номер + timestamp
        nid = f"{it['source']}:{int(now.timestamp())}:{idx}"

        news_items.append(
            NewsItem(
                id=nid,
                source=it["source"],
                title=it["title"],
                url=it["url"],
                published=published_iso,
                summary=_strip_html(it["summary_raw"]),
                collected_at=now_iso,
            )
        )

    return news_items


def cmd_collect(args: argparse.Namespace) -> None:
    store = JsonNewsStore(args.store)
    items = collect_news(args.max_age_days)
    added = store.add_items(items)
    print(f"[main] Добавлено новостей: {added}")


def cmd_list(args: argparse.Namespace) -> None:
    store = JsonNewsStore(args.store)
    items = store.items
    if not items:
        print("[main] Хранилище пустое.")
        return

    # сортируем по дате публикации (новые сверху)
    def parse_iso(s: str) -> dt.datetime:
        try:
            d = dt.datetime.fromisoformat(s)
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
            return d
        except Exception:
            return dt.datetime.min.replace(tzinfo=dt.timezone.utc)

    items_sorted = sorted(items, key=lambda it: parse_iso(it["published"]), reverse=True)

    limit = args.limit
    if limit and limit > 0:
        to_show = items_sorted[:limit]
    else:
        to_show = items_sorted

    for i, it in enumerate(to_show, start=1):
        print(
            f"{i:4d}. [{it['source']}] {it['published']} — {it['title']}\n"
            f"      {it['url']}"
        )


def cmd_export(args: argparse.Namespace) -> None:
    store = JsonNewsStore(args.store)
    items = store.items
    if not items:
        print("[main] Хранилище пустое, экспортировать нечего.")
        return

    path = args.output
    fieldnames = ["id", "source", "published", "title", "url", "summary", "collected_at"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for it in items:
            row = {k: it.get(k, "") for k in fieldnames}
            writer.writerow(row)

    print(f"[main] Экспортировано {len(items)} записей в {path}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Простой агрегатор новостей ЦБП/упаковки (JSON-хранилище)"
    )
    parser.add_argument(
        "--store",
        default="news_store.json",
        help="Путь к JSON-хранилищу (по умолчанию: news_store.json)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_collect = subparsers.add_parser("collect", help="Собрать новости из источников")
    p_collect.add_argument(
        "--max-age-days",
        type=int,
        default=365,
        help=(
            "Максимальный возраст новости в днях. "
            "0 = не фильтровать по дате (по умолчанию: 365)"
        ),
    )
    p_collect.set_defaults(func=cmd_collect)

    p_list = subparsers.add_parser("list", help="Посмотреть, что лежит в хранилище")
    p_list.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Сколько последних новостей показать (по умолчанию: 30, 0 = все)",
    )
    p_list.set_defaults(func=cmd_list)

    p_export = subparsers.add_parser(
        "export", help="Экспортировать все новости в CSV"
    )
    p_export.add_argument(
        "--output",
        "-o",
        required=True,
        help="Путь к CSV-файлу для экспорта",
    )
    p_export.set_defaults(func=cmd_export)

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
