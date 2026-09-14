"""
Secteurs statistiques — le niveau géographique le plus fin (10 631 secteurs,
Wallonie + Bruxelles), vs 283 communes ou 145 quartiers bruxellois.

Sources réelles, vérifiées (voir AUDIT_SUPABASE_ACTUEL.md / PLAN_INTELLIGENCE_TERRITORIALE.md) :
- Démographie/logement : Recensement 2021 (Statbel, exhaustif)
- Revenu : Statbel fiscal (par déclaration, pas par ménage — voir encadré)
- Prix immobilier secteur : Statbel TF_IMMO_SECTOR — couverture faible (6-4%),
  seuil de confidentialité à 16 transactions/an

Ce que ce niveau NE couvre PAS (repli sur la commune, toujours étiqueté) :
- Typologie fine des ménages (couples/isolés/monoparentaux) — WalStat, commune seulement
- Loyers — commune seulement
- Capacité d'emprunt "officielle" (revenu par ménage) — commune seulement,
  calculée ici pour le secteur à partir du revenu par déclaration (proxy, pas identique)
"""

import folium
import pandas as pd
import requests
import streamlit as st
from streamlit_folium import st_folium

from lib import get_credentials

st.set_page_config(page_title="Secteurs statistiques — Repérage Immo", page_icon="🔬", layout="wide")

# Mensualité mensuelle par euro emprunté sur 20 ans — reverse-engineré et vérifié
# à l'exact (4/4 communes testées) depuis communes_capacite_emprunt : 33% du revenu
# mensuel du ménage, ~3,7%/an implicite. Même convention utilisée ici pour rester
# cohérent avec la page Communes.
ANNUITE_FACTEUR = 0.0058925  # mensualité = capital × ce facteur, sur 20 ans
TAUX_EFFORT_MAX = 0.33


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_view(view_name, select="*", filters=None):
    url, key = get_credentials()
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    params = {"select": select, "limit": 1000}
    if filters:
        params.update(filters)
    rows, offset = [], 0
    while True:
        params["offset"] = offset
        r = requests.get(f"{url}/rest/v1/{view_name}", headers=headers, params=params, timeout=30)
        r.raise_for_status()
        page = r.json()
        rows.extend(page)
        if len(page) < 1000:
            break
        offset += 1000
    return pd.DataFrame(rows)


st.title("🔬 Secteurs statistiques — le niveau le plus fin")
st.caption(
    "10 631 secteurs (Wallonie + Bruxelles) — bien plus fin que la commune (283) ou même les "
    "quartiers bruxellois (145). Typologie des logements, des ménages, revenus, capacité d'achat, "
    "loyers, taux d'effort."
)

with st.expander("ℹ️ Comment chaque chiffre est calculé — et ce qui reste au niveau commune"):
    st.markdown(f"""
**Formule capacité d'emprunt / taux d'effort** (reverse-engineré et vérifié à l'exact contre
`communes_capacite_emprunt` sur 4 communes tests) :
- Mensualité max acceptable = revenu mensuel du ménage × **33%**
- Sur 20 ans, taux implicite ≈ 3,7%/an → mensualité pour un capital emprunté X = X × **{ANNUITE_FACTEUR}**
- Capacité d'emprunt = mensualité max ÷ {ANNUITE_FACTEUR}
- **Taux d'effort réel** pour acheter au prix médian = (prix × {ANNUITE_FACTEUR}) ÷ revenu mensuel — au-dessus
  de 33%, le ménage médian ne peut normalement plus emprunter pour ce prix.

⚠️ **Le revenu secteur est "par déclaration fiscale"**, pas "par ménage" (un ménage peut avoir 2 déclarations).
La capacité d'emprunt calculée ici avec ce revenu est donc un **ordre de grandeur**, pas directement comparable
chiffre pour chiffre à celle de la page Communes (qui utilise le revenu par ménage, plus précis mais
indisponible au niveau secteur).

**Ce qui n'existe qu'au niveau commune, affiché ici en repli explicite** :
- Typologie fine des ménages (couples avec/sans enfant, isolés, monoparentaux — IWEPS WalStat)
- Loyers (`communes_loyers_marche`)
- Prix immobilier commune (utilisé quand le secteur n'a pas assez de ventes — moins de 6% des secteurs
  ont un prix propre, seuil de confidentialité Statbel à 16 ventes/an)

**Ce qui est réel et propre au secteur** : population, densité, chômage, diplôme supérieur, âge (Recensement
2021), revenu médian (Statbel fiscal), taille des ménages et % isolés (Recensement), type de bâti
(période de construction), taille des logements (nb de pièces), % propriétaires, % logements inoccupés
(⚠️ inclut probablement les résidences secondaires, pas une vacance structurelle stricte).
""")

geo = fetch_view("v_dashboard_secteurs")
if geo.empty:
    st.warning("Aucune donnée reçue.")
    st.stop()

num_cols = [
    "population", "densite_hab_km2", "nb_menages", "taille_menage_moyenne_census", "pct_menages_isoles",
    "taux_chomage_pct", "pct_diplome_superieur", "pct_0_14", "pct_65_plus", "revenu_median_net",
    "pct_batiments_avant_1945", "pct_batiments_apres_2000", "nb_pieces_moyen", "pct_logements_1_2_pieces",
    "pct_proprietaires_census", "pct_logements_inoccupes", "prix_median_maison", "nb_transactions_maison",
    "prix_median_appart", "nb_transactions_appart", "commune_revenu_median_menage",
    "commune_capacite_emprunt_20ans", "commune_prix_median_maison", "commune_pct_familles",
    "commune_pct_couples_sans_enfant", "commune_pct_isoles", "commune_pct_monoparental",
    "commune_taille_menage_2026", "centroid_lat", "centroid_lng", "superficie_ha",
]
for c in num_cols:
    if c in geo.columns:
        geo[c] = pd.to_numeric(geo[c], errors="coerce")

# --- Capacité d'achat / taux d'effort calculés (secteur, indicatif) ---
geo["revenu_mensuel_secteur"] = geo["revenu_median_net"] / 12
geo["capacite_emprunt_secteur_est"] = (geo["revenu_mensuel_secteur"] * TAUX_EFFORT_MAX) / ANNUITE_FACTEUR
prix_ref = geo["prix_median_maison"].fillna(geo["commune_prix_median_maison"])
geo["prix_reference_utilise"] = prix_ref
geo["mensualite_pour_prix_ref"] = prix_ref * ANNUITE_FACTEUR
geo["taux_effort_achat_pct"] = (geo["mensualite_pour_prix_ref"] / geo["revenu_mensuel_secteur"] * 100)

st.markdown("---")

# --- Sélection ---
c1, c2 = st.columns([1, 1])
with c1:
    communes = sorted(geo["nom_commune"].dropna().unique().tolist())
    default_idx = communes.index("Mons") if "Mons" in communes else 0
    commune_sel = st.selectbox("Commune", communes, index=default_idx)

scope = geo[geo["nom_commune"] == commune_sel].copy()

with c2:
    secteurs = sorted(scope["nom_secteur"].dropna().unique().tolist())
    secteur_sel = st.selectbox("Secteur", secteurs) if secteurs else None

loyers_commune = fetch_view("v_dashboard_loyers")
if not loyers_commune.empty:
    code_ins_sel = scope["code_ins"].iloc[0] if not scope.empty else None
    loyers_scope = loyers_commune[loyers_commune["code_ins"] == code_ins_sel] if code_ins_sel else pd.DataFrame()
else:
    loyers_scope = pd.DataFrame()

st.markdown("---")

if secteur_sel:
    row = scope[scope["nom_secteur"] == secteur_sel].iloc[0]

    st.subheader(f"📋 {row['nom_secteur'].title()} — {row['nom_commune']}")
    st.caption(f"{row['nom_province']} · secteur statistique {row['cd_sector']}")

    st.markdown("**Démographie**")
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Population", f"{row['population']:,.0f}".replace(",", " ") if pd.notna(row["population"]) else "n.c.")
    d2.metric("Densité", f"{row['densite_hab_km2']:,.0f} hab/km²".replace(",", " ") if pd.notna(row["densite_hab_km2"]) else "n.c.")
    d3.metric("Taux de chômage", f"{row['taux_chomage_pct']:.1f} %" if pd.notna(row["taux_chomage_pct"]) else "n.c.",
              help="Recensement 2021 — chômeurs / (chômeurs+emploi) parmi la population active. Exhaustif, pas un seuil de confidentialité comme pour les prix.")
    d4.metric("Diplôme supérieur", f"{row['pct_diplome_superieur']:.0f} %" if pd.notna(row["pct_diplome_superieur"]) else "n.c.",
              help="Part de bachelier+master+doctorat parmi la population de niveau connu, Recensement 2021.")

    st.markdown("**Ménages**")
    m1, m2, m3 = st.columns(3)
    m1.metric("Taille moyenne (secteur)", f"{row['taille_menage_moyenne_census']:.2f} pers." if pd.notna(row["taille_menage_moyenne_census"]) else "n.c.")
    m2.metric("% ménages isolés (secteur)", f"{row['pct_menages_isoles']:.0f} %" if pd.notna(row["pct_menages_isoles"]) else "n.c.")
    m3.metric("Nb ménages", f"{row['nb_menages']:,.0f}".replace(",", " ") if pd.notna(row["nb_menages"]) else "n.c.")
    if pd.notna(row.get("commune_pct_familles")):
        st.caption(
            f"⚠️ Typologie fine non disponible au secteur — au niveau **{row['nom_commune']}** (commune entière) : "
            f"familles avec enfants {row['commune_pct_familles']:.0f}% · couples sans enfant {row['commune_pct_couples_sans_enfant']:.0f}% · "
            f"isolés {row['commune_pct_isoles']:.0f}% · monoparental {row['commune_pct_monoparental']:.0f}% (IWEPS WalStat, maj 06/07/2026)."
        )

    st.markdown("**Logement — typologie**")
    l1, l2, l3 = st.columns(3)
    l1.metric("Bâti avant 1945", f"{row['pct_batiments_avant_1945']:.0f} %" if pd.notna(row["pct_batiments_avant_1945"]) else "n.c.")
    l2.metric("Nb pièces moyen", f"{row['nb_pieces_moyen']:.1f}" if pd.notna(row["nb_pieces_moyen"]) else "n.c.",
              help="Convention belge de comptage des pièces — large, pas juste les chambres.")
    l3.metric("% petits logements (1-2 pièces)", f"{row['pct_logements_1_2_pieces']:.0f} %" if pd.notna(row["pct_logements_1_2_pieces"]) else "n.c.")
    l4, l5 = st.columns(2)
    l4.metric("% propriétaires", f"{row['pct_proprietaires_census']:.0f} %" if pd.notna(row["pct_proprietaires_census"]) else "n.c.")
    l5.metric("% logements inoccupés", f"{row['pct_logements_inoccupes']:.0f} %" if pd.notna(row["pct_logements_inoccupes"]) else "n.c.",
              help="⚠️ Inclut probablement les résidences secondaires (définition recensement) — pas une vacance structurelle stricte, surtout en zone touristique/rurale.")

    st.markdown("**Revenus, prix, capacité d'achat**")
    r1, r2 = st.columns(2)
    r1.metric("Revenu médian (secteur, par déclaration)", f"{row['revenu_median_net']:,.0f} €".replace(",", " ") if pd.notna(row["revenu_median_net"]) else "n.c.",
              help="Statbel fiscal — par déclaration, pas par ménage (un ménage peut avoir 2 déclarations). Ne pas confondre avec le revenu par ménage utilisé au niveau commune.")
    prix_src = "secteur" if pd.notna(row["prix_median_maison"]) else ("commune (secteur indisponible)" if pd.notna(row["commune_prix_median_maison"]) else None)
    r2.metric(f"Prix maison de référence ({prix_src or 'n.c.'})",
              f"{row['prix_reference_utilise']:,.0f} €".replace(",", " ") if pd.notna(row["prix_reference_utilise"]) else "n.c.",
              help="Prix du secteur si disponible (≥16 ventes/an), sinon prix de la commune entière.")

    r3, r4 = st.columns(2)
    r3.metric("Capacité d'emprunt estimée (secteur)", f"{row['capacite_emprunt_secteur_est']:,.0f} €".replace(",", " ") if pd.notna(row["capacite_emprunt_secteur_est"]) else "n.c.",
              help="33% du revenu mensuel (par déclaration) sur 20 ans — voir l'encadré de formule en haut de page. Indicatif, pas directement comparable à la capacité communale (revenu par ménage).")
    effort = row["taux_effort_achat_pct"]
    r4.metric("Taux d'effort pour acheter au prix de référence",
              f"{effort:.0f} %" if pd.notna(effort) else "n.c.",
              delta="au-dessus du seuil de 33%" if pd.notna(effort) and effort > 33 else ("dans le seuil" if pd.notna(effort) else None),
              delta_color="inverse" if pd.notna(effort) and effort > 33 else "normal",
              help="Mensualité pour acheter au prix de référence ÷ revenu mensuel. Au-dessus de 33%, le ménage médian du secteur ne peut normalement plus se loger en achetant à ce prix.")

    if not loyers_scope.empty:
        st.markdown("**Loyers (commune — non disponible au secteur)**")
        loy_table = loyers_scope[["type_bien", "nb_chambres", "loyer_median", "annee"]].rename(columns={
            "type_bien": "Type", "nb_chambres": "Chambres", "loyer_median": "Loyer médian (€)", "annee": "Année réf.",
        })
        st.dataframe(loy_table, hide_index=True, use_container_width=True,
                     column_config={"Loyer médian (€)": st.column_config.NumberColumn(format="%d €")})
        best_loyer = loyers_scope.sort_values("annee", ascending=False).iloc[0]
        loyer_val = pd.to_numeric(best_loyer["loyer_median"], errors="coerce")
        if pd.notna(loyer_val) and pd.notna(row["revenu_mensuel_secteur"]):
            taux_effort_loc = loyer_val / row["revenu_mensuel_secteur"] * 100
            st.metric("Taux d'effort locatif indicatif (loyer commune ÷ revenu secteur)", f"{taux_effort_loc:.0f} %")
    else:
        st.caption("Pas de donnée de loyer pour cette commune.")

st.markdown("---")

# --- Carte des secteurs de la commune ---
st.subheader(f"🗺️ Carte des secteurs — {commune_sel}")
map_df = scope.dropna(subset=["centroid_lat", "centroid_lng"])
metric_choice = st.selectbox(
    "Colorer par",
    ["pct_menages_isoles", "taux_effort_achat_pct", "pct_logements_inoccupes", "pct_batiments_avant_1945", "revenu_median_net"],
    format_func=lambda x: {
        "pct_menages_isoles": "% ménages isolés", "taux_effort_achat_pct": "Taux d'effort achat (%)",
        "pct_logements_inoccupes": "% logements inoccupés", "pct_batiments_avant_1945": "% bâti avant 1945",
        "revenu_median_net": "Revenu médian (€)",
    }[x],
)
if not map_df.empty and map_df[metric_choice].notna().any():
    m = folium.Map(location=[map_df["centroid_lat"].mean(), map_df["centroid_lng"].mean()], zoom_start=13, tiles="OpenStreetMap")
    valid = map_df[metric_choice].dropna()
    vmin, vmax = valid.min(), valid.max()
    for _, r in map_df.iterrows():
        val = r[metric_choice]
        if pd.notna(val):
            frac = (val - vmin) / (vmax - vmin) if vmax > vmin else 0.5
            color = f"#{int(255*frac):02x}{int(180*(1-frac)):02x}40"
        else:
            color = "#999999"
        is_sel = secteur_sel and r["nom_secteur"] == secteur_sel
        folium.CircleMarker(
            location=[r["centroid_lat"], r["centroid_lng"]],
            radius=8 if is_sel else 5,
            color="#1D2420" if is_sel else color,
            weight=2 if is_sel else 1,
            fill=True, fill_color=color, fill_opacity=0.8,
            popup=folium.Popup(f"<b>{r['nom_secteur'].title()}</b><br>{metric_choice} : {val:.1f}" if pd.notna(val) else r["nom_secteur"].title(), max_width=200),
        ).add_to(m)
    st_folium(m, use_container_width=True, height=460, returned_objects=[])
    st.caption("Rouge = valeur haute, vert = valeur basse pour l'indicateur choisi. Cercle noir = secteur sélectionné. Position au centroïde du secteur.")
else:
    st.info("Pas assez de données géolocalisées pour cette commune.")

st.markdown("---")

# --- Tableau classant tous les secteurs de la commune ---
st.subheader(f"Tous les secteurs de {commune_sel}")
table = scope.sort_values("pct_menages_isoles", ascending=False, na_position="last")[[
    "nom_secteur", "population", "pct_menages_isoles", "taille_menage_moyenne_census",
    "pct_batiments_avant_1945", "nb_pieces_moyen", "pct_logements_inoccupes",
    "revenu_median_net", "taux_effort_achat_pct",
]].rename(columns={
    "nom_secteur": "Secteur", "population": "Population", "pct_menages_isoles": "% isolés",
    "taille_menage_moyenne_census": "Taille ménage", "pct_batiments_avant_1945": "% avant 1945",
    "nb_pieces_moyen": "Pièces/logt", "pct_logements_inoccupes": "% inoccupés",
    "revenu_median_net": "Revenu médian (€)", "taux_effort_achat_pct": "Taux d'effort achat (%)",
})
st.dataframe(
    table, use_container_width=True, height=400, hide_index=True,
    column_config={
        "Population": st.column_config.NumberColumn(format="%d"),
        "% isolés": st.column_config.NumberColumn(format="%.0f %%"),
        "Taille ménage": st.column_config.NumberColumn(format="%.2f"),
        "% avant 1945": st.column_config.NumberColumn(format="%.0f %%"),
        "Pièces/logt": st.column_config.NumberColumn(format="%.1f"),
        "% inoccupés": st.column_config.NumberColumn(format="%.0f %%"),
        "Revenu médian (€)": st.column_config.NumberColumn(format="%d €"),
        "Taux d'effort achat (%)": st.column_config.NumberColumn(format="%.0f %%"),
    },
)
st.caption("Triés par % de ménages isolés décroissant — repère rapide des secteurs à forte proportion de petits ménages (potentiel de demande en petits logements si le parc ne suit pas).")
