#!/usr/bin/env python3
import json
import os
import re
import ssl
import uuid
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime
from collections import Counter
from typing import List, Dict, Any, Optional


def _load_dotenv(path: str = ".env") -> None:
    """Загружает ключи из .env файла в переменные окружения."""
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

PROVIDER_BADGE = {
    "gigachat": ("#21A038", "GigaChat"),
    "gemini":   ("#4285F4", "Gemini"),
    "groq":     ("#F55036", "Groq"),
    "ollama":   ("#7C3AED", "Ollama"),
}

SYSTEM_PROMPT = (
    "Ты — аналитический ассистент, специализирующийся на новостях "
    "целлюлозно-бумажной и упаковочной промышленности. "
    "Твоя задача — кратко изложить суть новости в 2–3 предложениях на русском языке. "
    "Пиши нейтрально и по существу: укажи ключевое событие, участников (если есть) "
    "и его значение для отрасли. Не используй вводные фразы вроде «В данной новости...»."
)

REQUEST_TIMEOUT = 60


# ─────────────────────────────────────────────
# Хранилище
# ─────────────────────────────────────────────

def load_store(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"items": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_store(path: str, store: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_items(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        st.error(f"Файл хранилища не найден: {path}")
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("items", [])


# ─────────────────────────────────────────────
# AI — вызовы провайдеров
# ─────────────────────────────────────────────

def _build_user_text(title: str, summary: Optional[str]) -> str:
    if summary and len(summary.strip()) > 20:
        return f"Заголовок: {title}\n\nТекст: {summary}"
    return f"Заголовок: {title}"


def _http_post(url: str, payload: dict, headers: dict, verify_ssl: bool = True) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    ctx = None
    if not verify_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _openai_compatible(url: str, api_key: str, model: str, title: str, original: str) -> str:
    payload = {
        "model": model, "max_tokens": 600, "temperature": 0.3,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_text(title, original)},
        ],
    }
    result = _http_post(url, payload, {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    return result["choices"][0]["message"]["content"].strip()


def call_gemini(title: str, original: str, api_key: str, model: str = "gemini-1.5-flash") -> str:
    combined = f"{SYSTEM_PROMPT}\n\n{_build_user_text(title, original)}"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    result = _http_post(url, {
        "contents": [{"parts": [{"text": combined}]}],
        "generationConfig": {"maxOutputTokens": 600, "temperature": 0.3},
    }, {"Content-Type": "application/json"})
    return result["candidates"][0]["content"]["parts"][0]["text"].strip()


def call_groq(title: str, original: str, api_key: str, model: str = "llama3-8b-8192") -> str:
    return _openai_compatible("https://api.groq.com/openai/v1/chat/completions", api_key, model, title, original)


def _gigachat_token(credentials: str) -> str:
    data = urllib.parse.urlencode({"scope": "GIGACHAT_API_PERS"}).encode("utf-8")
    req = urllib.request.Request(
        "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
        data=data,
        headers={
            "Authorization": f"Basic {credentials}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))["access_token"]


def call_gigachat(title: str, original: str, credentials: str, model: str = "GigaChat") -> str:
    token = _gigachat_token(credentials)
    payload = {
        "model": model, "max_tokens": 600, "temperature": 0.3,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_text(title, original)},
        ],
    }
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://gigachat.devices.sberbank.ru/api/v1/chat/completions",
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=ctx) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    return result["choices"][0]["message"]["content"].strip()


def call_ollama(title: str, original: str, model: str = "llama3") -> str:
    payload = {
        "model": model,
        "prompt": f"{SYSTEM_PROMPT}\n\n{_build_user_text(title, original)}",
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 600},
    }
    result = _http_post("http://localhost:11434/api/generate", payload, {"Content-Type": "application/json"})
    return result["response"].strip()


def generate_summary(provider: str, title: str, original: str, api_key: str = "", model: str = "") -> str:
    """Единая точка вызова нужного провайдера."""
    if provider == "gigachat":
        return call_gigachat(title, original, api_key, model or "GigaChat")
    elif provider == "gemini":
        return call_gemini(title, original, api_key, model or "gemini-1.5-flash")
    elif provider == "groq":
        return call_groq(title, original, api_key, model or "llama3-8b-8192")
    elif provider == "ollama":
        return call_ollama(title, original, model or "llama3")
    else:
        raise ValueError(f"Неизвестный провайдер: {provider}")



# ─────────────────────────────────────────────
# Страновая принадлежность источников
# ─────────────────────────────────────────────

SOURCE_COUNTRY = {
    # Россия
    "sbo-paper":          "Россия",
    "rosinvest-bumles":   "Россия",
    "upackunion-stati":   "Россия",
    "lesprominform-news": "Россия",
    # Индия
    "paper-mart-india":       "Индия",
    "thepackman-india":       "Индия",
    "pulpandpapertimes-india": "Индия",
    # Вьетнам (БРИКС+)
    "paper-vietnam-news": "Вьетнам",
    # Египет (БРИКС+)
    "daily-news-egypt":   "Египет",
    # Иран (БРИКС+)
    "financial-tribune-iran": "Иран",
    # ЮАР (БРИКС)
    "the-paper-story":    "ЮАР",
    # Глобальные / Европа
    "tissueworldmagazine":    "Глобально",
    "packaging-gateway":      "Глобально",
    "papnews":                "Европа",
    "packaging-europe":       "Европа",
    "paper-technology-intl":  "Глобально",
    "paperadvance":           "Канада/Глобально",
    "paperage":               "США",
    "pulpapernews":           "Европа",
    "packaging-strategies":   "США",
}

COUNTRY_GROUPS = {
    "🇷🇺 Россия":          ["Россия"],
    "🇮🇳 Индия":           ["Индия"],
    "🇧🇷 Бразилия":        ["Бразилия"],
    "🇨🇳 Китай":           ["Китай"],
    "🌍 БРИКС+ (другие)":  ["ЮАР", "Египет", "Иран", "Вьетнам", "Таиланд", "ОАЭ"],
    "🌐 США / Канада":     ["США", "Канада/Глобально"],
    "🇪🇺 Европа":          ["Европа"],
    "🌏 Глобально":        ["Глобально"],
}

COUNTRY_COLORS = {
    "🇷🇺 Россия":         "#CC0000",
    "🇮🇳 Индия":          "#FF9933",
    "🇧🇷 Бразилия":       "#009C3B",
    "🇨🇳 Китай":          "#DE2910",
    "🌍 БРИКС+ (другие)": "#8B5CF6",
    "🌐 США / Канада":    "#3B82F6",
    "🇪🇺 Европа":         "#003399",
    "🌏 Глобально":       "#64748B",
}

COUNTRY_KW = {
    "Россия":   ["россия", "российск", "рубл", "москв", "петербург", "сыктывкар",
                 "архангельск", "светогорск", "кондопог", "илим", "монди", "russia", "russian"],
    "Китай":    ["китай", "китайск", "china", "chinese", "beijing", "shanghai",
                 "guangzhou", "shandong", "юань"],
    "Индия":    ["india", "indian", "delhi", "mumbai", "rupee", "инди"],
    "Бразилия": ["brazil", "brazilian", "brasilia", "бразили", "real"],
    "Таиланд":  ["thailand", "thai", "bangkok", "таиланд"],
    "Вьетнам":  ["vietnam", "vietnamese", "hanoi", "вьетнам"],
    "ЮАР":      ["south africa", "johannesburg", "sappi", "юар"],
    "Египет":   ["egypt", "egyptian", "cairo", "египет"],
    "Иран":     ["iran", "iranian", "tehran", "иран"],
}


def detect_country(row) -> str:
    """Определяет страну новости: сначала по источнику, потом по тексту."""
    # 1. По источнику
    src_country = SOURCE_COUNTRY.get(row.get("source", ""), "")
    if src_country:
        return src_country
    # 2. По тексту (заголовок + резюме)
    text = " ".join([
        str(row.get("title", "") or ""),
        str(row.get("summary", "") or ""),
    ]).lower()
    for country, keywords in COUNTRY_KW.items():
        if any(kw in text for kw in keywords):
            return country
    return "Глобально"


def get_group(country: str) -> str:
    """Возвращает группу БРИКС для страны."""
    for group, countries in COUNTRY_GROUPS.items():
        if country in countries:
            return group
    return "🌏 Глобально"


# ─────────────────────────────────────────────
# Сегменты отрасли
# ─────────────────────────────────────────────

SEGMENTS: dict = {
    "Pulp / Целлюлоза":         ["pulp", "целлюлоз", "pulping", "kraft pulp", "dissolving pulp",
                                  "softwood pulp", "hardwood pulp", "bleached pulp"],
    "Paper / Бумага":           ["paper machine", "paper mill", "newsprint", "бумажн", "бумага",
                                  "fine paper", "graphic paper", "copy paper", "офсет"],
    "Paperboard / Картон":      ["paperboard", "board machine", "картон", "cartonboard",
                                  "solid board", "folding boxboard", "fbb", "грейбор"],
    "Cardboard / Гофро":        ["cardboard", "corrugated", "гофрокартон", "гофро", "kraftliner",
                                  "containerboard", "fluting", "testliner", "schrenz"],
    "Packaging / Упаковка":     ["packaging", "упаковк", "package", "пакет", "bag", "sachet",
                                  "flexible packaging", "rigid packaging", "pouch"],
    "Tissue / Тиссью":          ["tissue", "тиссью", "туалетн", "салфетк", "towel", "hygiene",
                                  "санитарно-гигиен", "facial tissue", "bathroom tissue"],
    "Molded Fiber":             ["molded fiber", "molded pulp", "формован", "яичн лоток",
                                  "bagasse", "fiber tray"],
    "Recycled Fiber / Макулатура": ["recycled fiber", "recovered paper", "waste paper", "макулатур",
                                    "deinking", "деинкинг", "secondary fiber", "occ"],
    "Nanocellulose / Нано":     ["nanocellulose", "nfc", "cnf", "cnc", "cellulose nanofiber",
                                  "nanofibril", "microfibrillated", "mfc", "наноцеллюлоз"],
    "Biorefinery / Биорафинад": ["biorefinery", "biorefining", "биорафинад", "lignin",
                                  "лигнин", "hemicellulose", "tall oil", "биоэкономик"],
    "Barrier & Coating":        ["barrier coating", "pfas-free", "pfas free", "without pfas",
                                  "barrier paper", "repulpable", "recyclable barrier",
                                  "water resistance", "grease resistant", "покрыти"],
}

SEGMENT_COLORS: dict = {
    "Pulp / Целлюлоза":            "#8B4513",
    "Paper / Бумага":              "#4A90D9",
    "Paperboard / Картон":         "#7B68EE",
    "Cardboard / Гофро":           "#D2691E",
    "Packaging / Упаковка":        "#2E8B57",
    "Tissue / Тиссью":             "#FF69B4",
    "Molded Fiber":                "#6B8E23",
    "Recycled Fiber / Макулатура": "#708090",
    "Nanocellulose / Нано":        "#00CED1",
    "Biorefinery / Биорафинад":    "#228B22",
    "Barrier & Coating":           "#FF8C00",
}


def detect_segments(row) -> list:
    """Возвращает список сегментов для новости (может быть несколько)."""
    text = " ".join([
        str(row.get("title", "") or ""),
        str(row.get("summary", "") or ""),
    ]).lower()
    found = []
    for segment, keywords in SEGMENTS.items():
        if any(kw in text for kw in keywords):
            found.append(segment)
    return found if found else ["Другое"]


def detect_segment_primary(row) -> str:
    """Возвращает основной (первый найденный) сегмент."""
    return detect_segments(row)[0]

# ─────────────────────────────────────────────
# DataFrame
# ─────────────────────────────────────────────

def provider_badge(provider: str, model: str) -> str:
    color, label = PROVIDER_BADGE.get(provider, ("#888", provider or "AI"))
    model_str = f" · {model}" if model and model != label else ""
    return (
        f'<span style="background:{color}22; color:{color}; border:1px solid {color}55;'
        f'border-radius:3px; padding:1px 8px; font-size:11px; font-weight:700; letter-spacing:0.5px;">'
        f'⚡ {label}{model_str}</span>'
    )


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
    df["country"] = df.apply(detect_country, axis=1)
    df["country_group"] = df["country"].apply(get_group)
    df["segment"] = df.apply(detect_segment_primary, axis=1)
    df["segments_all"] = df.apply(detect_segments, axis=1)
    for col in ["summary_provider", "summary_model"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("")
    return df


# ─────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────

DARK_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');
html, body, [class*="css"] { font-family: 'IBM Plex Sans', sans-serif; }
.stApp { background: #0d0d1a; color: #ddd; }
.stSidebar { background: #111126 !important; }
.metric-card { background: #1a1a2e; border-radius: 10px; padding: 18px 20px; border: 1px solid #2a2a4a; text-align: center; }
.metric-value { font-family: 'IBM Plex Mono', monospace; font-size: 32px; font-weight: 500; color: #4a9eff; line-height: 1; }
.metric-label { font-size: 12px; color: #888; margin-top: 6px; letter-spacing: 0.5px; text-transform: uppercase; }
h1, h2, h3 { color: #eee !important; }
.stTabs [data-baseweb="tab"] { color: #aaa; }
.stTabs [aria-selected="true"] { color: #4a9eff !important; }
.news-card-header { border-left: 4px solid var(--card-color, #4a9eff); background: #1a1a2e; border-radius: 8px; padding: 16px 20px; margin-bottom: 4px; box-shadow: 0 2px 8px rgba(0,0,0,0.3); }
.source-badge { display: inline-block; border-radius: 4px; padding: 2px 10px; font-size: 12px; font-weight: 600; letter-spacing: 0.5px; }
.summary-box { background: #0f3460; border-radius: 6px; padding: 14px 18px; border-left: 3px solid #4CAF50; margin-bottom: 12px; }
.summary-label { color: #4CAF50; font-size: 11px; font-weight: 700; margin-bottom: 8px; letter-spacing: 1px; }
.danger-zone { background: #2a0a0a; border: 1px solid #c73e1d55; border-radius: 8px; padding: 16px 20px; margin-top: 16px; }
</style>
"""


# ─────────────────────────────────────────────
# Полноэкранная карточка
# ─────────────────────────────────────────────

def show_full_card(row: pd.Series, store_path: str):
    source   = row.get("source", "")
    color    = SOURCE_COLORS.get(source, "#4a9eff")
    label    = SOURCE_LABELS.get(source, source)
    title    = row.get("title", "Без заголовка")
    url      = row.get("url", "")
    published = row.get("published_raw") or row.get("published", "")
    summary  = str(row.get("summary", "") or "")
    has_sum  = row.get("has_summary", False)
    news_id  = row.get("id", "")
    sp       = row.get("summary_provider", "")
    sm       = row.get("summary_model", "")

    # ── Кнопка назад ──
    if st.button("← Назад к списку", key="back_btn"):
        st.session_state["open_news_id"] = None
        st.rerun()

    st.markdown(f"""
    <div style="margin: 16px 0 8px 0;">
        <span class="source-badge" style="background:{color}22; color:{color}; border:1px solid {color}55;">{label}</span>
        <span style="color:#888; font-size:13px; margin-left:12px;">{published}</span>
    </div>
    <h2 style="color:#eee; font-size:22px; line-height:1.4; margin: 0 0 20px 0;">{title}</h2>
    """, unsafe_allow_html=True)
    st.markdown(f"<div style='width:100%; height:3px; background:linear-gradient({color}, transparent); margin-bottom:24px; border-radius:2px;'></div>", unsafe_allow_html=True)

    # ── Текущее резюме ──
    if has_sum and summary.strip():
        st.markdown(
            f'<div class="summary-box"><div class="summary-label">✦ AI-РЕЗЮМЕ &nbsp;&nbsp;{provider_badge(sp, sm)}</div></div>',
            unsafe_allow_html=True
        )
        st.markdown(f"> {summary}")
    else:
        st.info("Резюме ещё не сгенерировано.")

    st.markdown("")

    # ── Ссылка ──
    if url:
        st.markdown(f"**Источник:** [{url}]({url})")

    st.markdown("---")

    # ══════════════════════════════════════════
    # Пересоздать резюме
    # ══════════════════════════════════════════
    with st.expander("🤖 Пересоздать резюме через AI", expanded=not has_sum):
        st.markdown("Выберите нейросеть и нажмите кнопку — резюме будет перезаписано.")

        col_prov, col_key = st.columns([1, 2])
        with col_prov:
            prov_choice = st.selectbox(
                "Нейросеть",
                options=["gigachat", "gemini", "groq", "ollama"],
                format_func=lambda x: {
                    "gigachat": "🟢 GigaChat (Сбер)",
                    "gemini":   "🔵 Gemini (Google)",
                    "groq":     "🔴 Groq",
                    "ollama":   "🟣 Ollama (локально)",
                }[x],
                key=f"prov_{news_id}",
            )
        with col_key:
            needs_key = prov_choice != "ollama"
            env_map = {
                "gigachat": "GIGACHAT_CREDENTIALS",
                "gemini":   "GEMINI_API_KEY",
                "groq":     "GROQ_API_KEY",
            }
            env_var = env_map.get(prov_choice, "")
            default_key = os.environ.get(env_var, "") if env_var else ""
            api_key_input = ""
            if needs_key:
                # key включает prov_choice чтобы поле пересоздавалось при смене провайдера
                api_key_input = st.text_input(
                    f"Ключ ({env_var})",
                    value=default_key,
                    type="password",
                    key=f"key_{news_id}_{prov_choice}",
                    help=f"Автоматически подставлен из {env_var}" if default_key else f"Введите {env_var}",
                )
                if not api_key_input:
                    api_key_input = default_key

        if st.button("✨ Сгенерировать резюме", key=f"gen_{news_id}", type="primary"):
            if needs_key and not api_key_input:
                st.error(f"Введите ключ API или задайте переменную окружения {env_var}")
            else:
                with st.spinner("Генерирую резюме..."):
                    try:
                        new_summary = generate_summary(
                            provider=prov_choice,
                            title=title,
                            original=summary,
                            api_key=api_key_input,
                        )
                        # Сохраняем в хранилище
                        store = load_store(store_path)
                        for item in store.get("items", []):
                            if item.get("id") == news_id:
                                item["summary"] = new_summary
                                item["summary_provider"] = prov_choice
                                item["summary_model"] = {
                                    "gigachat": "GigaChat",
                                    "gemini":   "gemini-1.5-flash",
                                    "groq":     "llama3-8b-8192",
                                    "ollama":   "llama3",
                                }.get(prov_choice, prov_choice)
                                break
                        save_store(store_path, store)
                        st.success("✓ Резюме сохранено!")
                        st.markdown(f"> {new_summary}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Ошибка: {e}")

    # ══════════════════════════════════════════
    # Удалить новость
    # ══════════════════════════════════════════
    with st.expander("🗑 Удалить новость из хранилища"):
        st.markdown('<div class="danger-zone">', unsafe_allow_html=True)
        st.warning("Это действие необратимо — новость будет удалена из `news_store.json`.")
        confirm = st.checkbox("Я понимаю, удалить эту новость", key=f"confirm_del_{news_id}")
        if confirm:
            if st.button("🗑 Удалить", key=f"del_{news_id}", type="primary"):
                store = load_store(store_path)
                store["items"] = [it for it in store.get("items", []) if it.get("id") != news_id]
                save_store(store_path, store)
                st.success("Новость удалена.")
                st.session_state["open_news_id"] = None
                st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    # ── Все поля ──
    with st.expander("📄 Все данные записи"):
        st.json({"id": news_id, "source": source, "title": title, "url": url,
                 "published": row.get("published", ""), "published_raw": published,
                 "summary": summary, "summary_provider": sp, "summary_model": sm})


# ─────────────────────────────────────────────
# Компактная карточка в списке
# ─────────────────────────────────────────────

def show_news_card_compact(row: pd.Series, idx: int):
    source   = row.get("source", "")
    color    = SOURCE_COLORS.get(source, "#555")
    label    = SOURCE_LABELS.get(source, source)
    title    = row.get("title", "Без заголовка")
    url      = row.get("url", "")
    published = row.get("published_raw") or row.get("published", "")
    summary  = str(row.get("summary", "") or "")
    has_sum  = row.get("has_summary", False)
    news_id  = row.get("id", str(idx))
    sp       = row.get("summary_provider", "")
    sm       = row.get("summary_model", "")

    st.markdown(f"""
    <div class="news-card-header" style="--card-color:{color};">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <span class="source-badge" style="background:{color}22; color:{color}; border:1px solid {color}55;">{label}</span>
            <span style="color:#888; font-size:12px;">{published}</span>
        </div>
        <div style="color:#eee; font-size:16px; font-weight:500; line-height:1.4;">{title}</div>
    </div>
    """, unsafe_allow_html=True)

    if has_sum and summary.strip():
        st.markdown(
            f'<div class="summary-box"><div class="summary-label">✦ AI-РЕЗЮМЕ &nbsp;&nbsp;{provider_badge(sp, sm)}</div></div>',
            unsafe_allow_html=True
        )
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
# Токены и анализ
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
# main
# ─────────────────────────────────────────────

def main():
    st.set_page_config(page_title="Аналитика новостей ЦБП", layout="wide", initial_sidebar_state="expanded")
    st.markdown(DARK_CSS, unsafe_allow_html=True)

    if "open_news_id" not in st.session_state:
        st.session_state["open_news_id"] = None

    with st.sidebar:
        st.markdown("### ⚙️ Настройки")
        store_path = st.text_input("Файл хранилища", STORE_DEFAULT)
        st.markdown("---")

    items = load_items(store_path)
    if not items:
        st.stop()

    df = build_dataframe(items)

    # ── Полноэкранная карточка ──
    if st.session_state["open_news_id"] is not None:
        news_id = st.session_state["open_news_id"]
        match = df[df["id"] == news_id]
        if len(match) == 0:
            st.error("Новость не найдена.")
            st.session_state["open_news_id"] = None
        else:
            show_full_card(match.iloc[0], store_path)
        return

    # ── Фильтры ──
    with st.sidebar:
        st.markdown("### 🔍 Фильтры")
        sources = sorted(df["source"].dropna().unique())
        chosen_sources = st.multiselect("Источники", options=sources, default=sources,
                                        format_func=lambda x: SOURCE_LABELS.get(x, x))
        search_query = st.text_input("Поиск по тексту", placeholder="введите слово...")
        only_with_summary = st.checkbox("Только с AI-резюме", value=False)

        # Фильтр по нейросети-автору резюме
        available_providers = sorted([
            p for p in df["summary_provider"].unique() if p and str(p).strip()
        ])
        chosen_providers = []
        if available_providers:
            provider_labels = {
                "gigachat": "🟢 GigaChat",
                "gemini":   "🔵 Gemini",
                "groq":     "🔴 Groq",
                "ollama":   "🟣 Ollama",
            }
            chosen_providers = st.multiselect(
                "Нейросеть резюме",
                options=available_providers,
                default=[],
                format_func=lambda x: provider_labels.get(x, x),
                placeholder="Все нейросети...",
            )

        min_dt = df["published_dt"].min()
        max_dt = df["published_dt"].max()
        use_date = False
        if pd.notna(min_dt) and pd.notna(max_dt):
            use_date = st.checkbox("Фильтр по дате")
            if use_date:
                start_date, end_date = st.date_input("Диапазон", value=(min_dt.date(), max_dt.date()))

        top_n = st.slider("Топ слов", 10, 60, 25, 5)

    # ── Заголовок ──
    st.markdown("""
    <div style="padding: 8px 0 24px 0;">
        <div style="font-family:'IBM Plex Mono',monospace; font-size:11px; color:#4a9eff; letter-spacing:2px; margin-bottom:6px;">МОНИТОРИНГ ОТРАСЛИ</div>
        <h1 style="margin:0; font-size:28px; font-weight:600; color:#eee;">Аналитика новостей ЦБП и упаковки</h1>
    </div>
    """, unsafe_allow_html=True)

    # ── Применяем фильтры ──
    filtered = df.copy()
    if chosen_sources:
        filtered = filtered[filtered["source"].isin(chosen_sources)]
    if only_with_summary:
        filtered = filtered[filtered["has_summary"]]
    if chosen_providers:
        filtered = filtered[filtered["summary_provider"].isin(chosen_providers)]
    if search_query.strip():
        q = search_query.strip().lower()
        filtered = filtered[
            filtered["title"].str.lower().str.contains(q, na=False) |
            filtered["summary"].str.lower().str.contains(q, na=False)
        ]
    if use_date and pd.notna(min_dt) and pd.notna(max_dt):
        from datetime import timezone
        s = pd.Timestamp(datetime.combine(start_date, datetime.min.time()), tz=timezone.utc)
        e = pd.Timestamp(datetime.combine(end_date, datetime.max.time()), tz=timezone.utc)
        filtered = filtered[(filtered["published_dt"] >= s) & (filtered["published_dt"] <= e)]

    # ── Метрики ──
    total = len(filtered)
    with_summary = int(filtered["has_summary"].sum())
    pct = int(with_summary / total * 100) if total else 0
    n_sources = filtered["source"].nunique()
    last_month_count = 0
    if filtered["published_dt"].notna().any():
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30)
        last_month_count = int((filtered["published_dt"] >= cutoff).sum())

    c1, c2, c3, c4 = st.columns(4)
    for col, val, lbl in [(c1, total, "Новостей"), (c2, n_sources, "Источников"),
                           (c3, f"{with_summary} / {pct}%", "С AI-резюме"), (c4, last_month_count, "За 30 дней")]:
        col.markdown(f'<div class="metric-card"><div class="metric-value">{val}</div><div class="metric-label">{lbl}</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📋 Новости", "📈 Аналитика", "☁️ Частотный анализ", "🌍 По странам", "🏭 По сегментам"])

    # ════════════════════════════════════════
    # ВКЛАДКА 1
    # ════════════════════════════════════════
    with tab1:
        view_mode = st.radio("Режим отображения", ["Карточки", "Таблица"], horizontal=True)

        if total == 0:
            st.info("Нет новостей под текущие фильтры.")

        elif view_mode == "Таблица":
            show_cols = ["source_label", "published_raw", "title", "url", "has_summary", "summary_provider"]
            rename_map = {"source_label": "Источник", "published_raw": "Дата", "title": "Заголовок",
                          "url": "Ссылка", "has_summary": "Резюме", "summary_provider": "Нейросеть"}
            st.dataframe(filtered[show_cols].rename(columns=rename_map), width="stretch", height=400)
            st.markdown("#### Открыть карточку")
            chosen_title = st.selectbox("Выберите новость", ["— не выбрано —"] + filtered["title"].tolist())
            if chosen_title != "— не выбрано —":
                match = filtered[filtered["title"] == chosen_title]
                if len(match):
                    if st.button("Открыть полную карточку →"):
                        st.session_state["open_news_id"] = match.iloc[0].get("id", "")
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
    # ВКЛАДКА 2
    # ════════════════════════════════════════
    with tab2:
        if total == 0:
            st.info("Нет данных.")
        else:
            col_l, col_r = st.columns(2)
            with col_l:
                st.markdown("##### Новости по источникам")
                st.bar_chart(filtered.groupby("source_label").size().rename("Количество").sort_values(ascending=False))
            with col_r:
                st.markdown("##### Покрытие AI-резюме по источникам")
                cov = filtered.groupby("source_label")["has_summary"].agg(["sum", "count"]).rename(columns={"sum": "С резюме", "count": "Всего"})
                cov["% покрытия"] = (cov["С резюме"] / cov["Всего"] * 100).round(1)
                st.dataframe(cov, width="stretch")

            if filtered["published_dt"].notna().any():
                st.markdown("##### Динамика публикаций по месяцам")
                tmp = filtered.dropna(subset=["published_dt"]).copy()
                tmp["month"] = tmp["published_dt"].dt.to_period("M").astype(str)
                per_month = tmp.groupby("month").size().rename("Новостей").reset_index().sort_values("month")
                st.line_chart(per_month.set_index("month"))

            if filtered["has_summary"].any():
                st.markdown("##### Распределение резюме по нейросетям")
                prov_df = (
                    filtered[filtered["has_summary"] & (filtered["summary_provider"] != "")]
                    .groupby("summary_provider").size()
                    .rename("Количество резюме").reset_index()
                    .rename(columns={"summary_provider": "Провайдер"})
                )
                if len(prov_df):
                    col_p1, col_p2 = st.columns([1, 2])
                    with col_p1:
                        st.dataframe(prov_df, width="stretch", hide_index=True)
                    with col_p2:
                        st.bar_chart(prov_df.set_index("Провайдер"))

            no_summary = filtered[~filtered["has_summary"]][["source_label", "title"]]
            if len(no_summary):
                with st.expander(f"📭 Новости без резюме ({len(no_summary)} шт.)"):
                    st.dataframe(no_summary.rename(columns={"source_label": "Источник", "title": "Заголовок"}), width="stretch")

    # ════════════════════════════════════════
    # ВКЛАДКА 3
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
                    st.dataframe(freq_df, width="stretch", height=450)
                with col_b:
                    st.markdown("##### Частота упоминаний")
                    st.bar_chart(freq_df.set_index("Слово"))

                st.markdown("##### Топ-15 слов по каждому источнику")
                if chosen_sources:
                    src_cols = st.columns(min(len(chosen_sources), 3))
                    for i, src in enumerate(chosen_sources):
                        src_freq = compute_word_freq(filtered[filtered["source"] == src])
                        top_src = src_freq.most_common(15)
                        if top_src:
                            with src_cols[i % len(src_cols)]:
                                clr = SOURCE_COLORS.get(src, "#4a9eff")
                                st.markdown(f"<span style='color:{clr}; font-weight:600;'>{SOURCE_LABELS.get(src, src)}</span>", unsafe_allow_html=True)
                                st.dataframe(pd.DataFrame(top_src, columns=["Слово", "Частота"]), width="stretch", height=350)


    # ════════════════════════════════════════
    # ВКЛАДКА 4 — По странам (БРИКС)
    # ════════════════════════════════════════
    with tab4:
        if total == 0:
            st.info("Нет данных.")
        else:
            # ── Метрики по группам ──
            st.markdown("##### Распределение новостей по регионам")
            group_counts = filtered["country_group"].value_counts().reset_index()
            group_counts.columns = ["Регион", "Новостей"]

            col_gc1, col_gc2 = st.columns([1, 2])
            with col_gc1:
                st.dataframe(group_counts, width="stretch", hide_index=True)
            with col_gc2:
                st.bar_chart(group_counts.set_index("Регион"))

            st.markdown("---")

            # ── Вкладки по группам ──
            groups_present = [g for g in COUNTRY_GROUPS if g in filtered["country_group"].values]
            if not groups_present:
                st.info("Нет данных по странам.")
            else:
                group_tabs = st.tabs(groups_present)
                for gtab, group_name in zip(group_tabs, groups_present):
                    with gtab:
                        group_df = filtered[filtered["country_group"] == group_name]
                        gcolor = COUNTRY_COLORS.get(group_name, "#64748B")

                        # Метрики группы
                        g_total = len(group_df)
                        g_with_sum = int(group_df["has_summary"].sum())
                        gc1, gc2, gc3 = st.columns(3)
                        for col, val, lbl in [
                            (gc1, g_total, "Новостей"),
                            (gc2, group_df["source"].nunique(), "Источников"),
                            (gc3, g_with_sum, "С резюме"),
                        ]:
                            col.markdown(
                                f'<div class="metric-card"><div class="metric-value" '
                                f'style="color:{gcolor};">{val}</div>'
                                f'<div class="metric-label">{lbl}</div></div>',
                                unsafe_allow_html=True
                            )
                        st.markdown("<br>", unsafe_allow_html=True)

                        if g_total == 0:
                            st.info("Нет новостей для этого региона.")
                            continue

                        # Разбивка по странам внутри группы
                        countries_in_group = group_df["country"].value_counts()
                        if len(countries_in_group) > 1:
                            st.markdown("**По странам:**")
                            st.bar_chart(countries_in_group)

                        # Разбивка по источникам
                        st.markdown("**По источникам:**")
                        src_counts = group_df.groupby("source_label").size().rename("Новостей")
                        st.bar_chart(src_counts)

                        # Топ слов
                        top_words_g = compute_word_freq(group_df).most_common(20)
                        if top_words_g:
                            st.markdown("**Топ-20 слов:**")
                            wdf = pd.DataFrame(top_words_g, columns=["Слово", "Частота"])
                            st.bar_chart(wdf.set_index("Слово"))

                        # Список новостей
                        with st.expander(f"📋 Все новости региона ({g_total} шт.)"):
                            show = group_df[["source_label", "published_raw", "title", "country", "url"]].rename(columns={
                                "source_label": "Источник", "published_raw": "Дата",
                                "title": "Заголовок", "country": "Страна", "url": "Ссылка"
                            })
                            st.dataframe(show, width="stretch")



    # ════════════════════════════════════════
    # ВКЛАДКА 5 — По сегментам отрасли
    # ════════════════════════════════════════
    with tab5:
        if total == 0:
            st.info("Нет данных.")
        else:
            # ── Общее распределение по сегментам ──
            st.markdown("##### Распределение новостей по сегментам отрасли")

            # Разворачиваем списки segments_all для подсчёта
            seg_counts: dict = {}
            for segs in filtered["segments_all"]:
                for s in segs:
                    seg_counts[s] = seg_counts.get(s, 0) + 1

            seg_df = (
                pd.DataFrame(list(seg_counts.items()), columns=["Сегмент", "Новостей"])
                .sort_values("Новостей", ascending=False)
                .reset_index(drop=True)
            )

            col_s1, col_s2 = st.columns([1, 2])
            with col_s1:
                st.dataframe(seg_df, width="stretch", hide_index=True)
            with col_s2:
                st.bar_chart(seg_df.set_index("Сегмент"))

            st.markdown("---")

            # ── Детализация по каждому сегменту ──
            segments_present = [s for s in SEGMENTS if s in seg_counts]
            if "Другое" in seg_counts:
                segments_present.append("Другое")

            if segments_present:
                chosen_seg = st.selectbox(
                    "Выберите сегмент для детализации",
                    options=segments_present,
                    format_func=lambda x: f"{x}  ({seg_counts.get(x, 0)} новостей)",
                )

                seg_color = SEGMENT_COLORS.get(chosen_seg, "#4a9eff")
                seg_filtered = filtered[filtered["segments_all"].apply(lambda s: chosen_seg in s)]

                # Метрики сегмента
                sm1, sm2, sm3 = st.columns(3)
                for col, val, lbl in [
                    (sm1, len(seg_filtered), "Новостей"),
                    (sm2, seg_filtered["source"].nunique(), "Источников"),
                    (sm3, int(seg_filtered["has_summary"].sum()), "С резюме"),
                ]:
                    col.markdown(
                        f'<div class="metric-card"><div class="metric-value" '
                        f'style="color:{seg_color};">{val}</div>'
                        f'<div class="metric-label">{lbl}</div></div>',
                        unsafe_allow_html=True
                    )
                st.markdown("<br>", unsafe_allow_html=True)

                col_sl, col_sr = st.columns(2)
                with col_sl:
                    st.markdown("**По источникам:**")
                    src_s = seg_filtered.groupby("source_label").size().rename("Новостей").sort_values(ascending=False)
                    st.bar_chart(src_s)
                with col_sr:
                    st.markdown("**По странам:**")
                    ctr_s = seg_filtered.groupby("country").size().rename("Новостей").sort_values(ascending=False)
                    st.bar_chart(ctr_s)

                # Топ слов сегмента
                top_seg_words = compute_word_freq(seg_filtered).most_common(20)
                if top_seg_words:
                    st.markdown("**Топ-20 слов сегмента:**")
                    st.bar_chart(pd.DataFrame(top_seg_words, columns=["Слово", "Частота"]).set_index("Слово"))

                # Список новостей сегмента
                with st.expander(f"📋 Все новости сегмента «{chosen_seg}» ({len(seg_filtered)} шт.)"):
                    show_seg = seg_filtered[["source_label", "published_raw", "title", "country", "url"]].rename(columns={
                        "source_label": "Источник", "published_raw": "Дата",
                        "title": "Заголовок", "country": "Страна", "url": "Ссылка",
                    })
                    st.dataframe(show_seg, width="stretch")

            # ── Матрица сегмент × страна ──
            st.markdown("---")
            st.markdown("##### Матрица: сегменты × страны")
            matrix_data = []
            for _, row in filtered.iterrows():
                for seg in row["segments_all"]:
                    matrix_data.append({"Сегмент": seg, "Страна": row["country"]})
            if matrix_data:
                matrix_df = pd.DataFrame(matrix_data)
                pivot = matrix_df.groupby(["Сегмент", "Страна"]).size().unstack(fill_value=0)
                st.dataframe(pivot, width="stretch")

if __name__ == "__main__":
    main()

"""streamlit run news_app.py"""
