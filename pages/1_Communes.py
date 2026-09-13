"""
Repérage Immo — Communes : classement comparatif, radar multi-critères,
tendance passée, comparateur de pairs.

Phase 1 du plan d'intelligence territoriale (voir vault Moubri,
PLAN_INTELLIGENCE_TERRITORIALE.md). Toutes les données viennent de
v_dashboard_communes / v_dashboard_communes_historique (vues Supabase
dédiées, clé anon — jamais les tables de base ni service_role).

Scoring reconstruit le 2026-09-13 : l'ancien score (communes_score_investissement)
n'avait aucune formule documentée retrouvable. Remplacé par 4 piliers dont la
formule est écrite ci-dessous et vérifiable dans v_dashboard_communes (vue SQL).
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
KOHESIO_VIEW = "v_dashboard_kohesio"

SCORE_COLS = ["score_marche", "score_prix", "score_demo", "score_mobilite"]
SCORE_LABELS = ["Marché", "Prix/Accessibilité", "Démographie", "Mobilité"]


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
    kohesio, _ = fetch_view(KOHESIO_VIEW)

if df.empty:
    st.warning("Aucune donnée reçue.")
    st.stop()

if not kohesio.empty:
    kohesio["nb_projets_ue"] = pd.to_numeric(kohesio["nb_projets_ue"], errors="coerce")
    kohesio["budget_ue_total_eur"] = pd.to_numeric(kohesio["budget_ue_total_eur"], errors="coerce")
    df = df.merge(kohesio[["code_ins", "nb_projets_ue", "budget_ue_total_eur"]], on="code_ins", how="left")
    df["nb_projets_ue"] = df["nb_projets_ue"].fillna(0)
    df["budget_ue_total_eur"] = df["budget_ue_total_eur"].fillna(0)
else:
    df["nb_projets_ue"] = 0
    df["budget_ue_total_eur"] = 0

num_cols = [
    "population_totale", "superficie_km2", "densite_hab_km2", "revenu_median_net",
    "taux_chomage_pct", "pct_proprietaires", "age_median", "pct_diplome_superieur",
    "score_total", *SCORE_COLS, "capacite_emprunt_20ans", "revenu_median_menage",
    "prix_median_maison", "nb_transactions_recent", "ratio_capacite_prix_pct",
    "anciennete_active_jours", "n_annonces_actives",
    "distance_gare_km", "centroid_lat", "centroid_lng",
    "pct_parc_degrade", "rendement_locatif_brut_pct", "permis_neuf_logements_12m",
    "prime_renovation_eur_m2", "prime_renovation_pct", "n_bon_peb", "n_degrade",
    "distance_zone_houillere_km",
    "nb_entreprises_bep", "nb_entreprises_idelux", "nb_entreprises_idea",
    "nb_emploi_idea", "superficie_disponible_idea_m2",
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
    f"ancienneté active ({df['anciennete_active_jours'].notna().sum()}/{len(df)}). "
    "Les cases vides ('n.c.') ne sont pas des zéros."
)

with st.expander("ℹ️ Comment chaque chiffre est calculé — pas de boîte noire", expanded=False):
    st.markdown("""
**Scoring reconstruit le 13/09/2026.** L'ancien score (`communes_score_investissement`, 6 sous-scores)
a été **abandonné** : aucune formule documentée n'a été retrouvée nulle part (ni dans le vault,
ni dans la base — c'est une table figée, pas une vue qu'on peut inspecter). Plutôt que de garder
une boîte noire, il a été remplacé par 4 piliers dont la formule exacte suit — vérifiable dans
la vue SQL `v_dashboard_communes`.

| Pilier (0-20 pts) | Formule exacte |
|---|---|
| **Marché** | 60% × ancienneté active (20 pts si annonces neuves, 0 pt si ≥180 j. en moyenne) + 40% × volume de transactions/an (20 pts si ≥200/an) |
| **Prix / Accessibilité** | 20 × min(capacité d'emprunt ÷ prix médian maison, 100%) ÷ 100 |
| **Démographie** | 10 × (rang percentile du revenu médian) + 10 × (rang percentile inverse du chômage) — position relative parmi les 339 communes, pas une valeur absolue |
| **Mobilité** | 20 pts si gare à 0 km, 0 pt si ≥15 km (linéaire, à vol d'oiseau) |
| **Score global** | (somme des 4 piliers) × 1,25 → ramené sur 100. Une donnée manquante est neutralisée à 10/20 (médiane), jamais à 0. |

**Ancienneté active (jours)** — ⚠️ ce n'est **pas** un temps de vente :
> Moyenne (aujourd'hui − date de première détection) sur les annonces **actives** de la commune. C'est le temps déjà passé sur le marché pour le stock non vendu — pas la durée réelle avant vente.
> **Pourquoi pas un vrai "temps pour vendre" :** vérifié en base — le champ censé capter la sortie du marché (`date_disparition`) est vide à 100%, et la seule alternative disponible donne une durée médiane de **0 jour** sur les biens vendus, ce qui est incohérent. Cette donnée n'est tout simplement pas mesurable fiablement avec le pipeline de collecte actuel.
> Couverture : jointure sur le nom de commune normalisé (pas `code_ins`, vide sur 98% de la table) → 98% des communes calculables, contre 2% avec l'ancienne méthode. Communes avec moins de 5 annonces actives : non calculé (`n.c.`).

**Chiffres 100% expliqués (pas de scoring, données ou calculs directs) :**

| Indicateur | Formule / source |
|---|---|
| Capacité d'achat / prix | `capacite_emprunt_20ans` (Statbel + hypothèses bancaires) ÷ prix médian maison le plus récent × 100 |
| Densité (hab/km²) | Population totale (Statbel 2021) ÷ superficie de la commune |
| Groupe de pairs | Croisement population (5 bandes) × densité (3 bandes) |
| Rangs (pairs/province/région) | Classement du Score global dans chaque périmètre |
| Tendance passée | Prix médian trimestriel Statbel, 2020-2025 — donnée brute |
| Revenu, chômage, % propriétaires | Statbel, tels quels |
| Parc dégradé (E-F-G) | Somme des % du parc en PEB E+F+G, `communes_peb_stock`, dernière année dispo (tous types de logements confondus) |
| Rendement locatif brut | Moyenne maison+appartement, `communes_rendement_locatif`, dernière année |
| Permis neuf | Logements neufs autorisés, `communes_permis_batir`, dernière année — le volet rénovation existe chez Statbel (vérifié réel) mais l'import actuel ne le lit pas ; correctif écrit, pas encore déployé |
| Prime de rénovation | Écart médian de prix/m² entre annonces PEB A-B et PEB E-F-G, même commune (calculé depuis les annonces déjà en base, ≥5 annonces de chaque côté requis) |
| Projets UE (Kohesio) | 3 888 projets européens belges (2014-2020, FEDER/FSE) téléchargés depuis `cohesiondata.ec.europa.eu` (dataset officiel), rattachés à la commune la plus proche par distance au centroïde (seuil 15 km, pour exclure les projets flamands). **Approximatif** : pas un géocodage à l'adresse, une commune rurale étendue peut absorber un projet en réalité situé dans une commune voisine plus petite. 2 364/3 888 projets rattachés (les autres : hors zone ou >15 km, essentiellement flamands). Aucune donnée 2021-2027 disponible au niveau projet à ce jour. |
| Risque minier | Distance du centroïde communal à la concession minière de houille ou zone déhouillée (Bassin de Mons) la plus proche — couche officielle SPW (`geoservices.wallonie.be`, service WFS INSPIRE), 157 polygones houille/déhouillées filtrés sur 360 concessions minières wallonnes (métal, fer, or, houille...). **Approximatif** : distance au centroïde communal, pas une vérification à la parcelle — une commune peut avoir 0 km affiché alors qu'une adresse précise est loin de la zone réelle, ou l'inverse. 40/283 communes ont leur centroïde dans une zone. Non stocké en géométrie brute dans Supabase (cohérent avec l'architecture 3 niveaux du plan) — seule la distance calculée est conservée. |
| Parcs d'activité économique | Comptage d'entreprises sur les parcs économiques : IDEA (Hainaut/Mons-Borinage, avec emplois et surface disponible par parc — source `odwb.be`), BEP (Namur), IDELUX (Luxembourg). SPI (Liège) : pas de source en open data trouvée. **Snapshot statique** — aucune des 3 sources ne publie de date d'implantation, donc pas un signal "investissements récents" à proprement parler, juste "présence économique actuelle". Matching commune fait par nom de localité (accents normalisés + table de correspondance village→commune post-fusion 1977) — 94-100% de taux de correspondance selon la source, le résidu (localités rares/mal orthographiées) n'est pas comptabilisé plutôt que mal attribué. |

**Table écartée après vérification :** `communes_fiscalite_immo` — les taux d'enregistrement et le précompte sont vides à 100%, et le "coefficient additionnel communal" affiché comme rempli est en réalité **une valeur constante (2600) sur les 338 communes** — une donnée placeholder, pas une vraie donnée fiscale locale. Non utilisée.
""")

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
    k1.metric("Score global", f"{row['score_total']:.0f}" if pd.notna(row["score_total"]) else "n.c.",
              help="(Marché + Prix + Démographie + Mobilité) × 1,25, ramené sur 100. Formule complète dans l'encadré ci-dessus.")
    k2.metric("Capacité d'achat / prix", f"{row['ratio_capacite_prix_pct']:.0f} %" if pd.notna(row["ratio_capacite_prix_pct"]) else "n.c.",
              help="Capacité d'emprunt du ménage médian (20 ans) ÷ prix médian maison le plus récent × 100. Bas = les locaux ne peuvent plus suivre le prix du marché.")
    k3.metric(
        "Ancienneté active",
        f"{row['anciennete_active_jours']:.0f} j." if pd.notna(row["anciennete_active_jours"]) else "n.c. (< 5 annonces exploitables)",
        help="Temps moyen déjà passé sur le marché pour les annonces actives (pas un temps de vente — voir encadré ci-dessus pour pourquoi). "
             + (f"Basé sur {int(row['n_annonces_actives'])} annonces." if pd.notna(row.get("n_annonces_actives")) else ""),
    )

    r1, r2, r3 = st.columns(3)
    r1.metric("Rang / groupe de pairs", f"{int(row['rang_pairs'])}/{int(row['n_pairs'])}" if pd.notna(row["rang_pairs"]) else "n.c.",
              help="Rang du Score global parmi les communes du même groupe de pairs (population × densité).")
    r2.metric("Rang / province", f"{int(row['rang_province'])}/{int(row['n_province'])}" if pd.notna(row["rang_province"]) else "n.c.",
              help="Rang du Score global parmi toutes les communes de la même province.")
    r3.metric("Rang / Wallonie+BXL", f"{int(row['rang_general'])}/{int(row['n_general'])}" if pd.notna(row["rang_general"]) else "n.c.",
              help="Rang du Score global sur l'ensemble des 339 communes couvertes.")

    st.markdown("**Démographie** (Statbel, brut)")
    d1, d2, d3 = st.columns(3)
    d1.metric("Revenu médian net", f"{row['revenu_median_net']:,.0f} €".replace(",", " ") if pd.notna(row["revenu_median_net"]) else "n.c.")
    d2.metric("Taux de chômage", f"{row['taux_chomage_pct']:.1f} %" if pd.notna(row["taux_chomage_pct"]) else "n.c.")
    d3.metric("% propriétaires", f"{row['pct_proprietaires']:.0f} %" if pd.notna(row["pct_proprietaires"]) else "n.c.")

    st.markdown("**Marché élargi** (hors score — indicatif)")
    e1, e2, e3, e4 = st.columns(4)
    e1.metric("Parc dégradé (E-F-G)", f"{row['pct_parc_degrade']:.0f} %" if pd.notna(row["pct_parc_degrade"]) else "n.c.",
              help="% du parc de logements total (tous types) en PEB E, F ou G — l'ampleur du marché potentiel pour un achat dégradé → rénovation. Source : communes_peb_stock.")
    e2.metric("Rendement locatif brut", f"{row['rendement_locatif_brut_pct']:.1f} %" if pd.notna(row["rendement_locatif_brut_pct"]) else "n.c.",
              help="Moyenne maison+appartement, loyer annuel ÷ prix d'achat. Source : communes_rendement_locatif.")
    e3.metric("Permis neuf (dernière année)", f"{row['permis_neuf_logements_12m']:.0f} logements" if pd.notna(row["permis_neuf_logements_12m"]) else "n.c.",
              help="Logements neufs autorisés — future concurrence à la revente si résidentiel. Le volet rénovation existe chez Statbel mais l'import ne le lit pas encore (correctif prêt, pas déployé) — non affiché ici pour l'instant.")
    e4.metric("Prime de rénovation", f"+{row['prime_renovation_pct']:.0f} %" if pd.notna(row["prime_renovation_pct"]) else "n.c. (échantillon insuffisant)",
              help="Écart de prix/m² médian entre annonces PEB A-B et PEB E-F-G, même commune, maison+appartement. "
                   + (f"Basé sur {int(row['n_bon_peb'])} annonces bon PEB et {int(row['n_degrade'])} dégradées." if pd.notna(row.get("n_bon_peb")) else "")
                   + " Indicatif : est-ce que le marché local paie la rénovation ? Ne remplace pas la formule de marge MdB (qui compare déjà au comparable bon PEB, pas au prix dégradé).")

    st.markdown("**🇪🇺 Objectifs européens** (hors score — indicatif, ajouté le 13/09)")
    u1, u2 = st.columns(2)
    u1.metric("Projets UE rattachés (Kohesio 2014-2020)", f"{int(row['nb_projets_ue'])}",
              help="Nombre de projets européens (FEDER/FSE, période 2014-2020) rattachés à cette commune par proximité géographique (distance au centroïde communal, seuil 15 km). Rattachement approximatif — pas un géocodage exact à l'adresse. Source : cohesiondata.ec.europa.eu.")
    u2.metric("Budget UE cumulé", f"{row['budget_ue_total_eur']:,.0f} €".replace(",", " ") if row["budget_ue_total_eur"] else "0 €",
              help="Somme du financement européen (project_eu_budget) des projets rattachés. 0 ne veut pas dire 'aucun investissement UE dans la zone' — signifie qu'aucun projet du dataset 2014-2020 n'a été rattaché à moins de 15 km du centroïde.")

    st.markdown("**⚠️ Risque minier** (hors score — indicatif, ajouté le 13/09)")
    dist_minier = row.get("distance_zone_houillere_km")
    if pd.notna(dist_minier):
        if dist_minier == 0:
            minier_label, minier_delta = "Dans une zone houillère", "⚠️"
        elif dist_minier < 5:
            minier_label, minier_delta = f"{dist_minier:.1f} km d'une zone houillère", "à surveiller"
        else:
            minier_label, minier_delta = f"{dist_minier:.1f} km d'une zone houillère", None
        st.metric("Distance zone houillère/déhouillée (SPW)", minier_label,
                  help="Distance du centroïde communal à la concession minière de houille ou zone déhouillée (Bassin de Mons) SPW la plus proche — 0 = centroïde dans la zone. Approximatif (centroïde, pas parcelle par parcelle). Ancien bassin minier = risque d'affaissement/cavités à vérifier avant achat, pas un empêchement automatique. Source : geoservices.wallonie.be.")
    else:
        st.caption("Pas de donnée de risque minier pour cette commune.")

    nb_parcs = int((row.get("nb_entreprises_bep") or 0) + (row.get("nb_entreprises_idelux") or 0) + (row.get("nb_entreprises_idea") or 0))
    if nb_parcs > 0:
        st.markdown("**🏭 Parcs d'activité économique** (hors score — indicatif, ajouté le 13/09)")
        p1, p2 = st.columns(2)
        p1.metric("Entreprises sur parcs (snapshot)", f"{nb_parcs}",
                  help="Nombre d'entreprises implantées sur les parcs d'activité économique de la commune. Sources combinées : IDEA (Hainaut/Mons-Borinage), BEP (Namur), IDELUX (Luxembourg). SPI (Liège) non trouvé en open data. Snapshot statique — pas de date d'implantation disponible, donc pas un signal de tendance récente.")
        emploi = row.get("nb_emploi_idea")
        p2.metric("Emplois estimés (IDEA)", f"{int(emploi)}" if pd.notna(emploi) and emploi else "n.c.",
                  help="Emplois recensés sur les parcs IDEA de la commune (donnée non disponible pour BEP/IDELUX dans cette source).")

with c2:
    st.subheader("Radar — 4 piliers")
    vals = [row[c] if pd.notna(row[c]) else 10 for c in SCORE_COLS]
    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(r=vals, theta=SCORE_LABELS, fill="toself", name=row["nom_commune"]))
    fig.update_layout(polar=dict(radialaxis=dict(visible=True, range=[0, 20])), showlegend=False, height=340, margin=dict(l=30, r=30, t=20, b=20))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Échelle 0-20 par pilier — formule exacte de chacun dans l'encadré 'Comment chaque chiffre est calculé' en haut de page.")

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
    st.caption("Source : Statbel, prix médian trimestriel par type de bien (2020-2025) — donnée brute, aucun calcul.")
else:
    st.info("Pas d'historique de prix disponible pour cette commune.")

st.markdown("---")

# ---------------------------------------------------------------- comparateur pairs

st.subheader("⚖️ Comparateur — groupe de pairs")
peers = df[df["groupe_pairs"] == row["groupe_pairs"]].sort_values("score_total", ascending=False)
peers_table = peers[[
    "nom_commune", "nom_province", "population_totale", "score_total", "ratio_capacite_prix_pct",
    "anciennete_active_jours", "prix_median_maison", "taux_chomage_pct",
]].rename(columns={
    "nom_commune": "Commune", "nom_province": "Province", "population_totale": "Population",
    "score_total": "Score", "ratio_capacite_prix_pct": "Capacité/prix (%)",
    "anciennete_active_jours": "Ancienneté active (j.)", "prix_median_maison": "Prix médian maison (€)",
    "taux_chomage_pct": "Chômage (%)",
})
st.dataframe(
    peers_table, use_container_width=True, height=min(420, 60 + 35 * len(peers_table)), hide_index=True,
    column_config={
        "Population": st.column_config.NumberColumn(format="%d"),
        "Score": st.column_config.NumberColumn(format="%.0f"),
        "Capacité/prix (%)": st.column_config.NumberColumn(format="%.1f %%"),
        "Ancienneté active (j.)": st.column_config.NumberColumn(format="%.0f"),
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
    "anciennete_active_jours", "taux_chomage_pct", "prix_median_maison",
]].rename(columns={
    "nom_commune": "Commune", "nom_province": "Province", "groupe_pairs": "Groupe de pairs",
    "score_total": "Score", "ratio_capacite_prix_pct": "Capacité/prix (%)",
    "anciennete_active_jours": "Ancienneté active (j.)", "taux_chomage_pct": "Chômage (%)",
    "prix_median_maison": "Prix médian maison (€)",
})
st.dataframe(
    full_table, use_container_width=True, height=420, hide_index=True,
    column_config={
        "Score": st.column_config.NumberColumn(format="%.0f"),
        "Capacité/prix (%)": st.column_config.NumberColumn(format="%.1f %%"),
        "Ancienneté active (j.)": st.column_config.NumberColumn(format="%.0f"),
        "Chômage (%)": st.column_config.NumberColumn(format="%.1f %%"),
        "Prix médian maison (€)": st.column_config.NumberColumn(format="%d €"),
    },
)
