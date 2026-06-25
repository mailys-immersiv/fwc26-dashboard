"""
FWC 26 — Traffic Analytics Dashboard
Source : onglet "FWC26 - Raw data - DO NOT TOUCH"
Sheet public (visible par le lien) — aucun credential requis.
SHEET_ID est lu depuis st.secrets["SHEET_ID"] ou la variable d'env SHEET_ID.
"""

import os
import re
from datetime import timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="FWC 26 · Traffic Dashboard",
    page_icon="⚽",
    layout="wide",
)

SHEET_ID   = st.secrets.get("SHEET_ID", os.environ.get("SHEET_ID", ""))
SHEET_NAME = "FWC26 - Raw data - DO NOT TOUCH"
YEAR       = 2026

if not SHEET_ID:
    st.error("Variable **SHEET_ID** manquante. Ajoutez-la dans les Secrets Streamlit Cloud.")
    st.stop()

CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
    f"/gviz/tq?tqx=out:csv&sheet={SHEET_NAME.replace(' ', '%20')}"
)

# ── Data helpers ───────────────────────────────────────────────────────────────

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Accepte "12 Jun 00:00" et "12 Jun, 00:00" (avec ou sans virgule)
FULL_DATE_RE = re.compile(r"^\s*(\d{1,2})\s+([A-Za-z]{3}),?\s+(\d{1,2}:\d{2})\s*$")
TIME_ONLY_RE = re.compile(r"^\s*(\d{1,2}:\d{2})\s*$")


def _forward_fill_dates(series: pd.Series) -> pd.Series:
    current_day = None
    result = []
    for raw_val in series:
        s = str(raw_val).strip() if not pd.isna(raw_val) else ""
        m_full = FULL_DATE_RE.match(s)
        m_time = TIME_ONLY_RE.match(s)
        if m_full:
            day  = m_full.group(1).zfill(2)
            mon  = m_full.group(2).capitalize()
            time = m_full.group(3)
            current_day = f"{day} {mon}"
            result.append(f"{current_day} {time}")
        elif m_time and current_day:
            result.append(f"{current_day} {m_time.group(1)}")
        else:
            result.append(None)
    return pd.Series(result, index=series.index)


def _parse_datetime(s) -> pd.Timestamp:
    if s is None or pd.isna(s):
        return pd.NaT
    try:
        parts = str(s).strip().split()
        day   = int(parts[0])
        month = MONTH_MAP.get(parts[1].lower(), 0)
        hh, mm = map(int, parts[2].split(":"))
        return pd.Timestamp(YEAR, month, day, hh, mm)
    except Exception:
        return pd.NaT


def _duration_to_minutes(value) -> float | None:
    if pd.isna(value) or str(value).strip() == "":
        return None
    parts = str(value).strip().split(":")
    try:
        if len(parts) == 3:
            h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
            return h * 60 + m + s / 60
        if len(parts) == 2:
            m, s = int(parts[0]), float(parts[1])
            return m + s / 60
    except ValueError:
        pass
    return None


def _to_numeric_col(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).str.strip(),
        errors="coerce",
    )


def _clean_match(series: pd.Series) -> pd.Series:
    raw = series.where(series.notna(), "")
    raw = raw.astype(str).str.strip()
    raw = raw.replace({"nan": "", "none": "", "None": "", "NaN": ""}, regex=False)
    # Garder uniquement la première occurrence de chaque match consécutif
    return raw.where(raw != raw.shift(), "")


def _clean(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    df.columns = [c.strip() for c in df.columns]

    required = ["Date", "Total Visitors", "Average session time"]
    missing  = [c for c in required if c not in df.columns]
    if missing:
        st.error(
            f"Colonnes manquantes : **{missing}**\n\n"
            f"Colonnes trouvées : `{list(df.columns)}`"
        )
        st.stop()

    df["_date_str"]      = _forward_fill_dates(df["Date"])
    df["DateTime"]       = df["_date_str"].apply(_parse_datetime)
    df["Total Visitors"] = _to_numeric_col(df["Total Visitors"])
    df["Session (min)"]  = df["Average session time"].apply(_duration_to_minutes)

    for col in ["New Visitors", "Returning Visitors"]:
        df[col] = _to_numeric_col(df[col]) if col in df.columns else None

    # Daily users : valeur présente uniquement à 00:00, on la propage sur toute la journée
    if "Daily users" in df.columns:
        df["Daily users"] = _to_numeric_col(df["Daily users"])
        df["Daily Visitors"] = df["Daily users"].ffill()
    else:
        df["Daily Visitors"] = None

    df["BBC Match"] = _clean_match(df["BBC Matches"]) if "BBC Matches" in df.columns else ""
    df["FWC Match"] = _clean_match(df["FWC Matches"]) if "FWC Matches" in df.columns else ""

    return (
        df.dropna(subset=["DateTime"])
        .sort_values("DateTime")
        .reset_index(drop=True)
    )


# ── Data loading ───────────────────────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner="📡 Chargement depuis Google Sheets…")
def load_data() -> pd.DataFrame:
    try:
        raw = pd.read_csv(CSV_URL, header=0)
    except Exception as exc:
        st.error(
            f"**Erreur de connexion Google Sheets** : {exc}\n\n"
            "Vérifiez que le sheet est bien partagé en 'Visible par le lien'."
        )
        st.stop()
    return _clean(raw)


# ── Chart ──────────────────────────────────────────────────────────────────────

# Palette des courbes axe gauche
LEFT_TRACES = {
    "Total Visitors":    dict(color="#1a6cdb", fill=True,  step=False),
    "New Visitors":      dict(color="#16a34a", fill=False, step=False),
    "Returning Visitors":dict(color="#9333ea", fill=False, step=False),
    "Daily Visitors":    dict(color="#e63946", fill=False, step=True),
}

def _match_label(m):
    return m if (isinstance(m, str) and m.strip() not in ("", "nan", "none", "null")) else "Pas de match"


def build_figure(df: pd.DataFrame, show: dict, show_bbc: bool = True, show_fwc: bool = True) -> go.Figure:
    fig = go.Figure()

    bbc_labels = df["BBC Match"].apply(_match_label) if "BBC Match" in df.columns else pd.Series("Pas de match", index=df.index)
    fwc_labels = df["FWC Match"].apply(_match_label) if "FWC Match" in df.columns else pd.Series("Pas de match", index=df.index)

    # Hover : priorité BBC puis FWC
    hover_match = bbc_labels.where(bbc_labels != "Pas de match", fwc_labels)

    # ── Courbes axe gauche ─────────────────────────────────────────────────
    for col, style in LEFT_TRACES.items():
        if col not in df.columns or df[col].isna().all():
            continue
        fig.add_trace(go.Scatter(
            x=df["DateTime"], y=df[col],
            name=col, yaxis="y1",
            mode="lines",
            visible=True if show.get(col, True) else "legendonly",
            line=dict(color=style["color"], width=2,
                      shape="hv" if style.get("step") else "linear"),
            fill="tozeroy" if style["fill"] else "none",
            fillcolor="rgba(26,108,219,0.10)" if style["fill"] else None,
            customdata=hover_match,
            hovertemplate=(
                f"<b>%{{x|%d %b %H:%M}}</b><br>"
                f"{col} : <b>%{{y:,}}</b><br>"
                "Match : %{customdata}<extra></extra>"
            ),
        ))

    # ── Courbe axe droit : Session ─────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=df["DateTime"], y=df["Session (min)"],
        name="Session moy. (min)", yaxis="y2",
        mode="lines",
        visible=True if show.get("Session (min)", True) else "legendonly",
        line=dict(color="#e88a00", width=2),
        customdata=hover_match,
        hovertemplate=(
            "<b>%{x|%d %b %H:%M}</b><br>"
            "Session : <b>%{y:.1f} min</b><br>"
            "Match : %{customdata}<extra></extra>"
        ),
    ))

    # ── Lignes verticales matchs ───────────────────────────────────────────
    # Collecte tous les événements, BBC prioritaire sur FWC si même datetime
    events: dict = {}  # datetime → (label, line_color, text_color, icon)

    # FWC en premier (priorité basse)
    if show_fwc and "FWC Match" in df.columns:
        for _, row in df[df["FWC Match"] != ""].iterrows():
            events[row["DateTime"]] = (
                row["FWC Match"],
                "rgba(30,120,200,0.6)", "#1a6cdb", "🏆",
            )
    # BBC en second (priorité haute — écrase FWC si même datetime)
    if show_bbc and "BBC Match" in df.columns:
        for _, row in df[df["BBC Match"] != ""].iterrows():
            events[row["DateTime"]] = (
                row["BBC Match"],
                "rgba(200,30,30,0.6)", "#c01e1e", "🇬🇧",
            )

    shapes, annotations = [], []
    for xv, (label, line_color, text_color, icon) in events.items():
        shapes.append(dict(
            type="line", x0=xv, x1=xv,
            yref="paper", y0=0, y1=1,
            line=dict(color=line_color, width=1.5, dash="dash"),
        ))
        annotations.append(dict(
            x=xv, yref="paper", y=1.01,
            text=f"{icon} {label}",
            showarrow=False, textangle=-45,
            font=dict(size=8.5, color=text_color),
            xanchor="left",
        ))

    range_buttons = [
        dict(count=1, label="24 h",    step="day", stepmode="backward"),
        dict(count=3, label="3 jours", step="day", stepmode="backward"),
        dict(count=7, label="7 jours", step="day", stepmode="backward"),
        dict(step="all", label="Tout"),
    ]

    fig.update_layout(
        title="<b>FWC 26 — Trafic horaire</b> · Visiteurs & Durée de session",
        height=580,
        uirevision="stable",   # préserve zoom/pan entre les reruns Streamlit
        hovermode="x unified",
        shapes=shapes,
        annotations=annotations,
        legend=dict(orientation="h", y=1.06, x=0),
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        font=dict(color="#111111"),
        xaxis=dict(
            title="Date / Heure",
            gridcolor="#e8e8e8",
            rangeselector=dict(buttons=range_buttons),
            rangeslider=dict(visible=True, thickness=0.06),
            type="date",
        ),
        yaxis=dict(
            title="Visiteurs",
            gridcolor="#e8e8e8",
            rangemode="tozero",
        ),
        yaxis2=dict(
            title="Session moyenne (min)",
            title_font=dict(color="#e88a00"),
            tickfont=dict(color="#e88a00"),
            overlaying="y", side="right",
            rangemode="tozero", showgrid=False,
        ),
        margin=dict(t=100, r=70, b=50, l=70),
        dragmode="zoom",
    )
    return fig


# Valeurs par défaut des checkboxes
CHECKBOX_DEFAULTS = {
    "show_Total Visitors":     True,
    "show_New Visitors":       False,
    "show_Returning Visitors": False,
    "show_Daily Visitors":     False,
    "show_Session (min)":      True,
    "show_bbc":                True,
    "show_fwc":                True,
}

def _init_session_state():
    for key, val in CHECKBOX_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = val


# ── Sidebar ────────────────────────────────────────────────────────────────────

def sidebar_controls(df: pd.DataFrame):
    _init_session_state()

    st.sidebar.title("⚽ FWC 26 · Filtres")

    if st.sidebar.button("🔄 Forcer la synchronisation Google Sheets"):
        load_data.clear()
        for key, val in CHECKBOX_DEFAULTS.items():
            st.session_state[key] = val
        st.rerun()

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Courbes affichées**")

    show = {
        "Total Visitors":     st.sidebar.checkbox("Total Visitors",        key="show_Total Visitors"),
        "New Visitors":       st.sidebar.checkbox("New Visitors",          key="show_New Visitors"),
        "Returning Visitors": st.sidebar.checkbox("Returning Visitors",    key="show_Returning Visitors"),
        "Daily Visitors":     st.sidebar.checkbox("Daily Visitors",        key="show_Daily Visitors"),
        "Session (min)":      st.sidebar.checkbox("Session moyenne (min)", key="show_Session (min)"),
    }

    st.sidebar.markdown("---")
    st.sidebar.markdown("**Événements**")
    show_bbc = st.sidebar.checkbox("🇬🇧 Matchs BBC", key="show_bbc")
    show_fwc = st.sidebar.checkbox("🏆 Matchs FWC", key="show_fwc")

    st.sidebar.markdown("---")
    min_d = df["DateTime"].min().date()
    max_d = df["DateTime"].max().date()

    date_range = st.sidebar.date_input(
        "Plage de dates",
        value=(min_d, max_d),
        min_value=min_d,
        max_value=max_d,
    )
    start, end = (date_range if len(date_range) == 2 else (min_d, max_d))

    filtered = df[
        (df["DateTime"] >= pd.Timestamp(start)) &
        (df["DateTime"] <  pd.Timestamp(end) + timedelta(days=1))
    ]
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**{len(filtered):,}** lignes")
    return filtered, show, show_bbc, show_fwc


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    st.title("⚽ FWC 26 — Traffic Analytics Dashboard")
    st.caption(
        "Données Google Sheets · "
        "Rafraîchissement auto toutes les **5 min** · "
        "Zoom : dessinez un rectangle sur le graphique"
    )

    df                                   = load_data()
    filtered, show, show_bbc, show_fwc   = sidebar_controls(df)

    if filtered.empty:
        st.warning("Aucune donnée pour la plage sélectionnée.")
        return

    c1, c2, c3, c4 = st.columns(4)
    # Daily users est répété 24× par jour après ffill — on prend la valeur unique par jour
    if "Daily users" in filtered.columns:
        tmp = filtered.dropna(subset=["Daily users"]).copy()
        tmp["_day"] = tmp["DateTime"].dt.date
        daily_total = tmp.drop_duplicates(subset="_day")["Daily users"].sum()
    else:
        daily_total = filtered["Total Visitors"].sum()
    c1.metric("Visiteurs totaux", f"{int(daily_total):,}")
    c2.metric("Session moy. (min)", f"{filtered['Session (min)'].mean():.1f}")
    def _count_matches(col):
        if col not in filtered.columns:
            return 0
        return (
            filtered[col][filtered[col] != ""]
            .str.split(" / ")
            .explode()
            .str.strip()
            .pipe(lambda s: s[s != ""])
            .count()
        )

    n_bbc = _count_matches("BBC Match")
    n_fwc = _count_matches("FWC Match")
    c3.metric("Matchs BBC", int(n_bbc))
    c4.metric("Matchs FWC", int(n_fwc))

    fig = build_figure(filtered, show, show_bbc, show_fwc)
    st.plotly_chart(fig, use_container_width=True, key="main_chart")

    with st.expander("📄 Données brutes"):
        cols = ["DateTime", "Total Visitors", "New Visitors", "Returning Visitors", "Session (min)", "BBC Match", "FWC Match"]
        cols = [c for c in cols if c in filtered.columns]
        st.dataframe(
            filtered[cols].rename(columns={"DateTime": "Date/Heure"}),
            use_container_width=True,
            hide_index=True,
        )


if __name__ == "__main__":
    main()
