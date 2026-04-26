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

        # Deprecation-safe поиск даты
        date_el = item.find("pubDate")
        if date_el is None:
            date_el = item.find("{http://purl.org/dc/elements/1.1/}date")

        title = (title_el.text or "").strip() if title_el is not None else ""
        link = (link_el.text or "").strip() if link_el is not None else ""
        desc = (desc_el.text or "").strip() if desc_el is not None else ""
        pub_raw = (date_el.text or "").strip() if date_el is not None else ""

        pub_dt = parse_rss_datetime(pub_raw) if pub_raw else None
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


# ---------------------- HTML sources: common helpers ----------------------


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


def strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<.*?>", "", text)
    return unescape(text).strip()


# ---------------------- HTML sources: UpackUnion ----------------------


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


def fetch_upackunion_articles(cfg: Dict[str, Any]) -> List[NewsItem]:
    """
    Парсим именно рубрику /cat/stati/:
    берем блоки <article class="mg-posts-sec-post ..."> прямо со страниц
    пагинации, чтобы не грузить каждую статью отдельно.
    """
    base_url: str = cfg["base_url"]
    pages: int = cfg.get("pages", 1)
    max_articles: int = cfg.get("max_articles", 30)
    name: str = cfg.get("name", "upackunion-stati")

    print(f"[collector] Читаю HTML '{name}' (UpackUnion) из {base_url} ...")

    items: List[NewsItem] = []
    for page in range(1, pages + 1):
        if page == 1:
            url = base_url
        else:
            # на сайте пагинация в виде /cat/stati/page/2/
            url = base_url.rstrip("/") + f"/page/{page}/"
        try:
            html = http_get(url)
        except (HTTPError, URLError) as e:
            print(f"[collector]   ошибка при чтении страницы {url}: {e}")
            continue

        # выделяем каждый <article class="... mg-posts-sec-post ...">
        for m_art in re.finditer(
            r'<article[^>]*class="[^"]*mg-posts-sec-post[^"]*"[^>]*>(.*?)</article>',
            html,
            flags=re.S | re.I,
        ):
            block = m_art.group(1)

            # заголовок + URL
            m_title = re.search(
                r'<h4[^>]*class="[^"]*entry-title[^"]*"[^>]*>\s*'
                r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                block,
                flags=re.S | re.I,
            )
            if not m_title:
                continue
            href = m_title.group(1)
            title_html = m_title.group(2)
            url_full = urljoin(base_url, href)
            title = strip_html(title_html)

            # дата
            m_date = re.search(
                r'<span[^>]*class="[^"]*mg-blog-date[^"]*"[^>]*>.*?'
                r'<a[^>]*href="[^"]*">\s*([^<]+)\s*</a>',
                block,
                flags=re.S | re.I,
            )
            pub_raw = m_date.group(1).strip() if m_date else None
            pub_dt = parse_upackunion_date(pub_raw) if pub_raw else None
            pub_iso = pub_dt.isoformat() if pub_dt is not None else None

            # краткое содержание
            m_summary = re.search(
                r'<div[^>]*class="[^"]*mg-content[^"]*"[^>]*>\s*<p[^>]*>(.*?)</p>',
                block,
                flags=re.S | re.I,
            )
            summary = strip_html(m_summary.group(1)) if m_summary else None
            if summary:
                summary = summary[:400] + ("…" if len(summary) > 400 else "")

            nid = f"{name}:{url_full}"
            items.append(
                NewsItem(
                    id=nid,
                    source=name,
                    title=title,
                    url=url_full,
                    published=pub_iso,
                    published_raw=pub_raw,
                    summary=summary,
                )
            )

            if len(items) >= max_articles:
                break
        if len(items) >= max_articles:
            break

    print(f"[collector]   найдено статей: {len(items)}")
    return items


# ---------------------- HTML sources: Lesprominform ----------------------


def parse_lesprominform_date(text: str) -> Optional[dt.datetime]:
    """
    Формат: 10.12.2025
    """
    text = text.strip()
    try:
        d = dt.datetime.strptime(text, "%d.%m.%Y")
        return d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def fetch_lesprominform_news(cfg: Dict[str, Any]) -> List[NewsItem]:
    """
    Парсим страницу отношений по тегу ЦБП:
    https://lesprominform.ru/relations.html?tag=888[&p=2]
    Берем все <article class="news teaser">, и новости, и статьи.
    """
    base_url: str = cfg["base_url"]  # уже с ?tag=888
    name: str = cfg.get("name", "lesprominform-news")
    pages: int = cfg.get("pages", 1)
    max_articles: int = cfg.get("max_articles", 50)

    print(f"[collector] Читаю HTML '{name}' (Lesprominform) из {base_url} ...")

    items: List[NewsItem] = []

    for page in range(1, pages + 1):
        if page == 1:
            url = base_url
        else:
            # если в base_url уже есть ?, добавляем &p=...
            sep = "&" if "?" in base_url else "?"
            url = base_url + f"{sep}p={page}"

        try:
            html = http_get(url)
        except (HTTPError, URLError) as e:
            print(f"[collector]   ошибка при чтении {url}: {e}")
            continue

        # для каждой статьи/новости
        for m_art in re.finditer(
            r'<article[^>]*class="[^"]*news teaser[^"]*"[^>]*>(.*?)</article>',
            html,
            flags=re.S | re.I,
        ):
            block = m_art.group(1)

            # дата (верхний маленький див)
            m_date = re.search(
                r'<div[^>]*class="[^"]*\bdate\b[^"]*d-inline-block[^"]*"[^>]*>\s*([\d\.]+)\s*</div>',
                block,
                flags=re.S | re.I,
            )
            date_raw = m_date.group(1).strip() if m_date else None
            pub_dt = parse_lesprominform_date(date_raw) if date_raw else None
            pub_iso = pub_dt.isoformat() if pub_dt is not None else None

            # заголовок и ссылка
            m_title = re.search(
                r'<div[^>]*class="[^"]*\btitle\b[^"]*"[^>]*>\s*'
                r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                block,
                flags=re.S | re.I,
            )
            if not m_title:
                continue
            href = m_title.group(1)
            title_html = m_title.group(2)
            url_full = urljoin(base_url, href)
            title = strip_html(title_html)

            # используем сам заголовок как краткое описание
            summary = None

            nid = f"{name}:{url_full}"
            items.append(
                NewsItem(
                    id=nid,
                    source=name,
                    title=title,
                    url=url_full,
                    published=pub_iso,
                    published_raw=date_raw,
                    summary=summary,
                )
            )

            if len(items) >= max_articles:
                break

        if len(items) >= max_articles:
            break

    print(f"[collector]   найдено элементов: {len(items)}")
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

# Узкоспециализированные источники, для которых тематика почти гарантирована
NARROW_SOURCES = {
    "sbo-paper",
    "rosinvest-bumles",
    "upackunion-stati",
    "lesprominform-news",
    # Новые — тематика ЦБП/упаковки гарантирована
    "tissueworldmagazine",
    "packaging-gateway",
    "papnews",
    "pulpandpaper-technology",
    "packaging-europe",
    "nonwovens-industry",
    "toscotec",
    "sappi-news",
    "the-paper-story",
}


def is_thematic(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in THEMATIC_KEYWORDS)


def filter_thematic(items: List[NewsItem]) -> List[NewsItem]:
    if not items:
        return []

    narrow: List[NewsItem] = []
    generic: List[NewsItem] = []

    for it in items:
        if it.source in NARROW_SOURCES:
            narrow.append(it)
        else:
            generic.append(it)

    passed_generic: List[NewsItem] = []
    for it in generic:
        body = " ".join(part for part in (it.title, it.summary or "", it.source) if part)
        if is_thematic(body):
            passed_generic.append(it)

    res = narrow + passed_generic
    print(
        f"[service] Тематический фильтр (ЦБП/упаковка): оставлено {len(res)} из {len(items)} "
        f"(из них {len(narrow)} — узкоспециализированные источники без фильтра)"
    )
    return res


def filter_by_date(items: List[NewsItem], max_age_days: int) -> List[NewsItem]:
    """
    max_age_days >= 0:
        - для новостей с корректной датой: оставляем только те, что не старше порога;
        - для новостей без даты / с ошибкой парсинга: НЕ выбрасываем, а помечаем published_raw,
          как минимум 'ДАТА ОТСУТСТВУЕТ', и оставляем.
    """
    if max_age_days < 0:
        print("[service] Фильтр по дате отключён (max_age_days < 0).")
        return items

    now = dt.datetime.now(dt.timezone.utc)
    res: List[NewsItem] = []
    no_date_or_error = 0
    with_date_checked = 0

    for it in items:
        if not it.published:
            no_date_or_error += 1
            # если вообще ничего нет — явно помечаем
            if not it.published_raw:
                it = dataclasses.replace(it, published_raw="ДАТА ОТСУТСТВУЕТ")
            res.append(it)
            continue

        try:
            d = dt.datetime.fromisoformat(it.published)
        except Exception:
            no_date_or_error += 1
            it = dataclasses.replace(it, published_raw="ДАТА ОТСУТСТВУЕТ")
            res.append(it)
            continue

        with_date_checked += 1
        age = now - d
        if age.days <= max_age_days:
            res.append(it)

    print(
        f"[service] Фильтр по дате: оставлено {len(res)} из {len(items)} "
        f"(max_age_days={max_age_days}, проверено по дате: {with_date_checked}, "
        f"без даты/ошибкой: {no_date_or_error}, они сохранены без фильтра)"
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
    # ── Новые глобальные RSS-источники ──────────────────────────────
    "tissueworldmagazine": {
        "type": "rss",
        "url": "https://www.tissueworldmagazine.com/feed/",
    },
    "packaging-gateway": {
        "type": "rss",
        "url": "https://www.packaging-gateway.com/feed/",
    },
    "papnews": {
        "type": "rss",
        "url": "https://www.papnews.com/feed/",
    },
    "pulpandpaper-technology": {
        "type": "rss",
        "url": "https://www.pulpandpaper-technology.com/feed/",
    },
    "packaging-europe": {
        "type": "rss",
        "url": "https://packagingeurope.com/rss",
    },
    "nonwovens-industry": {
        "type": "rss",
        "url": "https://www.nonwovens-industry.com/feed/",
    },
    "toscotec": {
        "type": "rss",
        "url": "https://www.toscotec.com/feed/",
    },
    "sappi-news": {
        "type": "rss",
        "url": "https://www.sappi.com/news/feed/",
    },
    "the-paper-story": {
        "type": "rss",
        "url": "https://thepaperstory.co.za/news/feed/",
    },
    "rbc": {
        "type": "rss",
        "url": "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",
    },
    "upackunion-stati": {
        "type": "html_upackunion",
        "base_url": "https://upackunion.ru/cat/stati/",
        "pages": 2,
        "max_articles": 40,
        "name": "upackunion-stati",
    },
    "lesprominform-news": {
        "type": "html_lesprominform",
        # страница новостей/статей по тегу ЦБП
        "base_url": "https://lesprominform.ru/relations.html?tag=888",
        "pages": 3,          # сколько страниц листать
        "max_articles": 80,  # общий лимит по статьям
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

    thematic = filter_by_date(thematic, max_age_days)

    if not thematic:
        print(
            "[service] Все тематические новости с датой оказались старше порога. "
            "Новости без даты сохранены, но их может быть мало. "
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
    elif args.format == "xlsx":
        try:
            from openpyxl import Workbook
        except ImportError:
            print(
                "[main] Для экспорта в Excel (.xlsx) нужен пакет openpyxl.\n"
                "Установите командой:\n"
                "    pip install openpyxl"
            )
            return

        wb = Workbook()
        ws = wb.active
        ws.title = "News"

        headers = ["source", "title", "url", "published", "published_raw", "summary"]
        ws.append(headers)

        for it in items:
            ws.append([
                it.get("source", ""),
                it.get("title", ""),
                it.get("url", ""),
                it.get("published", ""),
                it.get("published_raw", ""),
                it.get("summary", ""),
            ])

        wb.save(args.output)
        print(f"[main] Экспортировано {len(items)} новостей в {args.output} (XLSX)")

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

    p_export = sub.add_parser("export", help="экспорт новостей в JSON/CSV/XLSX")
    p_export.add_argument(
        "--format",
        choices=["json", "csv", "xlsx"],
        default="json",
        help="формат экспорта (json, csv или xlsx, по умолчанию json)",
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

"""python main.py export --format xlsx --output news.xlsx

 python main.py list -v     
  python main.py collect     
"""