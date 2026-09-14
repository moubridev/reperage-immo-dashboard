"""
Quartiers Bruxelles — Monitoring des Quartiers (IBSA/perspective.brussels).

Niveau infra-communal pour les 19 communes bruxelloises (145 quartiers).
Ne couvre PAS la Wallonie (Phase 3 du plan, "IWEPS Walstat + secteurs
statistiques" — pas encore fait). Ne couvre pas non plus les prix
immobiliers (cette source n'en publie pas à ce niveau) : uniquement
démographie/socio-économique.

Bug trouvé et corrigé le 14/09 : le champ "commune" publié par la source
(opendata.brussels.be) est faux pour 51/145 quartiers (bug de la source,
vérifié par test point-dans-polygone contre les vraies limites communales)
— voir PLAN_INTELLIGENCE_TERRITORIALE.md pour le détail.
"""

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from lib import get_credentials
import requests

st.set_page_config(page_title="Quartiers Bruxelles — Repérage Immo", page_icon="🏙️", layout="wide")

st.title("🏙️ Quartiers de Bruxelles — Monitoring des Quartiers")
st.caption(
    "145 quartiers, 19 communes bruxelloises. Source : IBSA/perspective.brussels — démographie et "
    "socio-économique uniquement, pas de prix immobilier à ce niveau (source indisponible)."
)

with st.expander("ℹ️ D'où viennent ces chiffres, et une erreur trouvée dans la source"):
    st.markdown("""
- **Population, densité** : année de référence 2025
- **Revenu médian, taux de chômage** : année de référence 2023 (dernière disponible)
- **% propriétaires/locataires** : année de référence 2021
- **% 65 ans et plus** : année de référence 2025

**Erreur trouvée dans la source (corrigée ici) :** le fichier officiel de géométries
(`opendata.brussels.be`) associe la mauvaise commune à 51 des 145 quartiers — par exemple
"Boitsfort Centre" y était étiqueté "Ixelles" au lieu de "Watermael-Boitsfort". Vérifié et
corrigé par un vrai test géométrique (le centroïde de chaque quartier est-il dans le polygone
officiel de quelle commune ?), pas en faisant confiance au champ texte de la source.

**Pas de prix immobilier ici** : contrairement à la page Communes (Wallonie), le Monitoring
des Quartiers ne publie pas de statistiques de prix. Pour un signal prix à Bruxelles, se référer
à la page annonces filtrée par commune.
""")


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_quartiers():
    url, key = get_credentials()
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    r = requests.get(f"{url}/rest/v1/v_dashboard_quartiers_bxl", headers=headers, params={"select": "*", "limit": 200}, timeout=30)
    r.raise_for_status()
    return pd.DataFrame(r.json())


with st.spinner("Chargement des quartiers..."):
    df = fetch_quartiers()

if df.empty:
    st.warning("Aucune donnée reçue.")
    st.stop()

num_cols = ["revenu_median", "taux_chomage", "densite_pop", "pct_proprietaires", "pct_locataires", "pct_65_plus", "population", "superficie_m2", "centroid_lat", "centroid_lng"]
for c in num_cols:
    df[c] = pd.to_numeric(df[c], errors="coerce")

COMMUNE_NAMES = {
    "21001": "Anderlecht", "21002": "Auderghem", "21003": "Berchem-Sainte-Agathe",
    "21004": "Bruxelles", "21005": "Etterbeek", "21006": "Evere", "21007": "Forest",
    "21008": "Ganshoren", "21009": "Ixelles", "21010": "Jette", "21011": "Koekelberg",
    "21012": "Molenbeek-Saint-Jean", "21013": "Saint-Gilles", "21014": "Saint-Josse-ten-Noode",
    "21015": "Schaerbeek", "21016": "Uccle", "21017": "Watermael-Boitsfort",
    "21018": "Woluwe-Saint-Lambert", "21019": "Woluwe-Saint-Pierre",
}
df["commune"] = df["code_ins"].map(COMMUNE_NAMES)

st.markdown("---")

c1, c2 = st.columns([1, 1])

with c1:
    communes = ["Toutes"] + sorted(df["commune"].dropna().unique().tolist())
    commune_filter = st.selectbox("Filtrer par commune", communes)
    scope = df if commune_filter == "Toutes" else df[df["commune"] == commune_filter]
    quartier_options = sorted(scope["nom_quartier"].dropna().tolist())
    quartier_sel = st.selectbox("Quartier", quartier_options) if quartier_options else None

    if quartier_sel:
        row = df[df["nom_quartier"] == quartier_sel].iloc[0]
        st.subheader(f"📋 {row['nom_quartier'].title()} — {row['commune']}")
        k1, k2 = st.columns(2)
        k1.metric("Population", f"{row['population']:,.0f}".replace(",", " ") if pd.notna(row["population"]) else "n.c.")
        k2.metric("Densité", f"{row['densite_pop']:,.0f} hab/km²".replace(",", " ") if pd.notna(row["densite_pop"]) else "n.c.")
        k3, k4 = st.columns(2)
        k3.metric("Revenu médian", f"{row['revenu_median']:,.0f} €".replace(",", " ") if pd.notna(row["revenu_median"]) else "n.c. (< seuil de secret statistique)")
        k4.metric("Taux de chômage", f"{row['taux_chomage']:.1f} %" if pd.notna(row["taux_chomage"]) else "n.c.")
        k5, k6 = st.columns(2)
        k5.metric("% propriétaires", f"{row['pct_proprietaires']:.0f} %" if pd.notna(row["pct_proprietaires"]) else "n.c.")
        k6.metric("% 65 ans et plus", f"{row['pct_65_plus']:.0f} %" if pd.notna(row["pct_65_plus"]) else "n.c.")

with c2:
    st.subheader("🗺️ Carte — revenu médian par quartier")
    map_df = df.dropna(subset=["centroid_lat", "centroid_lng"])
    m = folium.Map(location=[50.85, 4.36], zoom_start=12, tiles="OpenStreetMap")
    valid_rev = map_df["revenu_median"].dropna()
    vmin, vmax = (valid_rev.min(), valid_rev.max()) if not valid_rev.empty else (0, 1)
    for _, r in map_df.iterrows():
        if pd.notna(r["revenu_median"]):
            frac = (r["revenu_median"] - vmin) / (vmax - vmin) if vmax > vmin else 0.5
            color = f"#{int(255*(1-frac)):02x}{int(180*frac):02x}40"
        else:
            color = "#999999"
        is_selected = quartier_sel and r["nom_quartier"] == quartier_sel
        folium.CircleMarker(
            location=[r["centroid_lat"], r["centroid_lng"]],
            radius=8 if is_selected else 4,
            color="#1D2420" if is_selected else color,
            weight=2 if is_selected else 1,
            fill=True, fill_color=color, fill_opacity=0.8,
            popup=folium.Popup(f"<b>{r['nom_quartier'].title()}</b> ({r['commune']})<br>Revenu médian : {r['revenu_median']:.0f} €" if pd.notna(r["revenu_median"]) else f"<b>{r['nom_quartier'].title()}</b> — n.c.", max_width=220),
        ).add_to(m)
    st_folium(m, use_container_width=True, height=480, returned_objects=[])
    st.caption("Vert = revenu médian élevé, rouge = faible, gris = non disponible (secret statistique). Position au centroïde du quartier.")

st.markdown("---")
st.subheader("Classement complet")
table = df.sort_values("revenu_median", ascending=False, na_position="last")[[
    "nom_quartier", "commune", "population", "revenu_median", "taux_chomage", "pct_proprietaires", "densite_pop"
]].rename(columns={
    "nom_quartier": "Quartier", "commune": "Commune", "population": "Population",
    "revenu_median": "Revenu médian (€)", "taux_chomage": "Chômage (%)",
    "pct_proprietaires": "Propriétaires (%)", "densite_pop": "Densité (hab/km²)",
})
st.dataframe(
    table, use_container_width=True, height=420, hide_index=True,
    column_config={
        "Population": st.column_config.NumberColumn(format="%d"),
        "Revenu médian (€)": st.column_config.NumberColumn(format="%d €"),
        "Chômage (%)": st.column_config.NumberColumn(format="%.1f %%"),
        "Propriétaires (%)": st.column_config.NumberColumn(format="%.0f %%"),
        "Densité (hab/km²)": st.column_config.NumberColumn(format="%d"),
    },
)
