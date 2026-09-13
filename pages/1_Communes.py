"""
Repérage Immo — Communes : classement comparatif, radar multi-critères,
tendance passée, comparateur de pairs.

Phase 1 du plan d'intelligence territoriale (voir vault Moubri,
PLAN_INTELLIGENCE_TERRITORIALE.md). Toutes les données viennent de
v_dashboard_communes / v_dashboard_communes_historique (vues Supabase
dédiées, clé anon — jamais les tables de base ni service_role).
"""

from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
import folium
from streamlit_folium import st_folium

from lib import get_credentials

st.set_page_config(page_title="Communes — Repérage Immo", page_icon="🏘️", layout="wide")

COMMUNES_VIEW = "v_dashboard_communes"
HIST_VIEW = "v_dashboard_communes_historique"

SCORE_COLS = ["score_prix", "score_demo", "score_infra", "score_foncier", "score_risque", "score_marche"]
SCORE_LABELS = ["Prix", "Démographie", "Infrastructure", "Foncier", "Risque", "Marché"]


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_view(view_name, select="*"):
    url, key = get_credentials()
    if not url or not key:
        st.error("Identifiants Supabase introuvables (st.secrets ou moubri/.env)")
        st.stop()
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows, offset, page_size = [], 0, 1000
    while True:
        params = {"select": select, "limit": page_size, "offset": offset}
        r = requests.get(f"{url}/rest/v1/{view_name}", headers=headers, params=params, timeout=60)
        r.raise_for_status()
        page = r.json()
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return pd.DataFrame(rows), datetime.now()


def population_band(pop):
    if pd.isna(pop):
        return "Inconnu"
    if pop >= 80_000:
        return "Métropole (80k+)"
    if pop >= 30_000:
        return "Grande ville (30-80k)"
    if pop >= 10_000:
        return "Ville moyenne (10-30k)"
    if pop >= 5_000:
        return "Petite ville (5-10k)"
    return "Rural (<5k)"


def densite_band(d):
    if pd.isna(d):
        return "Inconnu"
    if d >= 1000:
        return "Dense (1000+/km²)"
    if d >= 300:
        return "Semi-urbain (300-1000/km²)"
    return "Rural (<300/km²)"


with st.spinner("Chargement des données communes..."):
    df, fetched_at = fetch_view(COMMUNES_VIEW)
    hist, _ = fetch_view(HIST_VIEW)

if df.empty:
    st.warning("Aucune donnée reçue.")
    st.stop()

num_cols = [
    "population_totale", "superficie_km2", "densite_hab_km2", "revenu_median_net",
    "taux_chomage_pct", "pct_proprietaires", "age_median", "pct_diplome_superieur",
    "score_total", *SCORE_COLS, "capacite_emprunt_20ans", "revenu_median_menage",
    "prix_median_maison", "nb_transactions_recent", "ratio_capacite_prix_pct",
    "densite_logha_existante", "score_potentiel_developpement", "vitesse_jours",
    "distance_gare_km", "centroid_lat", "centroid_lng",
]
for c in num_cols:
    if c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")

df["groupe_population"] = df["population_totale"].apply(population_band)
df["groupe_densite"] = df["densite_hab_km2"].apply(densite_band)
df["groupe_pairs"] = df["groupe_population"] + " · " + df["groupe_densite"]

# Rangs de comparabilité — toujours à 3 niveaux
df["rang_pairs"] = df.groupby("groupe_pairs")["score_total"].rank(ascending=False, method="min")
df["n_pairs"] = df.groupby("groupe_pairs")["score_total"].transform("count")
df["rang_province"] = df.groupby("nom_province")["score_total"].rank(ascending=False, method="min")
df["n_province"] = df.groupby("nom_province")["score_total"].transform("count")
df["rang_general"] = df["score_total"].rank(ascending=False, method="min")
df["n_general"] = df["score_total"].notna().sum()

st.title("🏘️ Communes — classement comparatif")
st.caption(f"{len(df)} communes (Wallonie + Bruxelles) · chargées {fetched_at.strftime('%H:%M')}")
st.caption(
    "⚠️ Couverture partielle sur certains champs : ratio capacité/prix "
    f"({df['ratio_capacite_prix_pct'].notna().sum()}/{len(df)}), "
    f"densité ({df['densite_hab_km2'].notna().sum()}/{len(df)}), "
    f"vitesse de vente ({df['vitesse_jours'].notna().sum()}/{len(df)}). "
    "Les cases vides ('n.c.') ne sont pas des zéros."
)

# ---------------------------------------------------------------- sélection

communes_options = df.sort_values("nom_commune")["nom_commune"].tolist()
default_idx = communes_options.index("Mons") if "Mons" in communes_options else 0
commune_sel = st.selectbox("Commune à analyser", communes_options, index=default_idx)
row = df[df["nom_commune"] == commune_sel].iloc[0]

st.markdown("---")

# ---------------------------------------------------------------- fiche commune

c1, c2 = st.columns([1, 1])

with c1:
    st.subheader(f"📋 {row['nom_commune']} — {row['nom_province']}")
    st.caption(f"Groupe de pairs : **{row['groupe_pairs']}** ({int(row['n_pairs'])} communes comparables)")

    k1, k2, k3 = st.columns(3)
    k1.metric("Score global", f"{row['score_total']:.0f}" if pd.notna(row["score_total"]) else "n.c.")
    k2.metric("Capacité d'achat / prix", f"{row['ratio_capacite_prix_pct']:.0f} %" if pd.notna(row["ratio_capacite_prix_pct"]) else "n.c.",
              help="Capacité d'emprunt du ménage médian ÷ prix médian maison. Bas = les locaux ne peuvent plus suivre le prix du marché.")
    k3.metric("Vitesse de vente", f"{row['vitesse_jours']:.0f} j." if pd.notna(row["vitesse_jours"]) else "n.c.")

    r1, r2, r3 = st.columns(3)
    r1.metric("Rang / groupe de pairs", f"{int(row['rang_pairs'])}/{int(row['n_pairs'])}" if pd.notna(row["rang_pairs"]) else "n.c.")
    r2.metric("Rang / province", f"{int(row['rang_province'])}/{int(row['n_province'])}" if pd.notna(row["rang_province"]) else "n.c.")
    r3.metric("Rang / Wallonie+BXL", f"{int(row['rang_general'])}/{int(row['n_general'])}" if pd.notna(row["rang_general"]) else "n.c.")

    st.markdown("**Démographie**")
    d1, d2, d3 = st.columns(3)
    d1.metric("Revenu médian net", f"{row['revenu_median_net']:,.0f} €".replace(",", " ") if pd.notna(row["revenu_median_net"]) else "n.c.")
    d2.metric("Taux de chômage", f"{row['taux_chomage_pct']:.1f} %" if pd.notna(row["taux_chomage_pct"]) else "n.c.")
    d3.metric("% propriétaires", f"{row['pct_proprietaires']:.0f} %" if pd.notna(row["pct_proprietaires"]) else "n.c.")

with c2:
    st.subheader("Radar multi-critères")
    vals = [row[c] if pd.notna(row[c]) else 0 for c in SCORE_COLS]
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(r=vals, theta=SCORE_LABELS, fill="toself", name=row["nom_commune"]))
    fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 20])), showlegend=False, height=340, margin=dict(l=30, r=30, t=20, b=20))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Échelle 0-20 par sous-score (source : `communes_score_investissement`).")

st.markdown("---")

# ---------------------------------------------------------------- tendance passée

st.subheader("📈 Tendance du marché — passé")
h = hist[hist["code_ins"] == row["code_ins"]].copy() if not hist.empty else pd.DataFrame()
if not h.empty:
    h["prix_median"] = pd.to_numeric(h["prix_median"], errors="coerce")
    h["periode"] = h["annee"].astype(str) + "-T" + h["trimestre"].astype(str)
    h = h.sort_values(["annee", "trimestre"])
    fig = px.line(h, x="periode", y="prix_median", color="type_bien", markers=True)
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="", yaxis_title="Prix médian (€)")
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Source : Statbel, prix médian trimestriel par type de bien (2020-2025).")
else:
    st.info("Pas d'historique de prix disponible pour cette commune.")

st.markdown("---")

# ---------------------------------------------------------------- comparateur pairs

st.subheader("⚖️ Comparateur — groupe de pairs")
peers = df[df["groupe_pairs"] == row["groupe_pairs"]].sort_values("score_total", ascending=False)
peers_table = peers[[
    "nom_commune", "nom_province", "population_totale", "score_total", "ratio_capacite_prix_pct",
    "vitesse_jours", "prix_median_maison", "taux_chomage_pct",
]].rename(columns={
    "nom_commune": "Commune", "nom_province": "Province", "population_totale": "Population",
    "score_total": "Score", "ratio_capacite_prix_pct": "Capacité/prix (%)",
    "vitesse_jours": "Vitesse (j.)", "prix_median_maison": "Prix médian maison (€)",
    "taux_chomage_pct": "Chômage (%)",
})
st.dataframe(
    peers_table, use_container_width=True, height=min(420, 60 + 35 * len(peers_table)), hide_index=True,
    column_config={
        "Population": st.column_config.NumberColumn(format="%d"),
        "Score": st.column_config.NumberColumn(format="%.0f"),
        "Capacité/prix (%)": st.column_config.NumberColumn(format="%.1f %%"),
        "Vitesse (j.)": st.column_config.NumberColumn(format="%.0f"),
        "Chômage (%)": st.column_config.NumberColumn(format="%.1f %%"),
        "Prix médian maison (€)": st.column_config.NumberColumn(format="%d €"),
    },
)
st.caption(f"Comparé à {row['nom_commune']}, pas à un village rural ou une métropole sans rapport — même profil taille/densité.")

st.markdown("---")

# ---------------------------------------------------------------- carte

st.subheader("🗺️ Carte des scores")
map_df = df.dropna(subset=["centroid_lat", "centroid_lng", "score_total"])
if not map_df.empty:
    m = folium.Map(location=[50.5, 4.5], zoom_start=8, tiles="OpenStreetMap")
    vmin, vmax = map_df["score_total"].min(), map_df["score_total"].max()
    for _, r in map_df.iterrows():
        frac = (r["score_total"] - vmin) / (vmax - vmin) if vmax > vmin else 0.5
        color = f"#{int(255*(1-frac)):02x}{int(180*frac):02x}40"
        is_selected = r["nom_commune"] == commune_sel
        folium.CircleMarker(
            location=[r["centroid_lat"], r["centroid_lng"]],
            radius=9 if is_selected else 5,
            color="#1D2420" if is_selected else color,
            weight=2 if is_selected else 1,
            fill=True, fill_color=color, fill_opacity=0.8,
            popup=folium.Popup(f"<b>{r['nom_commune']}</b><br>Score : {r['score_total']:.0f}<br>Capacité/prix : {r['ratio_capacite_prix_pct']:.0f}%" if pd.notna(r["ratio_capacite_prix_pct"]) else f"<b>{r['nom_commune']}</b><br>Score : {r['score_total']:.0f}", max_width=200),
        ).add_to(m)
    st_folium(m, use_container_width=True, height=480, returned_objects=[])
    st.caption("Vert = score élevé, rouge = score faible. Cercle noir = commune sélectionnée. Position au centroïde communal (pas de polygones détaillés à ce stade — voir Phase 3 du plan).")
else:
    st.info("Pas assez de données géolocalisées.")

st.markdown("---")

# ---------------------------------------------------------------- classement complet

st.subheader("Classement complet")
provinces = ["Toutes"] + sorted(df["nom_province"].dropna().unique().tolist())
prov_filter = st.selectbox("Filtrer par province", provinces)
full = df if prov_filter == "Toutes" else df[df["nom_province"] == prov_filter]
full = full.sort_values("score_total", ascending=False)
full_table = full[[
    "nom_commune", "nom_province", "groupe_pairs", "score_total", "ratio_capacite_prix_pct",
    "vitesse_jours", "taux_chomage_pct", "prix_median_maison",
]].rename(columns={
    "nom_commune": "Commune", "nom_province": "Province", "groupe_pairs": "Groupe de pairs",
    "score_total": "Score", "ratio_capacite_prix_pct": "Capacité/prix (%)",
    "vitesse_jours": "Vitesse (j.)", "taux_chomage_pct": "Chômage (%)", "prix_median_maison": "Prix médian maison (€)",
})
st.dataframe(
    full_table, use_container_width=True, height=420, hide_index=True,
    column_config={
        "Score": st.column_config.NumberColumn(format="%.0f"),
        "Capacité/prix (%)": st.column_config.NumberColumn(format="%.1f %%"),
        "Vitesse (j.)": st.column_config.NumberColumn(format="%.0f"),
        "Chômage (%)": st.column_config.NumberColumn(format="%.1f %%"),
        "Prix médian maison (€)": st.column_config.NumberColumn(format="%d €"),
    },
)
