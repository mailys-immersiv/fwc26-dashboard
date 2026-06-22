"""
FWC 26 — Traffic Analytics Dashboard
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

# SHEET_ID lu depuis les secrets Streamlit, sinon depuis la variable d'env
SHEET_ID   = st.secrets.get("SHEET_ID", os.environ.get("SHEET_ID", ""))
SHEET_NAME = "FWC 26 - Chart data"
YEAR       = 2026

if not SHEET_ID:
    st.error("Variable **SHEET_ID** manquante. Ajoutez-la dans les Secrets Streamlit Cloud ou en variable d'environnement.")
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

FULL_DATE_RE = re.compile(r"^\s*(\d{1,2})\s+([A-Za-z]{3})\s+(\d{1,2}:\d{2})\s*$")
TIME_ONLY_RE = re.compile(r"^\s*(\d{1,2}:\d{2})\s*$")


def _forward_fill_dates(series: pd.Series) -> pd.Series:
    """
    Propagate the date part ('12 Jun') onto time-only rows ('01:00', '02:00')
    until the next full-date row appears.
    """
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


def _clean(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    df.columns = [c.strip() for c in df.columns]

    required = ["Date", "Total Visitors", "Average session time"]
    missing  = [c for c in required if c not in df.columns]
    if missing:
        st.error(
            f"Colonnes manquantes : **{missing}**\n\n"
            f"Colonnes trouvées : `{list(df.columns)}`\n\n"
            "Vérifiez que le nom de l'onglet et les en-têtes correspondent exactement."
        )
        st.stop()

    df["_date_str"]     = _forward_fill_dates(df["Date"])
    df["DateTime"]      = df["_date_str"].apply(_parse_datetime)
    df["Total Visitors"] = (
        df["Total Visitors"].astype(str)
        .str.replace(",", "", regex=False).str.strip()
    )
    df["Total Visitors"] = pd.to_numeric(df["Total Visitors"], errors="coerce")
    df["Session (min)"] = df["Average session time"].apply(_duration_to_minutes)

    match_col = next((c for c in ["BBC Matches", "FWC Matches"] if c in df.columns), None)
    if match_col:
        # Remplace les vraies valeurs NaN puis nettoie les strings parasites
        raw_match = df[match_col].where(df[match_col].notna(), "")
        raw_match = raw_match.astype(str).str.strip()
        raw_match = raw_match.replace({"nan": "", "none": "", "None": "", "NaN": ""}, regex=False)
        # Ne garder qu'une seule occurrence par match : effacer les doublons consécutifs
        raw_match = raw_match.where(raw_match != raw_match.shift(), "")
        df["Match"] = raw_match
    else:
        df["Match"] = ""

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

def build_figure(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()

    # Prépare le texte de match pour le hover (ligne par ligne)
    match_labels = df["Match"].apply(
        lambda m: m if (isinstance(m, str) and m.strip() not in ("", "nan", "none", "null")) else "Pas de match"
    )

    fig.add_trace(go.Scatter(
        x=df["DateTime"], y=df["Total Visitors"],
        name="Total Visitors", yaxis="y1",
        mode="lines",
        line=dict(color="#1a6cdb", width=2),
        fill="tozeroy", fillcolor="rgba(26,108,219,0.12)",
        customdata=match_labels,
        hovertemplate=(
            "<b>%{x|%d %b %H:%M}</b><br>"
            "Visiteurs : <b>%{y:,}</b><br>"
            "Match : %{customdata}<extra></extra>"
        ),
    ))

    fig.add_trace(go.Scatter(
        x=df["DateTime"], y=df["Session (min)"],
        name="Session moy. (min)", yaxis="y2",
        mode="lines",
        line=dict(color="#e88a00", width=2, dash="dot"),
        customdata=match_labels,
        hovertemplate=(
            "<b>%{x|%d %b %H:%M}</b><br>"
            "Session : <b>%{y:.1f} min</b><br>"
            "Match : %{customdata}<extra></extra>"
        ),
    ))

    shapes, annotations = [], []
    for _, row in df[df["Match"] != ""].iterrows():
        xv = row["DateTime"]
        shapes.append(dict(
            type="line", x0=xv, x1=xv,
            yref="paper", y0=0, y1=1,
            line=dict(color="rgba(200,30,30,0.6)", width=1.5, dash="dash"),
        ))
        annotations.append(dict(
            x=xv, yref="paper", y=1.01,
            text=f"⚽ {row['Match']}",
            showarrow=False, textangle=-45,
            font=dict(size=8.5, color="#c01e1e"),
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
            title="Total Visitors",
            title_font=dict(color="#1a6cdb"),
            tickfont=dict(color="#1a6cdb"),
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


# ── Sidebar ────────────────────────────────────────────────────────────────────

def sidebar_controls(df: pd.DataFrame) -> pd.DataFrame:
    st.sidebar.title("⚽ FWC 26 · Filtres")

    if st.sidebar.button("🔄 Forcer la synchronisation Google Sheets"):
        load_data.clear()
        st.rerun()

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
    st.sidebar.markdown(
        f"**{len(filtered):,}** lignes · "
        f"**{(filtered['Match'] != '').sum()}** match(s)"
    )
    return filtered


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    st.title("⚽ FWC 26 — Traffic Analytics Dashboard")
    st.caption(
        "Données Google Sheets · "
        "Rafraîchissement auto toutes les **5 min** · "
        "Zoom : dessinez un rectangle sur le graphique"
    )

    df       = load_data()
    filtered = sidebar_controls(df)

    if filtered.empty:
        st.warning("Aucune donnée pour la plage sélectionnée.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Visiteurs totaux",   f"{int(filtered['Total Visitors'].sum()):,}")
    c2.metric("Session moy. (min)", f"{filtered['Session (min)'].mean():.1f}")
    c3.metric("Matchs détectés",    filtered[filtered["Match"] != ""]["Match"].nunique())

    st.plotly_chart(build_figure(filtered), use_container_width=True)

    with st.expander("📄 Données brutes"):
        st.dataframe(
            filtered[["DateTime", "Total Visitors", "Session (min)", "Match"]]
            .rename(columns={"DateTime": "Date/Heure"}),
            use_container_width=True,
            hide_index=True,
        )


if __name__ == "__main__":
    main()
