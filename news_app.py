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
    "такой", "такое", "такая", "также", "же", "еще", "уже",
    "the", "and", "of", "in", "on", "for", "to", "a", "an",
}

WORD_RE = re.compile(r"[A-Za-zА-Яа-яёЁ]+")


def load_items(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        st.error(f"Файл хранилища не найден: {path}")
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("items", [])


def build_dataframe(items: List[Dict[str, Any]]) -> pd.DataFrame:
    if not items:
        return pd.DataFrame(columns=["source", "title", "url", "published", "published_raw", "summary"])

    df = pd.DataFrame(items)
    # нормализуем столбцы
    for col in ["source", "title", "url", "published", "published_raw", "summary"]:
        if col not in df.columns:
            df[col] = ""

    # приводим published к datetime, где возможно
    def parse_dt(val):
        if not isinstance(val, str) or not val:
            return pd.NaT
        try:
            return datetime.fromisoformat(val)
        except Exception:
            return pd.NaT

    df["published_dt"] = df["published"].apply(parse_dt)
    return df


def tokenize(text: str) -> List[str]:
    words = WORD_RE.findall((text or "").lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 2]


def compute_word_freq(df: pd.DataFrame) -> Counter:
    cnt = Counter()
    for _, row in df.iterrows():
        parts = [row.get("title", ""), row.get("summary", "")]
        txt = " ".join(p for p in parts if isinstance(p, str))
        cnt.update(tokenize(txt))
    return cnt


def main():
    st.set_page_config(page_title="Аналитика новостей ЦБП/упаковка", layout="wide")

    st.title("📊 Аналитика новостей по ЦБП и упаковке")

    # --- Сайдбар: выбор файла и фильтров ---
    st.sidebar.header("Настройки")

    store_path = st.sidebar.text_input("Путь к news_store.json", STORE_DEFAULT)

    items = load_items(store_path)
    if not items:
        st.stop()

    df = build_dataframe(items)

    st.sidebar.write(f"Загружено записей: **{len(df)}**")

    # Фильтр по источникам
    sources = sorted(df["source"].dropna().unique())
    chosen_sources = st.sidebar.multiselect("Источники", sources, default=sources)

    # Фильтр по дате (по published_dt, если есть)
    min_dt = df["published_dt"].min()
    max_dt = df["published_dt"].max()
    use_date_filter = False
    if pd.notna(min_dt) and pd.notna(max_dt):
        use_date_filter = st.sidebar.checkbox("Фильтровать по дате", value=False)
        if use_date_filter:
            start_date, end_date = st.sidebar.date_input(
                "Диапазон дат (по published)",
                value=(min_dt.date(), max_dt.date()),
            )
    else:
        st.sidebar.info("Корректных дат в published почти нет — фильтр по дате недоступен.")

    top_n_words = st.sidebar.slider("Сколько слов показать в топе", min_value=10, max_value=100, value=30, step=5)

    # --- Применяем фильтры ---
    filtered = df.copy()

    if chosen_sources:
        filtered = filtered[filtered["source"].isin(chosen_sources)]

    if use_date_filter and pd.notna(min_dt) and pd.notna(max_dt):
        start_ts = datetime.combine(start_date, datetime.min.time())
        end_ts = datetime.combine(end_date, datetime.max.time())
        mask = (filtered["published_dt"] >= start_ts) & (filtered["published_dt"] <= end_ts)
        filtered = filtered[mask]

    st.subheader("Отфильтрованные новости")
    st.write(f"Всего отфильтровано: **{len(filtered)}**")

    # показываем таблицу
    show_columns = ["source", "published", "published_raw", "title", "url", "summary"]
    st.dataframe(filtered[show_columns], use_container_width=True)

    # --- Частотный анализ слов ---
    st.subheader("Топ слов в заголовках и аннотациях")

    if len(filtered) == 0:
        st.info("Нет новостей под текущие фильтры.")
    else:
        freq = compute_word_freq(filtered)
        top_words = freq.most_common(top_n_words)
        if not top_words:
            st.info("Не удалось построить частотный список (слишком мало текста).")
        else:
            freq_df = pd.DataFrame(top_words, columns=["word", "count"])
            col1, col2 = st.columns([1, 2])

            with col1:
                st.write("Таблица топ-слов:")
                st.dataframe(freq_df, use_container_width=True, height=400)

            with col2:
                st.write("Гистограмма топ-слов:")
                st.bar_chart(freq_df.set_index("word"))

    # --- Немного общей статистики ---
    st.subheader("Общая статистика")

    col_a, col_b = st.columns(2)

    with col_a:
        by_source = filtered["source"].value_counts().rename_axis("source").reset_index(name="count")
        st.write("Новости по источникам:")
        st.bar_chart(by_source.set_index("source"))

    with col_b:
        if filtered["published_dt"].notna().any():
            per_month = (
                filtered.dropna(subset=["published_dt"])
                .assign(month=lambda d: d["published_dt"].dt.to_period("M").astype(str))
                .groupby("month")
                .size()
                .rename("count")
                .reset_index()
            )
            st.write("Новости по месяцам (где есть даты):")
            st.line_chart(per_month.set_index("month"))
        else:
            st.info("Нет достаточного количества корректных дат для графика по месяцам.")


if __name__ == "__main__":
    main()
