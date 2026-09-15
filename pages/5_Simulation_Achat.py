"""
Repérage Immo — Simulation d'achat : à un prix donné et un taux donné,
quelle part des ménages d'une commune (ou d'un bassin de vie autour) pourrait
théoriquement financer ce bien ?

Répond à 3 limites identifiées le 2026-09-15 sur la capacité d'achat :
1. % de ménages (pas seulement le ménage médian) — ESTIMATION statistique,
   ajustement log-normal sur (médiane, Q1, Q3) réels Statbel (dataset ADI,
   revenu disponible équivalent administratif — vraiment "par ménage").
2. Taux hypothécaire réglable — taux ECB (MIR, nouveaux contrats ménages
   Belgique) proposé par défaut, ajustable par curseur pour tester des scénarios.
3. Bassin de vie — communes accessibles en voiture (Valhalla, temps réel)
   depuis la commune choisie, pas seulement la commune isolée.

Toutes les limites ci-dessus restent des ESTIMATIONS, explicitement documentées
dans l'UI — jamais présentées comme une donnée Statbel brute.
"""

import math
from datetime import datetime

import pandas as pd
import folium
import requests
import streamlit as st
from streamlit_folium import st_folium

from lib import get_credentials, annuite_facteur, revenu_requis_pour_prix, pct_menages_au_dessus

st.set_page_config(page_title="Simulation d'achat — Repérage Immo", page_icon="🧮", layout="wide")

COMMUNES_VIEW = "v_dashboard_communes"
TAUX_VIEW = "v_dashboard_taux"


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_view(view_name, select="*", params_extra=None):
    url, key = get_credentials()
    if not url or not key:
        st.error("Identifiants Supabase introuvables (st.secrets ou moubri/.env)")
        st.stop()
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows, offset, page_size = [], 0, 1000
    while True:
        params = {"select": select, "limit": page_size, "offset": offset}
        if params_extra:
            params.update(params_extra)
        r = requests.get(f"{url}/rest/v1/{view_name}", headers=headers, params=params, timeout=60)
        r.raise_for_status()
        page = r.json()
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return pd.DataFrame(rows), datetime.now()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_bassin(code_ins_origine, minutes_max):
    """Communes accessibles en voiture depuis `code_ins_origine` en <= minutes_max.
    Temps dans un seul sens (depuis la commune de reference), pas un aller-retour moyenne —
    simplification assumee et documentee dans l'UI."""
    url, key = get_credentials()
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    params = {
        "select": "code_ins_destination,temps_min,distance_km",
        "code_ins_origine": f"eq.{code_ins_origine}",
        "temps_min": f"lte.{minutes_max}",
        "limit": 1000,
    }
    r = requests.get(f"{url}/rest/v1/v_dashboard_matrice_trajet", headers=headers, params=params, timeout=30)
    r.raise_for_status()
    return pd.DataFrame(r.json())


st.title("🧮 Simulation d'achat")
st.caption(
    "À un prix et un taux donnés, quelle part des ménages pourrait financer ce bien — "
    "dans une commune, ou dans un bassin de vie autour."
)

with st.spinner("Chargement des données..."):
    communes, fetched_at = fetch_view(
        COMMUNES_VIEW,
        select="code_ins,nom_commune,nom_province,population_totale,prix_median_maison,"
        "revenu_median_menage,capacite_emprunt_20ans,lognormal_mu,lognormal_sigma,"
        "centroid_lat,centroid_lng",
    )
    taux_df, _ = fetch_view(TAUX_VIEW)

if communes.empty:
    st.warning("Aucune donnée reçue.")
    st.stop()

for c in ["population_totale", "prix_median_maison", "revenu_median_menage",
          "capacite_emprunt_20ans", "lognormal_mu", "lognormal_sigma"]:
    communes[c] = pd.to_numeric(communes[c], errors="coerce")

taux_hypo_row = taux_df[taux_df["type_taux"] == "hypothecaire_be_nouveau"]
taux_defaut = float(taux_hypo_row["valeur"].iloc[0]) if not taux_hypo_row.empty else 3.7
date_taux = taux_hypo_row["date"].iloc[0] if not taux_hypo_row.empty else None

n_lognormal = communes["lognormal_mu"].notna().sum()
st.caption(
    f"⚠️ Le % de ménages est une **estimation statistique** (ajustement log-normal sur médiane/Q1/Q3 "
    f"Statbel réels, {n_lognormal}/{len(communes)} communes couvertes) — pas un comptage direct. "
    "Taux par défaut : dernier taux hypothécaire moyen réel publié par la BCE "
    f"({taux_defaut:.2f} % au {date_taux}), ajustable ci-dessous."
)

# --- Paramètres de simulation ---
col1, col2 = st.columns([2, 1])
with col1:
    commune_options = communes.sort_values("nom_commune")[["code_ins", "nom_commune", "nom_province"]]
    commune_labels = [f"{r.nom_commune} ({r.nom_province})" for r in commune_options.itertuples()]
    idx = st.selectbox("Commune de référence", range(len(commune_labels)), format_func=lambda i: commune_labels[i])
    code_ins_ref = commune_options.iloc[idx]["code_ins"]
    row_ref = communes[communes["code_ins"] == code_ins_ref].iloc[0]

with col2:
    minutes_max = st.slider(
        "Bassin de vie : communes à moins de … minutes en voiture", 0, 60, 0, step=5,
        help="0 = commune seule. Temps de trajet réel (Valhalla), calculé une fois, dans le sens "
        "depuis la commune de référence."
    )

prix_defaut = float(row_ref["prix_median_maison"]) if pd.notna(row_ref["prix_median_maison"]) else 250000.0
col3, col4, col5 = st.columns(3)
with col3:
    prix_simule = st.number_input("Prix du bien simulé (€)", min_value=10000, max_value=2000000,
                                   value=int(round(prix_defaut, -3)), step=5000)
with col4:
    taux_simule = st.slider("Taux hypothécaire annuel (%)", 1.0, 8.0, round(taux_defaut, 2), step=0.05)
with col5:
    duree_simulee = st.selectbox("Durée du prêt (ans)", [15, 20, 25, 30], index=1)

# --- Bassin de communes ---
if minutes_max > 0:
    bassin = fetch_bassin(code_ins_ref, minutes_max)
    codes_bassin = set(bassin["code_ins_destination"]) | {code_ins_ref}
else:
    codes_bassin = {code_ins_ref}

df_bassin = communes[communes["code_ins"].isin(codes_bassin)].copy()

# --- Calcul d'affordabilité ---
mensualite = prix_simule * annuite_facteur(taux_simule, duree_simulee)
revenu_requis = revenu_requis_pour_prix(prix_simule, taux_simule, duree_simulee)

df_bassin["pct_menages_ok"] = df_bassin.apply(
    lambda r: pct_menages_au_dessus(revenu_requis, r["lognormal_mu"], r["lognormal_sigma"]), axis=1
)

pop_valide = df_bassin.loc[df_bassin["pct_menages_ok"].notna(), "population_totale"].sum()
if pop_valide > 0:
    pct_agrege = (
        df_bassin.loc[df_bassin["pct_menages_ok"].notna(), "pct_menages_ok"]
        * df_bassin.loc[df_bassin["pct_menages_ok"].notna(), "population_totale"]
    ).sum() / pop_valide
else:
    pct_agrege = None

st.divider()
st.subheader(f"Résultat — {prix_simule:,.0f} € à {taux_simule:.2f} % sur {duree_simulee} ans".replace(",", " "))

k1, k2, k3, k4 = st.columns(4)
k1.metric("Mensualité correspondante", f"{mensualite:,.0f} €/mois".replace(",", " "))
k2.metric("Revenu annuel ménage requis", f"{revenu_requis:,.0f} €".replace(",", " "),
          help="Effort de 33% du revenu mensuel, même règle que le reste du dashboard.")
if pct_agrege is not None:
    label_zone = row_ref["nom_commune"] if minutes_max == 0 else f"bassin autour de {row_ref['nom_commune']} ({minutes_max} min)"
    k3.metric(f"% ménages pouvant acheter — {label_zone}", f"{pct_agrege:.0f} %")
else:
    k3.metric("% ménages pouvant acheter", "n.c.", help="Pas de distribution de revenu disponible pour cette zone.")
k4.metric("Ménage médian peut acheter ?",
          "Oui" if pd.notna(row_ref["revenu_median_menage"]) and row_ref["revenu_median_menage"] * 1.0 >= 0
          and (row_ref["revenu_median_menage"] >= revenu_requis) else "Non",
          help=f"Revenu médian de {row_ref['nom_commune']} : "
               f"{row_ref['revenu_median_menage']:,.0f} €".replace(",", " ") if pd.notna(row_ref["revenu_median_menage"]) else "Donnée manquante")

st.divider()
st.subheader("Détail par commune du bassin" if minutes_max > 0 else "Détail")

table = df_bassin[["nom_commune", "nom_province", "population_totale", "prix_median_maison",
                    "revenu_median_menage", "pct_menages_ok"]].copy()
table = table.sort_values("pct_menages_ok", ascending=False, na_position="last")
st.dataframe(
    table,
    column_config={
        "nom_commune": "Commune",
        "nom_province": "Province",
        "population_totale": st.column_config.NumberColumn("Population", format="%d"),
        "prix_median_maison": st.column_config.NumberColumn("Prix médian maison (€)", format="%d €"),
        "revenu_median_menage": st.column_config.NumberColumn("Revenu médian ménage (€/an)", format="%d €"),
        "pct_menages_ok": st.column_config.NumberColumn("% ménages pouvant acheter à ce prix/taux", format="%.0f %%"),
    },
    hide_index=True, use_container_width=True,
)

if minutes_max > 0 and len(df_bassin) > 1:
    st.subheader("Carte du bassin")
    m = folium.Map(location=[row_ref["centroid_lat"], row_ref["centroid_lng"]], zoom_start=9, tiles="CartoDB positron")
    for r in df_bassin.itertuples():
        if pd.isna(r.centroid_lat) or pd.isna(r.centroid_lng):
            continue
        pct = r.pct_menages_ok
        color = "#999999" if pd.isna(pct) else ("#1a9850" if pct >= 50 else "#fdae61" if pct >= 20 else "#d73027")
        is_ref = r.code_ins == code_ins_ref
        folium.CircleMarker(
            location=[r.centroid_lat, r.centroid_lng],
            radius=10 if is_ref else 6,
            color="#000000" if is_ref else color,
            weight=2 if is_ref else 1,
            fill=True, fill_color=color, fill_opacity=0.8,
            popup=folium.Popup(
                f"<b>{r.nom_commune}</b>{' (référence)' if is_ref else ''}<br>"
                f"% ménages OK : {pct:.0f}%" if pd.notna(pct) else f"<b>{r.nom_commune}</b><br>n.c.",
                max_width=200,
            ),
        ).add_to(m)
    st_folium(m, width=None, height=500, returned_objects=[])

with st.expander("ℹ️ Comment cette simulation est calculée — méthodologie et limites"):
    st.markdown(f"""
**1. Mensualité et revenu requis** — formule d'annuité standard, même règle d'effort que le reste du
dashboard (33% du revenu mensuel, vérifiée à l'exact contre le facteur déjà utilisé pour
`capacite_emprunt_20ans` le 2026-09-13). Généralisée ici à n'importe quel taux et durée, plutôt que le
taux fixe (~3,67%/20 ans) figé dans les données existantes.

**2. % de ménages pouvant acheter** — **estimation statistique**, pas un comptage réel. Le seul revenu
réellement publié par commune est la médiane (et, depuis le 2026-09-15, les 1er/3ème quartiles — dataset
Statbel "Revenu disponible équivalent administratif", méthodologie EU-SILC). À partir de ces 3 points,
un modèle log-normal est ajusté par commune, puis utilisé pour estimer la part de ménages au-dessus d'un
seuil de revenu. Vérifié : le modèle reproduit les 3 points connus à moins de 3% d'écart sur les communes
testées. **Ce n'est pas une vraie distribution observée** — deux communes avec la même médiane mais des
inégalités de revenu différentes donneraient un vrai résultat différent que ce modèle ne peut pas capter.

**3. Bassin de vie** — communes accessibles en voiture depuis la commune de référence en moins de
X minutes, temps de trajet réel calculé une fois via Valhalla (moteur de routage open-source, tuiles
Belgique, sur `acolys-serveur`) — pas à vol d'oiseau. Le temps est **dans un seul sens** (depuis la
commune de référence vers chaque commune du bassin), pas une moyenne aller-retour — simplification
assumée. Agrégation du % de ménages sur le bassin : moyenne pondérée par la population de chaque commune
(mathématiquement exact pour une mixture de distributions, pas juste une moyenne simple).

**4. Taux hypothécaire** — proposé par défaut : dernier taux moyen réel de la BCE (ECB, série MIR,
"Lending for house purchase", nouveaux contrats, ménages belges), mensuel, ~2 mois de décalage de
publication. Toujours ajustable par curseur pour tester un scénario de taux différent.

**Couverture** : {n_lognormal}/{len(communes)} communes ont un modèle de distribution calculable
(nécessite médiane + Q1 + Q3 tous connus). Les autres affichent "n.c." plutôt qu'un chiffre inventé.
""")
