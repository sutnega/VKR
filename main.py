#!/usr/bin/env python3
import argparse
import dataclasses
import datetime as dt
import email.utils
import json
import os
import re
from dataclasses import dataclass, asdict
from html import unescape
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import xml.etree.ElementTree as ET


# ---------------------- Data model & storage ----------------------


@dataclass
class NewsItem:
    id: str
    source: str
    title: str
    url: str
    published: Optional[str]  # ISO string or None
    published_raw: Optional[str]
    summary: Optional[str]


StoreType = Dict[str, Any]


def load_store(path: str) -> StoreType:
    if not os.path.exists(path):
        return {"items": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_store(path: str, store: StoreType) -> None:
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def add_items_to_store(store: StoreType, items: List[NewsItem]) -> int:
    existing_ids = {item["id"] for item in store.get("items", [])}
    added = 0
    for it in items:
        if it.id in existing_ids:
            continue
        store.setdefault("items", []).append(asdict(it))
        existing_ids.add(it.id)
        added += 1
    return added


# ---------------------- HTTP helpers ----------------------


USER_AGENT = "Mozilla/5.0 (compatible; VKR-news-bot/1.0; +https://example.com)"


def http_get(url: str, timeout: int = 20) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    # Try decode as utf-8, fallback to cp1251
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1251", errors="ignore")


# ---------------------- RSS sources ----------------------


def parse_rss_datetime(text: str) -> Optional[dt.datetime]:
    text = text.strip()
    if not text:
        return None
    # Try RFC822 (pubDate)
    try:
        d = email.utils.parsedate_to_datetime(text)
        if d is not None and d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d
    except Exception:
        pass
    # Try ISO
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            d = dt.datetime.strptime(text, fmt)
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
            return d
        except Exception:
            continue
    return None


def fetch_rss_source(name: str, cfg: Dict[str, Any]) -> List[NewsItem]:
    url = cfg["url"]
    print(f"[collector] Читаю RSS '{name}' из {url} ...")
    try:
        xml_text = http_get(url)
    except (HTTPError, URLError) as e:
        print(f"[collector]   ошибка при чтении {url}: {e}")
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"[collector]   ошибка парсинга XML: {e}")
        return []

    items: List[NewsItem] = []
    for item in root.findall(".//item"):
        title_el = item.find("title")
        link_el = item.find("link")
        desc_el = item.find("description")
        date_el = item.find("pubDate") or item.find("{http://purl.org/dc/elements/1.1/}date")

        title = (title_el.text or "").strip() if title_el is not None else ""
        link = (link_el.text or "").strip() if link_el is not None else ""
        desc = (desc_el.text or "").strip() if desc_el is not None else ""
        pub_raw = (date_el.text or "").strip() if date_el is not None else ""

        pub_dt = parse_rss_datetime(pub_raw)
        pub_iso = pub_dt.isoformat() if pub_dt is not None else None

        if not link:
            # Generate pseudo-id
            link = f"rss://{name}/{len(items)}"

        nid = f"{name}:{link}"

        items.append(
            NewsItem(
                id=nid,
                source=name,
                title=unescape(title),
                url=link,
                published=pub_iso,
                published_raw=pub_raw or None,
                summary=unescape(desc) if desc else None,
            )
        )

    print(f"[collector]   найдено элементов: {len(items)}")
    return items


# ---------------------- HTML sources: UpackUnion ----------------------


RU_MONTHS = {
    "янв": 1,
    "фев": 2,
    "мар": 3,
    "апр": 4,
    "май": 5,
    "июн": 6,
    "июл": 7,
    "авг": 8,
    "сен": 9,
    "сент": 9,
    "окт": 10,
    "ноя": 11,
    "дек": 12,
}


def parse_upackunion_date(text: str) -> Optional[dt.datetime]:
    """
    Примеры формата:
      'Ноя 10, 2025'
      'Окт 3, 2025'
    """
    text = text.strip()
    m = re.search(r"([А-Яа-яЁё]+)\s+(\d{1,2}),\s*(\d{4})", text)
    if not m:
        return None
    month_name = m.group(1).lower()
    # укоротим до первых 3 букв
    month_key = month_name[:3]
    month = RU_MONTHS.get(month_key)
    if not month:
        return None
    day = int(m.group(2))
    year = int(m.group(3))
    try:
        return dt.datetime(year, month, day, tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def strip_html(text: str) -> str:
    # Удаляем простейшие теги, не претендуем на идеальность
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<.*?>", "", text)
    return unescape(text).strip()


def fetch_upackunion_articles(cfg: Dict[str, Any]) -> List[NewsItem]:
    base_url: str = cfg["base_url"]
    pages: int = cfg.get("pages", 1)
    name: str = cfg.get("name", "upackunion-stati")

    print(f"[collector] Читаю HTML '{name}' (UpackUnion) из {base_url} ...")

    article_urls: List[str] = []
    for page in range(1, pages + 1):
        if page == 1:
            url = base_url
        else:
            url = base_url.rstrip("/") + f"/page/{page}/"
        try:
            html = http_get(url)
        except (HTTPError, URLError) as e:
            print(f"[collector]   ошибка при чтении страницы {url}: {e}")
            continue

        # Ищем ссылки вида /stati/slug/ или https://upackunion.ru/stati/slug/
        for href in re.findall(r'href="([^"]+)"', html):
            if "/stati/" in href:
                full = urljoin(base_url, href)
                if full not in article_urls:
                    article_urls.append(full)

    # Ограничим количество, чтобы не спамить сайт
    max_articles = cfg.get("max_articles", 30)
    article_urls = article_urls[:max_articles]

    items: List[NewsItem] = []

    for url in article_urls:
        try:
            html = http_get(url)
        except (HTTPError, URLError) as e:
            print(f"[collector]   ошибка при чтении статьи {url}: {e}")
            continue

        # Заголовок
        m_title = re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.S | re.I)
        title = strip_html(m_title.group(1)) if m_title else url

        # Дата рядом с заголовком
        m_date = re.search(r"([А-Яа-яЁё]+?\s+\d{1,2},\s*\d{4})", html)
        pub_dt = parse_upackunion_date(m_date.group(1)) if m_date else None
        pub_iso = pub_dt.isoformat() if pub_dt is not None else None
        pub_raw = m_date.group(1) if m_date else None

        # Аннотация — первые ~400 символов текста статьи
        m_article = re.search(r"<article[^>]*>(.*?)</article>", html, flags=re.S | re.I)
        body_html = m_article.group(1) if m_article else html
        body_text = strip_html(body_html)
        summary = body_text[:400] + ("…" if len(body_text) > 400 else "")

        nid = f"{name}:{url}"
        items.append(
            NewsItem(
                id=nid,
                source=name,
                title=title,
                url=url,
                published=pub_iso,
                published_raw=pub_raw,
                summary=summary or None,
            )
        )

    print(f"[collector]   найдено статей: {len(items)}")
    return items


# ---------------------- HTML sources: Lesprominform ----------------------


def parse_lesprominform_date(text: str) -> Optional[dt.datetime]:
    text = text.strip()
    m = re.search(r"(\d{2}\.\d{2}\.\d{4})(?:\s+(\d{2}:\d{2}))?", text)
    if not m:
        return None
    date_str = m.group(1)
    time_str = m.group(2) or "12:00"
    try:
        d = dt.datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
        return d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def fetch_lesprominform_news(cfg: Dict[str, Any]) -> List[NewsItem]:
    base_url: str = cfg["base_url"]
    name: str = cfg.get("name", "lesprominform-news")

    print(f"[collector] Читаю HTML '{name}' (Lesprominform) из {base_url} ...")

    try:
        html = http_get(base_url)
    except (HTTPError, URLError) as e:
        print(f"[collector]   ошибка при чтении {base_url}: {e}")
        return []

    # Ищем ссылки на новости вида news.html?id=NNNNN
    rel_urls = re.findall(r'href="(news\.html\?id=\d+)"', html)
    if not rel_urls:
        print("[collector]   не нашли ссылок news.html?id=...")
        return []

    # Удаляем дубликаты, сохраняем порядок
    seen = set()
    article_urls: List[str] = []
    for rel in rel_urls:
        if rel not in seen:
            seen.add(rel)
            article_urls.append(urljoin(base_url, rel))

    max_articles = cfg.get("max_articles", 30)
    article_urls = article_urls[:max_articles]

    items: List[NewsItem] = []

    for url in article_urls:
        try:
            article_html = http_get(url)
        except (HTTPError, URLError) as e:
            print(f"[collector]   ошибка при чтении статьи {url}: {e}")
            continue

        # Заголовок
        m_title = re.search(r"<h1[^>]*>(.*?)</h1>", article_html, flags=re.S | re.I)
        title = strip_html(m_title.group(1)) if m_title else url

        # Дата
        m_date = re.search(r"\d{2}\.\d{2}\.\d{4}(?:\s+\d{2}:\d{2})?", article_html)
        pub_dt = parse_lesprominform_date(m_date.group(0)) if m_date else None
        pub_iso = pub_dt.isoformat() if pub_dt is not None else None
        pub_raw = m_date.group(0) if m_date else None

        # Текст статьи — возьмём первый <p> после заголовка
        body_part = article_html
        if m_title:
            pos = m_title.end()
            body_part = article_html[pos:]
        m_p = re.search(r"<p[^>]*>(.*?)</p>", body_part, flags=re.S | re.I)
        if m_p:
            body_html = m_p.group(1)
        else:
            body_html = body_part
        body_text = strip_html(body_html)
        summary = body_text[:400] + ("…" if len(body_text) > 400 else "")

        nid = f"{name}:{url}"
        items.append(
            NewsItem(
                id=nid,
                source=name,
                title=title,
                url=url,
                published=pub_iso,
                published_raw=pub_raw,
                summary=summary or None,
            )
        )

    print(f"[collector]   найдено новостей: {len(items)}")
    return items


# ---------------------- Thematic & date filters ----------------------


# Ключевые слова по теме ЦБП и упаковки (очень приблизительно)
THEMATIC_KEYWORDS = [
    "целлюлозно-бумаж",
    "цбп",
    "бумажн",
    "картон",
    "гофро",
    "упаковк",
    "тар",
    "крахмал",
    "полиграф",
    "леспром",
    "лесопромышлен",
]


def is_thematic(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in THEMATIC_KEYWORDS)


def filter_thematic(items: List[NewsItem]) -> List[NewsItem]:
    if not items:
        return []
    res: List[NewsItem] = []
    for it in items:
        body = " ".join(
            part
            for part in (it.title, it.summary or "", it.source)
            if part
        ).lower()
        if is_thematic(body):
            res.append(it)
    print(f"[service] Тематический фильтр (ЦБП/упаковка): оставлено {len(res)} из {len(items)}")
    return res


def filter_by_date(items: List[NewsItem], max_age_days: int) -> List[NewsItem]:
    if max_age_days < 0:
        return items
    now = dt.datetime.now(dt.timezone.utc)
    res: List[NewsItem] = []
    skipped_no_date = 0
    for it in items:
        if not it.published:
            skipped_no_date += 1
            continue
        try:
            d = dt.datetime.fromisoformat(it.published)
        except Exception:
            skipped_no_date += 1
            continue
        age = now - d
        if age.days <= max_age_days:
            res.append(it)
    print(
        f"[service] Фильтр по дате: оставлено {len(res)} из {len(items)} "
        f"(max_age_days={max_age_days}, без даты/ошибкой: {skipped_no_date})"
    )
    return res


# ---------------------- Sources configuration ----------------------


SOURCES: Dict[str, Dict[str, Any]] = {
    "sbo-paper": {
        "type": "rss",
        "url": "https://sbo-paper.ru/rss",
    },
    "rosinvest-bumles": {
        "type": "rss",
        "url": "https://rosinvest.com/newsrubrik_7.rss",
    },
    "rbc": {
        "type": "rss",
        "url": "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",
    },
    "upackunion-stati": {
        "type": "html_upackunion",
        "base_url": "https://upackunion.ru/cat/stati/",
        "pages": 2,
        "max_articles": 30,
        "name": "upackunion-stati",
    },
    "lesprominform-news": {
        "type": "html_lesprominform",
        "base_url": "https://lesprominform.ru/",
        "max_articles": 30,
        "name": "lesprominform-news",
    },
}


def collect_from_all_sources(max_age_days: int) -> List[NewsItem]:
    all_items: List[NewsItem] = []
    for name, cfg in SOURCES.items():
        stype = cfg.get("type")
        if stype == "rss":
            items = fetch_rss_source(name, cfg)
        elif stype == "html_upackunion":
            items = fetch_upackunion_articles(cfg)
        elif stype == "html_lesprominform":
            items = fetch_lesprominform_news(cfg)
        else:
            print(f"[collector] неизвестный тип источника '{stype}' для {name}, пропускаю")
            items = []
        all_items.extend(items)

    print(f"[service] Всего собрали от всех источников: {len(all_items)}")

    thematic = filter_thematic(all_items)
    if not thematic:
        print("[service] После тематического фильтра ничего не осталось.")
        return []

    if max_age_days >= 0:
        thematic = filter_by_date(thematic, max_age_days)
    else:
        print("[service] Фильтр по дате отключён (max_age_days < 0).")

    if not thematic:
        print(
            "[service] Все тематические новости оказались старше порога или без корректной даты. "
            "Можешь указать --max-age-days -1, чтобы убрать фильтр по дате."
        )
    return thematic


# ---------------------- CLI commands ----------------------


def cmd_collect(args: argparse.Namespace) -> None:
    store = load_store(args.store)
    items = collect_from_all_sources(args.max_age_days)
    added = add_items_to_store(store, items)
    save_store(args.store, store)
    print(f"[main] Добавлено новостей: {added}")


def cmd_list(args: argparse.Namespace) -> None:
    store = load_store(args.store)
    items = store.get("items", [])
    print(f"[main] В хранилище новостей: {len(items)}")
    for it in items:
        print("-" * 80)
        print(f"[{it.get('source')}] {it.get('title')}")
        print(f"  URL: {it.get('url')}")
        print(f"  Дата: {it.get('published') or it.get('published_raw')}")
        if args.verbose and it.get("summary"):
            print()
            print(it["summary"])


def cmd_export(args: argparse.Namespace) -> None:
    store = load_store(args.store)
    items = store.get("items", [])
    if args.format == "json":
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        print(f"[main] Экспортировано {len(items)} новостей в {args.output} (JSON)")
    elif args.format == "csv":
        import csv

        fieldnames = ["source", "title", "url", "published", "published_raw", "summary"]
        with open(args.output, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for it in items:
                row = {k: it.get(k, "") for k in fieldnames}
                writer.writerow(row)
        print(f"[main] Экспортировано {len(items)} новостей в {args.output} (CSV)")
    else:
        print(f"[main] Неизвестный формат экспорта: {args.format}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Сборщик новостей по ЦБП/упаковке")
    p.add_argument(
        "--store",
        default="news_store.json",
        help="путь к JSON-файлу хранилища новостей (по умолчанию news_store.json)",
    )

    sub = p.add_subparsers(dest="command", required=True)

    p_collect = sub.add_parser("collect", help="собрать новости и добавить их в хранилище")
    p_collect.add_argument(
        "--max-age-days",
        type=int,
        default=365,
        help="максимальный возраст новости в днях (по умолчанию 365, -1 чтобы отключить фильтр по дате)",
    )
    p_collect.set_defaults(func=cmd_collect)

    p_list = sub.add_parser("list", help="показать новости из хранилища")
    p_list.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="показывать аннотацию новости",
    )
    p_list.set_defaults(func=cmd_list)

    p_export = sub.add_parser("export", help="экспорт новостей в JSON/CSV")
    p_export.add_argument(
        "--format",
        choices=["json", "csv"],
        default="json",
        help="формат экспорта (json или csv, по умолчанию json)",
    )
    p_export.add_argument(
        "--output",
        required=True,
        help="имя файла для экспорта",
    )
    p_export.set_defaults(func=cmd_export)

    return p


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
