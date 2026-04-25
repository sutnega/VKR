#!/usr/bin/env python3
import json
import os
import re
from datetime import datetime
from collections import Counter
from typing import List, Dict, Any

import pandas as pd
import streamlit as st

STORE_DEFAULT = "news_store.json"

STOPWORDS = {
    "и", "в", "во", "на", "по", "из", "от", "до", "за", "для", "над", "под",
    "о", "об", "про", "при", "без", "что", "это", "как", "так", "к", "ко",
    "же", "у", "не", "но", "с", "со", "а", "или", "бы", "мы", "вы", "они",
    "он", "она", "оно", "их", "наш", "ваш", "та", "тот", "эта", "этот",
    "такой", "такое", "такая", "также", "же", "еще", "уже", "года", "году",
    "the", "and", "of", "in", "on", "for", "to", "a", "an", "is", "are",
}

WORD_RE = re.compile(r"[A-Za-zА-Яа-яёЁ]+")

SOURCE_LABELS = {
    "lesprominform-news": "Леспроминформ",
    "sbo-paper": "СБО-Бумага",
    "rosinvest-bumles": "РосИнвест",
    "upackunion-stati": "УпакСоюз",
}

SOURCE_COLORS = {
    "lesprominform-news": "#2E86AB",
    "sbo-paper": "#A23B72",
    "rosinvest-bumles": "#F18F01",
    "upackunion-stati": "#C73E1D",
}


# ─────────────────────────────────────────────
# Загрузка данных
# ─────────────────────────────────────────────

def load_items(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        st.error(f"Файл хранилища не найден: {path}")
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("items", [])


def build_dataframe(items: List[Dict[str, Any]]) -> pd.DataFrame:
    if not items:
        return pd.DataFrame()
    df = pd.DataFrame(items)
    for col in ["source", "title", "url", "published", "published_raw", "summary"]:
        if col not in df.columns:
            df[col] = ""

    def parse_dt(val):
        if not isinstance(val, str) or not val:
            return pd.NaT
        try:
            return datetime.fromisoformat(val)
        except Exception:
            return pd.NaT

    df["published_dt"] = pd.to_datetime(df["published"].apply(parse_dt), utc=True, errors="coerce")
    df["has_summary"] = df["summary"].apply(lambda x: bool(x and len(str(x).strip()) > 50))
    df["source_label"] = df["source"].map(SOURCE_LABELS).fillna(df["source"])
    return df


# ─────────────────────────────────────────────
# Анализ текста
# ─────────────────────────────────────────────

def tokenize(text: str) -> List[str]:
    words = WORD_RE.findall((text or "").lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 3]


def compute_word_freq(df: pd.DataFrame) -> Counter:
    cnt = Counter()
    for _, row in df.iterrows():
        txt = " ".join(str(row.get(c, "")) for c in ["title", "summary"])
        cnt.update(tokenize(txt))
    return cnt


# ─────────────────────────────────────────────
# CSS / стили
# ─────────────────────────────────────────────

DARK_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');
html, body, [class*="css"] { font-family: 'IBM Plex Sans', sans-serif; }
.stApp { background: #0d0d1a; color: #ddd; }
.stSidebar { background: #111126 !important; }
.metric-card {
    background: #1a1a2e; border-radius: 10px; padding: 18px 20px;
    border: 1px solid #2a2a4a; text-align: center;
}
.metric-value {
    font-family: 'IBM Plex Mono', monospace; font-size: 32px;
    font-weight: 500; color: #4a9eff; line-height: 1;
}
.metric-label { font-size: 12px; color: #888; margin-top: 6px; letter-spacing: 0.5px; text-transform: uppercase; }
h1, h2, h3 { color: #eee !important; }
.stTabs [data-baseweb="tab"] { color: #aaa; }
.stTabs [aria-selected="true"] { color: #4a9eff !important; }
.news-card-header {
    border-left: 4px solid var(--card-color, #4a9eff);
    background: #1a1a2e; border-radius: 8px;
    padding: 16px 20px; margin-bottom: 4px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
}
.source-badge {
    display: inline-block; border-radius: 4px; padding: 2px 10px;
    font-size: 12px; font-weight: 600; letter-spacing: 0.5px;
}
.summary-box {
    background: #0f3460; border-radius: 6px; padding: 14px 18px;
    border-left: 3px solid #4CAF50; margin-bottom: 12px;
}
.summary-label {
    color: #4CAF50; font-size: 11px; font-weight: 700;
    margin-bottom: 8px; letter-spacing: 1px;
}
</style>
"""


# ─────────────────────────────────────────────
# Полноэкранная карточка новости
# ─────────────────────────────────────────────

def show_full_card(row: pd.Series):
    """Отдельная «страница» — полный просмотр одной новости."""
    source = row.get("source", "")
    color = SOURCE_COLORS.get(source, "#4a9eff")
    label = SOURCE_LABELS.get(source, source)
    title = row.get("title", "Без заголовка")
    url = row.get("url", "")
    published = row.get("published_raw") or row.get("published", "")
    summary = str(row.get("summary", "") or "")
    has_summary = row.get("has_summary", False)

    # Кнопка назад
    if st.button("← Назад к списку", key="back_btn"):
        st.session_state["open_news_id"] = None
        st.rerun()

    st.markdown(f"""
    <div style="margin: 16px 0 8px 0;">
        <span class="source-badge" style="background:{color}22; color:{color}; border:1px solid {color}55;">
            {label}
        </span>
        <span style="color:#888; font-size:13px; margin-left:12px;">{published}</span>
    </div>
    <h2 style="color:#eee; font-size:22px; line-height:1.4; margin: 0 0 20px 0;">{title}</h2>
    """, unsafe_allow_html=True)

    st.markdown(f"<div style='width:100%; height:3px; background:linear-gradient({color}, transparent); margin-bottom:24px; border-radius:2px;'></div>", unsafe_allow_html=True)

    # Резюме — выводится через st.markdown, не через HTML-переменную, поэтому не обрезается
    if has_summary and summary.strip():
        st.markdown("""
        <div class="summary-box">
            <div class="summary-label">✦ AI-РЕЗЮМЕ</div>
        </div>
        """, unsafe_allow_html=True)
        st.markdown(f"> {summary}")
        st.markdown("")
    else:
        st.info("Резюме ещё не сгенерировано — запустите summarize.py --provider ollama")

    # Ссылка
    if url:
        st.markdown(f"**Источник:** [{url}]({url})")

    # Все поля
    with st.expander("📄 Все данные записи"):
        st.json({
            "id": row.get("id", ""),
            "source": source,
            "title": title,
            "url": url,
            "published": row.get("published", ""),
            "published_raw": row.get("published_raw", ""),
            "summary": summary,
        })


# ─────────────────────────────────────────────
# Карточка в списке (компактная) + кнопка открытия
# ─────────────────────────────────────────────

def show_news_card_compact(row: pd.Series, idx: int):
    source = row.get("source", "")
    color = SOURCE_COLORS.get(source, "#555")
    label = SOURCE_LABELS.get(source, source)
    title = row.get("title", "Без заголовка")
    url = row.get("url", "")
    published = row.get("published_raw") or row.get("published", "")
    summary = str(row.get("summary", "") or "")
    has_summary = row.get("has_summary", False)
    news_id = row.get("id", str(idx))

    st.markdown(f"""
    <div class="news-card-header" style="--card-color:{color};">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <span class="source-badge" style="background:{color}22; color:{color}; border:1px solid {color}55;">{label}</span>
            <span style="color:#888; font-size:12px;">{published}</span>
        </div>
        <div style="color:#eee; font-size:16px; font-weight:500; line-height:1.4;">{title}</div>
    </div>
    """, unsafe_allow_html=True)

    # Резюме — выводим через st.markdown чтобы не обрезалось
    if has_summary and summary.strip():
        st.markdown("""
        <div class="summary-box">
            <div class="summary-label">✦ AI-РЕЗЮМЕ</div>
        </div>
        """, unsafe_allow_html=True)
        # Текст резюме через нативный st.markdown — без HTML, без обрезания
        st.markdown(summary)

    col_link, col_btn = st.columns([3, 1])
    with col_link:
        if url:
            st.markdown(f'<a href="{url}" target="_blank" style="color:#4a9eff; font-size:13px;">🔗 Открыть источник →</a>', unsafe_allow_html=True)
    with col_btn:
        if st.button("Открыть карточку →", key=f"open_{news_id}_{idx}"):
            st.session_state["open_news_id"] = news_id
            st.rerun()

    st.markdown("<hr style='border:none; border-top:1px solid #222; margin:12px 0;'>", unsafe_allow_html=True)


# ─────────────────────────────────────────────
# Главная
# ─────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Аналитика новостей ЦБП",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown(DARK_CSS, unsafe_allow_html=True)

    # Инициализация session state
    if "open_news_id" not in st.session_state:
        st.session_state["open_news_id"] = None

    # ── Сайдбар ──
    with st.sidebar:
        st.markdown("### ⚙️ Настройки")
        store_path = st.text_input("Файл хранилища", STORE_DEFAULT)
        st.markdown("---")

    items = load_items(store_path)
    if not items:
        st.stop()

    df = build_dataframe(items)

    # ══════════════════════════════════════════
    # РЕЖИМ: полноэкранная карточка одной новости
    # ══════════════════════════════════════════
    if st.session_state["open_news_id"] is not None:
        news_id = st.session_state["open_news_id"]
        match = df[df["id"] == news_id]
        if len(match) == 0:
            st.error("Новость не найдена.")
            st.session_state["open_news_id"] = None
        else:
            show_full_card(match.iloc[0])
        return  # не рендерим остальное

    # ══════════════════════════════════════════
    # РЕЖИМ: основной интерфейс
    # ══════════════════════════════════════════

    # Фильтры в сайдбаре
    with st.sidebar:
        st.markdown("### 🔍 Фильтры")
        sources = sorted(df["source"].dropna().unique())
        chosen_sources = st.multiselect(
            "Источники", options=sources, default=sources,
            format_func=lambda x: SOURCE_LABELS.get(x, x),
        )
        search_query = st.text_input("Поиск по тексту", placeholder="введите слово...")
        only_with_summary = st.checkbox("Только с AI-резюме", value=False)

        min_dt = df["published_dt"].min()
        max_dt = df["published_dt"].max()
        use_date = False
        if pd.notna(min_dt) and pd.notna(max_dt):
            use_date = st.checkbox("Фильтр по дате")
            if use_date:
                start_date, end_date = st.date_input(
                    "Диапазон", value=(min_dt.date(), max_dt.date()),
                )

        top_n = st.slider("Топ слов", 10, 60, 25, 5)

    # Заголовок
    st.markdown("""
    <div style="padding: 8px 0 24px 0;">
        <div style="font-family:'IBM Plex Mono',monospace; font-size:11px; color:#4a9eff; letter-spacing:2px; margin-bottom:6px;">
            МОНИТОРИНГ ОТРАСЛИ
        </div>
        <h1 style="margin:0; font-size:28px; font-weight:600; color:#eee;">
            Аналитика новостей ЦБП и упаковки
        </h1>
    </div>
    """, unsafe_allow_html=True)

    # Применяем фильтры
    filtered = df.copy()
    if chosen_sources:
        filtered = filtered[filtered["source"].isin(chosen_sources)]
    if only_with_summary:
        filtered = filtered[filtered["has_summary"]]
    if search_query.strip():
        q = search_query.strip().lower()
        mask = (
            filtered["title"].str.lower().str.contains(q, na=False) |
            filtered["summary"].str.lower().str.contains(q, na=False)
        )
        filtered = filtered[mask]
    if use_date and pd.notna(min_dt) and pd.notna(max_dt):
        from datetime import timezone
        s = pd.Timestamp(datetime.combine(start_date, datetime.min.time()), tz=timezone.utc)
        e = pd.Timestamp(datetime.combine(end_date, datetime.max.time()), tz=timezone.utc)
        filtered = filtered[(filtered["published_dt"] >= s) & (filtered["published_dt"] <= e)]

    # Метрики
    total = len(filtered)
    with_summary = int(filtered["has_summary"].sum())
    pct = int(with_summary / total * 100) if total else 0
    n_sources = filtered["source"].nunique()

    last_month_count = 0
    if filtered["published_dt"].notna().any():
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30)
        last_month_count = int((filtered["published_dt"] >= cutoff).sum())

    c1, c2, c3, c4 = st.columns(4)
    for col, val, label in [
        (c1, total, "Новостей"),
        (c2, n_sources, "Источников"),
        (c3, f"{with_summary} / {pct}%", "С AI-резюме"),
        (c4, last_month_count, "За 30 дней"),
    ]:
        col.markdown(f"""
        <div class="metric-card">
            <div class="metric-value">{val}</div>
            <div class="metric-label">{label}</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    tab1, tab2, tab3 = st.tabs(["📋 Новости", "📈 Аналитика", "☁️ Частотный анализ"])

    # ════════════════════════════════════════
    # ВКЛАДКА 1 — Новости
    # ════════════════════════════════════════
    with tab1:
        view_mode = st.radio("Режим отображения", ["Карточки", "Таблица"], horizontal=True)

        if total == 0:
            st.info("Нет новостей под текущие фильтры.")

        elif view_mode == "Таблица":
            show_cols = ["source_label", "published_raw", "title", "url", "has_summary"]
            rename_map = {
                "source_label": "Источник", "published_raw": "Дата",
                "title": "Заголовок", "url": "Ссылка", "has_summary": "Резюме",
            }
            st.dataframe(
                filtered[show_cols].rename(columns=rename_map),
                use_container_width=True, height=400,
            )
            st.markdown("#### Открыть карточку")
            titles = filtered["title"].tolist()
            chosen_title = st.selectbox("Выберите новость", ["— не выбрано —"] + titles)
            if chosen_title != "— не выбрано —":
                match = filtered[filtered["title"] == chosen_title]
                if len(match):
                    news_id = match.iloc[0].get("id", "")
                    if st.button("Открыть полную карточку →"):
                        st.session_state["open_news_id"] = news_id
                        st.rerun()

        else:
            page_size = st.select_slider("Карточек на странице", [5, 10, 20, 50], value=10)
            total_pages = max(1, (total - 1) // page_size + 1)
            page = st.number_input("Страница", min_value=1, max_value=total_pages, value=1, step=1)
            start = (page - 1) * page_size
            chunk = filtered.iloc[start: start + page_size]

            st.markdown(f"<div style='color:#888; font-size:13px; margin-bottom:16px;'>Показано {start+1}–{min(start+page_size, total)} из {total}</div>", unsafe_allow_html=True)

            for idx, (_, row) in enumerate(chunk.iterrows()):
                show_news_card_compact(row, start + idx)

    # ════════════════════════════════════════
    # ВКЛАДКА 2 — Аналитика
    # ════════════════════════════════════════
    with tab2:
        if total == 0:
            st.info("Нет данных.")
        else:
            col_l, col_r = st.columns(2)
            with col_l:
                st.markdown("##### Новости по источникам")
                by_src = (
                    filtered.groupby("source_label").size()
                    .rename("Количество").sort_values(ascending=False)
                )
                st.bar_chart(by_src)

            with col_r:
                st.markdown("##### Покрытие AI-резюме по источникам")
                cov = (
                    filtered.groupby("source_label")["has_summary"]
                    .agg(["sum", "count"])
                    .rename(columns={"sum": "С резюме", "count": "Всего"})
                )
                cov["% покрытия"] = (cov["С резюме"] / cov["Всего"] * 100).round(1)
                st.dataframe(cov, use_container_width=True)

            if filtered["published_dt"].notna().any():
                st.markdown("##### Динамика публикаций по месяцам")
                tmp = filtered.dropna(subset=["published_dt"]).copy()
                tmp["month"] = tmp["published_dt"].dt.to_period("M").astype(str)
                per_month = (
                    tmp.groupby("month").size()
                    .rename("Новостей").reset_index().sort_values("month")
                )
                st.line_chart(per_month.set_index("month"))

            no_summary = filtered[~filtered["has_summary"]][["source_label", "title"]]
            if len(no_summary):
                with st.expander(f"📭 Новости без резюме ({len(no_summary)} шт.)"):
                    st.dataframe(
                        no_summary.rename(columns={"source_label": "Источник", "title": "Заголовок"}),
                        use_container_width=True,
                    )

    # ════════════════════════════════════════
    # ВКЛАДКА 3 — Частотный анализ
    # ════════════════════════════════════════
    with tab3:
        if total == 0:
            st.info("Нет данных.")
        else:
            freq = compute_word_freq(filtered)
            top_words = freq.most_common(top_n)

            if not top_words:
                st.info("Недостаточно текста для анализа.")
            else:
                freq_df = pd.DataFrame(top_words, columns=["Слово", "Частота"])
                col_a, col_b = st.columns([1, 2])
                with col_a:
                    st.markdown("##### Таблица топ-слов")
                    st.dataframe(freq_df, use_container_width=True, height=450)
                with col_b:
                    st.markdown("##### Частота упоминаний")
                    st.bar_chart(freq_df.set_index("Слово"))

                st.markdown("##### Топ-15 слов по каждому источнику")
                if chosen_sources:
                    src_cols = st.columns(min(len(chosen_sources), 3))
                    for i, src in enumerate(chosen_sources):
                        src_df = filtered[filtered["source"] == src]
                        src_freq = compute_word_freq(src_df)
                        top_src = src_freq.most_common(15)
                        if top_src:
                            with src_cols[i % len(src_cols)]:
                                lbl = SOURCE_LABELS.get(src, src)
                                clr = SOURCE_COLORS.get(src, "#4a9eff")
                                st.markdown(f"<span style='color:{clr}; font-weight:600;'>{lbl}</span>", unsafe_allow_html=True)
                                st.dataframe(pd.DataFrame(top_src, columns=["Слово", "Частота"]), use_container_width=True, height=350)


if __name__ == "__main__":
    main()

"""streamlit run news_app.py"""