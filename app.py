"""
Repérage Immo — dashboard de visualisation des annonces Moubri.

Lance avec :
    /home/antoine/venv/bin/streamlit run app.py

Filtres partagés en direct + vue carte réelle (folium) + graphiques (plotly)
sur les annonces actives à vendre/louer en Wallonie et à Bruxelles.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from streamlit_folium import st_folium
import folium
from folium.plugins import MarkerCluster

DASHBOARD_DIR = Path(__file__).parent
ENV_PATH = Path("/home/antoine/moubri/.env")
FILTERS_PATH = DASHBOARD_DIR / "last_filters.json"

# Vue Supabase dédiée (lecture seule, clé anon/publishable) — jamais la table
# de base `annonces` ni la clé service_role dans ce dashboard.
SOURCE_VIEW = "v_dashboard_immo"
SELECT_FIELDS = (
    "lat,lng,prix,surface_habitable,type_bien,type_transaction,commune,code_postal,"
    "nb_chambres,annee_construction,peb,jours_sur_marche,url_principale"
)

st.set_page_config(page_title="Repérage Immo", page_icon="🗺️", layout="wide")


def load_env():
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get_credentials():
    """Streamlit Cloud: st.secrets (clé anon/publishable, lecture seule).
    Local: fallback sur moubri/.env pour le confort de dev — mais on lit
    SUPABASE_ANON_KEY en priorité, jamais la service_role, pour que le
    comportement local et déployé restent identiques."""
    if "SUPABASE_URL" in st.secrets and "SUPABASE_ANON_KEY" in st.secrets:
        return st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_ANON_KEY"]
    env = load_env()
    url = env.get("SUPABASE_URL")
    key = env.get("SUPABASE_ANON_KEY") or env.get("SUPABASE_KEY")
    return url, key


def region_of(cp):
    try:
        n = int(cp)
    except (TypeError, ValueError):
        return "Autre"
    if 1000 <= n <= 1299:
        return "Bruxelles"
    if 1300 <= n <= 1499:
        return "Brabant wallon"
    if 4000 <= n <= 4999:
        return "Liège"
    if 5000 <= n <= 5999:
        return "Namur"
    if (6000 <= n <= 6599) or (7000 <= n <= 7999):
        return "Hainaut"
    if 6600 <= n <= 6999:
        return "Luxembourg"
    return "Autre"


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_annonces():
    url, key = get_credentials()
    if not url or not key:
        st.error("Identifiants Supabase introuvables (st.secrets ou moubri/.env)")
        st.stop()

    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows = []
    offset = 0
    page_size = 1000
    while True:
        params = {
            "select": SELECT_FIELDS,
            "limit": page_size,
            "offset": offset,
        }
        r = requests.get(f"{url}/rest/v1/{SOURCE_VIEW}", headers=headers, params=params, timeout=60)
        r.raise_for_status()
        page = r.json()
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    df = pd.DataFrame(rows)
    return df, datetime.now()


def build_frame():
    df, fetched_at = fetch_annonces()
    if df.empty:
        return df, fetched_at
    df["prix"] = pd.to_numeric(df["prix"], errors="coerce")
    df["surface_habitable"] = pd.to_numeric(df["surface_habitable"], errors="coerce")
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lng"] = pd.to_numeric(df["lng"], errors="coerce")
    df["nb_chambres"] = pd.to_numeric(df["nb_chambres"], errors="coerce")
    df["annee_construction"] = pd.to_numeric(df["annee_construction"], errors="coerce")
    df["jours_sur_marche"] = pd.to_numeric(df["jours_sur_marche"], errors="coerce")
    df["prix_m2"] = (df["prix"] / df["surface_habitable"]).where(df["surface_habitable"] > 0)
    df["region"] = df["code_postal"].apply(region_of)
    df["type_bien"] = df["type_bien"].fillna("autre")
    df["commune"] = df["commune"].fillna("?")
    df["peb"] = df["peb"].fillna("n.c.")
    return df, fetched_at


def load_last_filters():
    if FILTERS_PATH.exists():
        try:
            return json.loads(FILTERS_PATH.read_text())
        except Exception:
            return {}
    return {}


def save_last_filters(f):
    FILTERS_PATH.write_text(json.dumps(f, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- data

with st.spinner("Chargement des annonces depuis Supabase..."):
    df, fetched_at = build_frame()

if df.empty:
    st.warning("Aucune donnée reçue de Supabase.")
    st.stop()

defaults = load_last_filters()

# ---------------------------------------------------------------- sidebar

st.sidebar.title("🗺️ Repérage Immo")
st.sidebar.caption(f"{len(df):,} annonces actives · chargées {fetched_at.strftime('%H:%M')}".replace(",", " "))

if st.sidebar.button("🔄 Rafraîchir depuis Supabase"):
    fetch_annonces.clear()
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("Transaction")
transaction = st.sidebar.radio("Type", ["vente", "location"], index=0 if defaults.get("transaction", "vente") == "vente" else 1, horizontal=True, label_visibility="collapsed")

st.sidebar.subheader("Zone")
regions_all = ["Bruxelles", "Brabant wallon", "Hainaut", "Namur", "Liège", "Luxembourg", "Autre"]
regions_sel = st.sidebar.multiselect("Région", regions_all, default=defaults.get("regions", []))
commune_query = st.sidebar.text_input("Recherche commune (contient)", value=defaults.get("commune_query", ""))

st.sidebar.subheader("Budget")
prix_max_data = int(df["prix"].quantile(0.99)) if df["prix"].notna().any() else 1_000_000
budget_min, budget_max = st.sidebar.slider(
    "Prix (€)", min_value=0, max_value=max(prix_max_data, 50_000), step=5_000,
    value=tuple(defaults.get("budget", [0, prix_max_data])),
)

st.sidebar.subheader("Surface")
surf_max_data = int(df["surface_habitable"].quantile(0.99)) if df["surface_habitable"].notna().any() else 500
surf_min = st.sidebar.slider("Surface habitable min (m²)", 0, max(surf_max_data, 50), value=defaults.get("surf_min", 0), step=10)

st.sidebar.subheader("Type de bien")
types_all = sorted(df["type_bien"].unique().tolist())
types_sel = st.sidebar.multiselect("Types", types_all, default=defaults.get("types", types_all))

with st.sidebar.expander("Plus de filtres"):
    chambres_min = st.number_input("Chambres minimum", min_value=0, step=1, value=defaults.get("chambres_min", 0))
    annee_min = st.number_input("Construit après", min_value=0, step=1, value=defaults.get("annee_min", 0))
    jours_max = st.number_input("En ligne depuis moins de X jours (0 = pas de limite)", min_value=0, step=5, value=defaults.get("jours_max", 0))

if st.sidebar.button("💾 Sauvegarder ces critères par défaut"):
    save_last_filters({
        "transaction": transaction, "regions": regions_sel, "commune_query": commune_query,
        "budget": [budget_min, budget_max], "surf_min": surf_min, "types": types_sel,
        "chambres_min": chambres_min, "annee_min": annee_min, "jours_max": jours_max,
    })
    st.sidebar.success("Enregistré ✓")

st.sidebar.markdown("---")
st.sidebar.button("↩️ Réinitialiser", on_click=lambda: (FILTERS_PATH.unlink(missing_ok=True), st.rerun()))

# ---------------------------------------------------------------- filtering

f = df[df["type_transaction"] == transaction].copy()
if regions_sel:
    f = f[f["region"].isin(regions_sel)]
if commune_query.strip():
    f = f[f["commune"].str.contains(commune_query.strip(), case=False, na=False)]
f = f[f["prix"].between(budget_min, budget_max) | f["prix"].isna() & (budget_min == 0)]
f = f[(f["surface_habitable"] >= surf_min) | f["surface_habitable"].isna() & (surf_min == 0)]
if types_sel:
    f = f[f["type_bien"].isin(types_sel)]
if chambres_min > 0:
    f = f[f["nb_chambres"] >= chambres_min]
if annee_min > 0:
    f = f[f["annee_construction"] >= annee_min]
if jours_max > 0:
    f = f[f["jours_sur_marche"] <= jours_max]

# ---------------------------------------------------------------- header + KPIs

st.title("Repérage Immo")
st.caption(f"{len(f):,} annonces correspondent, sur {len(df):,} au total".replace(",", " "))

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Résultats", f"{len(f):,}".replace(",", " "))
k2.metric("Prix médian", f"{int(f['prix'].median()):,} €".replace(",", " ") if f["prix"].notna().any() else "—")
k3.metric("Prix/m² médian", f"{int(f['prix_m2'].median()):,} €".replace(",", " ") if f["prix_m2"].notna().any() else "—")
k4.metric("Surface médiane", f"{int(f['surface_habitable'].median())} m²" if f["surface_habitable"].notna().any() else "—")
k5.metric("En ligne depuis (médiane)", f"{int(f['jours_sur_marche'].median())} j." if f["jours_sur_marche"].notna().any() else "—")

st.markdown("---")

# ---------------------------------------------------------------- charts

c1, c2, c3 = st.columns(3)
with c1:
    st.subheader("Distribution des prix")
    if f["prix"].notna().any():
        fig = px.histogram(f, x="prix", nbins=40)
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), showlegend=False)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Pas de données de prix.")

with c2:
    st.subheader("Par commune (top 15)")
    top_com = f["commune"].value_counts().head(15).sort_values()
    if not top_com.empty:
        fig = px.bar(top_com, orientation="h")
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), showlegend=False, yaxis_title="", xaxis_title="")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Aucune commune.")

with c3:
    st.subheader("Prix vs surface")
    scatter_df = f.dropna(subset=["prix", "surface_habitable"])
    if not scatter_df.empty:
        fig = px.scatter(scatter_df, x="surface_habitable", y="prix", color="type_bien", opacity=0.5)
        fig.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Pas assez de données.")

st.markdown("---")

# ---------------------------------------------------------------- map

st.subheader("Carte")
MAP_CAP = 4000
map_df = f.dropna(subset=["lat", "lng"])
if len(map_df) > MAP_CAP:
    st.caption(f"⚠️ {len(map_df):,} résultats — affichage d'un échantillon de {MAP_CAP:,} points sur la carte (affinez les critères pour tout voir).".replace(",", " "))
    map_df = map_df.sample(MAP_CAP, random_state=0)

if not map_df.empty:
    center = [map_df["lat"].mean(), map_df["lng"].mean()]
    m = folium.Map(location=center, zoom_start=8, tiles="OpenStreetMap")
    cluster = MarkerCluster().add_to(m)
    for _, row in map_df.iterrows():
        popup = (
            f"<b>{row['commune']}</b> ({row['code_postal']})<br>"
            f"{row['type_bien']} — {int(row['prix']):,} €<br>".replace(",", " ") if pd.notna(row["prix"]) else f"<b>{row['commune']}</b><br>{row['type_bien']}<br>"
        )
        if pd.notna(row["surface_habitable"]):
            popup += f"{int(row['surface_habitable'])} m²<br>"
        if row.get("url_principale"):
            popup += f"<a href='{row['url_principale']}' target='_blank'>Voir l'annonce ↗</a>"
        folium.CircleMarker(
            location=[row["lat"], row["lng"]], radius=4, color="#2E6B4C", fill=True,
            fill_opacity=0.7, popup=folium.Popup(popup, max_width=250),
        ).add_to(cluster)
    st_folium(m, use_container_width=True, height=520, returned_objects=[])
else:
    st.info("Aucune annonce géolocalisée avec ces critères.")

st.markdown("---")

# ---------------------------------------------------------------- table

st.subheader("Liste des annonces")
table_df = f.sort_values("jours_sur_marche", na_position="last")[
    ["commune", "code_postal", "type_bien", "prix", "surface_habitable", "nb_chambres",
     "annee_construction", "peb", "jours_sur_marche", "url_principale"]
].rename(columns={
    "code_postal": "CP", "type_bien": "Type", "prix": "Prix (€)", "surface_habitable": "Surface (m²)",
    "nb_chambres": "Chambres", "annee_construction": "Année", "peb": "PEB",
    "jours_sur_marche": "Jours en ligne", "url_principale": "Annonce", "commune": "Commune",
})
st.dataframe(
    table_df,
    use_container_width=True,
    height=420,
    column_config={"Annonce": st.column_config.LinkColumn("Annonce", display_text="Voir ↗")},
    hide_index=True,
)
