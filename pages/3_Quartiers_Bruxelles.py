"""
Quartiers Bruxelles — Monitoring des Quartiers (IBSA/perspective.brussels).

Niveau infra-communal pour les 19 communes bruxelloises (145 quartiers).
Ne couvre PAS la Wallonie (secteurs statistiques Statbel — table
`ref_secteurs_statistiques`, pas encore branchée au dashboard, couverture
prix trop faible à ce niveau pour une page dédiée).

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
    "145 quartiers, 19 communes bruxelloises. Source : IBSA/perspective.brussels — démographie, "
    "logement, emploi et prix des appartements (là où le nombre de ventes le permet)."
)

with st.expander("ℹ️ D'où viennent ces chiffres, et une erreur trouvée dans la source"):
    st.markdown("""
| Indicateur | Année de référence |
|---|---|
| Population, densité, % 0-17/18-29/18-64/65+ | 2025 |
| Revenu médian, taux de chômage, chômage jeunes | 2023 |
| % propriétaires/locataires, % maisons/appartements | 2021 |
| % logements sociaux | 2024 |
| % bénéficiaires CPAS | 2022 |
| Prix médian et nombre de ventes d'appartements | 2023 |

**Erreur trouvée dans la source (corrigée ici) :** le fichier officiel de géométries
(`opendata.brussels.be`) associe la mauvaise commune à 51 des 145 quartiers — par exemple
"Boitsfort Centre" y était étiqueté "Ixelles" au lieu de "Watermael-Boitsfort". Vérifié et
corrigé par un vrai test géométrique (le centroïde de chaque quartier est-il dans le polygone
officiel de quelle commune ?), pas en faisant confiance au champ texte de la source.

**Prix immobilier — appartements uniquement** : le Monitoring des Quartiers ne publie pas de
prix maison à ce niveau (trop peu de ventes pour un secret statistique fiable), et le prix
appartement lui-même est masqué (`n.c.`) en dessous de 32 ventes/an dans le quartier — 96/145
quartiers passent ce seuil. Pour un signal maison à Bruxelles, se référer à la page annonces.
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

num_cols = ["revenu_median", "taux_chomage", "taux_chomage_jeunes", "densite_pop", "pct_proprietaires",
            "pct_locataires", "pct_65_plus", "pct_0_17", "pct_18_29", "pct_18_64", "pct_maisons",
            "pct_appartements", "pct_cpas", "pct_logements_sociaux", "nb_ventes_appart_2023",
            "prix_median_appart_2023", "population", "superficie_m2", "centroid_lat", "centroid_lng"]
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
        k6.metric("% locataires", f"{row['pct_locataires']:.0f} %" if pd.notna(row["pct_locataires"]) else "n.c.")

        st.markdown("**Âge de la population**")
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("0-17 ans", f"{row['pct_0_17']:.0f} %" if pd.notna(row["pct_0_17"]) else "n.c.")
        a2.metric("18-29 ans", f"{row['pct_18_29']:.0f} %" if pd.notna(row["pct_18_29"]) else "n.c.")
        a3.metric("18-64 ans", f"{row['pct_18_64']:.0f} %" if pd.notna(row["pct_18_64"]) else "n.c.")
        a4.metric("65 ans et +", f"{row['pct_65_plus']:.0f} %" if pd.notna(row["pct_65_plus"]) else "n.c.")

        st.markdown("**Logement et marché**")
        l1, l2 = st.columns(2)
        l1.metric("% maisons / % appartements", f"{row['pct_maisons']:.0f} % / {row['pct_appartements']:.0f} %" if pd.notna(row["pct_maisons"]) else "n.c.")
        l2.metric("% logements sociaux", f"{row['pct_logements_sociaux']:.1f} %" if pd.notna(row["pct_logements_sociaux"]) else "n.c.")
        l3, l4 = st.columns(2)
        l3.metric("Prix médian appartement (2023)", f"{row['prix_median_appart_2023']:,.0f} €".replace(",", " ") if pd.notna(row["prix_median_appart_2023"]) else "n.c. (< 32 ventes/an)",
                  help="Pas de prix maison publié à ce niveau — trop peu de ventes pour un secret statistique fiable.")
        l4.metric("Nombre de ventes (2023)", f"{row['nb_ventes_appart_2023']:.0f}" if pd.notna(row["nb_ventes_appart_2023"]) else "n.c.")

        st.markdown("**Emploi et social**")
        e1, e2 = st.columns(2)
        e1.metric("Chômage jeunes (18-25)", f"{row['taux_chomage_jeunes']:.1f} %" if pd.notna(row["taux_chomage_jeunes"]) else "n.c.")
        e2.metric("% bénéficiaires CPAS", f"{row['pct_cpas']:.1f} %" if pd.notna(row["pct_cpas"]) else "n.c.")

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
    "nom_quartier", "commune", "population", "revenu_median", "prix_median_appart_2023",
    "taux_chomage", "pct_proprietaires", "pct_logements_sociaux", "densite_pop"
]].rename(columns={
    "nom_quartier": "Quartier", "commune": "Commune", "population": "Population",
    "revenu_median": "Revenu médian (€)", "prix_median_appart_2023": "Prix appart. médian (€, 2023)",
    "taux_chomage": "Chômage (%)", "pct_proprietaires": "Propriétaires (%)",
    "pct_logements_sociaux": "Logements sociaux (%)", "densite_pop": "Densité (hab/km²)",
})
st.dataframe(
    table, use_container_width=True, height=420, hide_index=True,
    column_config={
        "Population": st.column_config.NumberColumn(format="%d"),
        "Revenu médian (€)": st.column_config.NumberColumn(format="%d €"),
        "Prix appart. médian (€, 2023)": st.column_config.NumberColumn(format="%d €"),
        "Chômage (%)": st.column_config.NumberColumn(format="%.1f %%"),
        "Propriétaires (%)": st.column_config.NumberColumn(format="%.0f %%"),
        "Logements sociaux (%)": st.column_config.NumberColumn(format="%.1f %%"),
        "Densité (hab/km²)": st.column_config.NumberColumn(format="%d"),
    },
)
