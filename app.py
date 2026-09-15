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

import numpy as np
import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from streamlit_folium import st_folium
import folium
from folium.plugins import MarkerCluster

from lib import get_credentials

DASHBOARD_DIR = Path(__file__).parent
FILTERS_PATH = DASHBOARD_DIR / "last_filters.json"

# Vue Supabase dédiée (lecture seule, clé anon/publishable) — jamais la table
# de base `annonces` ni la clé service_role dans ce dashboard.
SOURCE_VIEW = "v_dashboard_immo"
SELECT_FIELDS = (
    "lat,lng,prix,surface_habitable,type_bien,type_transaction,commune,code_postal,"
    "nb_chambres,annee_construction,peb,jours_sur_marche,url_principale"
)

st.set_page_config(page_title="Repérage Immo", page_icon="🗺️", layout="wide")


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
    # `code_ins` est NULL sur ~98% de la base (Dev Log 2026-08-31) : le seul
    # regroupement géo fiable disponible ici est le texte libre `commune`, qui
    # contient des variantes de casse ("Mons" / "MONS"). On normalise au moins
    # ça pour ne pas fragmenter artificiellement les comparables.
    df["commune_norm"] = df["commune"].str.strip().str.upper()

    # Comparable marché "prix/m²" par commune, tous types/PEB confondus (pour le
    # ratio "vs marché local") — calculé sur tout le jeu de données, pas sur la
    # sélection filtrée, pour rester un vrai comparable indépendant des filtres.
    commune_comp_all = df.groupby("commune_norm")["prix_m2"].median()
    df["commune_prix_m2_median"] = df["commune_norm"].map(commune_comp_all)
    df["ecart_vs_marche_pct"] = (
        (df["prix_m2"] - df["commune_prix_m2_median"]) / df["commune_prix_m2_median"] * 100
    )
    return df, fetched_at


PEB_RENOVES = {"A++", "A+", "A", "B"}


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_contexte_mdb():
    """Contexte commune utile à la décision MdB : prix réellement réalisé (Statbel,
    actes notariés) pour recouper l'ARV, et risque d'affaissement minier."""
    url, key = get_credentials()
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    r = requests.get(
        f"{url}/rest/v1/v_dashboard_communes",
        headers=headers,
        params={"select": "nom_commune,prix_median_maison,dans_zone_houillere,anciennete_active_jours", "limit": 1000},
        timeout=30,
    )
    r.raise_for_status()
    ctx = pd.DataFrame(r.json())
    if not ctx.empty:
        ctx["commune_norm"] = ctx["nom_commune"].str.strip().str.upper()
        for c in ["prix_median_maison", "anciennete_active_jours"]:
            ctx[c] = pd.to_numeric(ctx[c], errors="coerce")
    return ctx


def compute_mdb_scores(
    df_scope, df_all, peb_cibles, taux_enregistrement_pct, cout_travaux_m2,
    frais_vente_pct, taux_financier_annuel_pct, duree_portage_mois, appliquer_isoc, isoc_pct,
    decote_arv_pct=8.0, frais_notaire_pct=1.6, frais_acte_eur=1300,
    tva_travaux_pct=6.0, charges_portage_mensuelles=180,
):
    """Marge d'un flip MdB, structurée comme `charge_fonciere.py` (Dev Log
    2026-08-31 : C = (R_nette − Cc − Ca − Marge − 0,5·Cc·f) / (1 + e + f)) —
    mais ici le prix d'achat est CONNU (l'annonce), donc pas de circularité à
    résoudre : on calcule la marge résultante plutôt que le prix maximum.

    Corrige les deux trous identifiés dans l'ancienne formule "référence
    rapide" (notés comme un bug réel dans Moubri.md sur le rapport Fichaux 6) :
    les frais de revente et le coût de portage/financement étaient absents.

    ARV comparé sur des ANNONCES (prix demandés, pas des ventes réelles) —
    limite connue, à améliorer en branchant `transactions_spf_wallonie`.
    """
    # ⚠️ Comparables et scope limités à la VENTE — un loyer mensuel (ex. 2100 €)
    # traité comme un prix d'achat produirait un prix/m² comparable délirant et
    # fausserait l'ARV même pour les biens à vendre analysés en même temps.
    comp_base = df_all[df_all["peb"].isin(PEB_RENOVES) & (df_all["type_transaction"] == "vente")]
    comp_commune = comp_base.groupby("commune_norm")["prix_m2"].median()
    comp_region = comp_base.groupby("region")["prix_m2"].median()
    comp_commune_n = comp_base.groupby("commune_norm")["prix_m2"].count()

    # Garde-fou plausibilité : la surface habitable est un champ connu pour ses
    # erreurs de collecte (souvent confondue avec la surface du terrain). Une
    # "maison" de 2000 m² à 100 000 € n'est pas un bon deal, c'est une donnée
    # fausse — sans ce filtre elle remonte artificiellement en tête de liste.
    surface_ok = (
        (df_scope["type_bien"].eq("appartement") & df_scope["surface_habitable"].between(15, 350))
        | (df_scope["type_bien"].eq("maison") & df_scope["surface_habitable"].between(25, 600))
    )
    d = df_scope[
        (df_scope["type_transaction"] == "vente")
        & df_scope["type_bien"].isin(["maison", "appartement"])
        & df_scope["peb"].isin(peb_cibles)
        & df_scope["prix"].notna()
        & surface_ok
    ].copy()
    if d.empty:
        return d

    n_comp = d["commune_norm"].map(comp_commune_n).fillna(0)
    comp_m2 = d["commune_norm"].map(comp_commune)
    comp_m2 = comp_m2.where(n_comp >= 5, d["region"].map(comp_region))
    d["n_comparables"] = n_comp.astype(int)
    d["comparable_source"] = ["commune" if n >= 5 else "région" for n in n_comp]

    e = taux_enregistrement_pct / 100
    f = (taux_financier_annuel_pct / 100) * (duree_portage_mois / 12)

    # ARV : les comparables sont des PRIX DEMANDÉS. Mesuré le 2026-09-16 sur 81 communes :
    # le prix demandé médian vaut ~1,24× le prix réellement acté (Statbel). Une partie est
    # un effet de stock (les biens surévalués restent en vitrine), une partie est la vraie
    # marge de négociation — d'où une décote paramétrable plutôt qu'un chiffre figé.
    # L'ancien code appliquait ×1,05, ce qui AGGRAVAIT le biais optimiste.
    d["arv_brut"] = comp_m2 * d["surface_habitable"] * (1 - decote_arv_pct / 100)

    # Travaux TVA comprise (6% si bâtiment >10 ans, 21% sinon) — 15 points d'écart
    # sur tout le budget travaux, soit souvent plus que la marge de sécurité du deal.
    d["cout_travaux"] = d["surface_habitable"] * cout_travaux_m2 * (1 + tva_travaux_pct / 100)

    # Coûts d'acquisition : droits d'enregistrement + honoraires notaire + frais d'acte.
    # Les deux derniers étaient totalement absents : ~3-4 k€ sur un bien à 150 k€,
    # soit 2-3% du deal pris directement sur la marge.
    d["cout_enregistrement"] = d["prix"] * e
    d["frais_acquisition"] = d["prix"] * (frais_notaire_pct / 100) + frais_acte_eur

    # Portage : capital immobilisé = prix payé dès le jour 1 + travaux tirés
    # progressivement (approximé à la moitié du budget travaux, comme dans
    # charge_fonciere.py : "0,5·Cc·f").
    d["cout_financier"] = (d["prix"] + 0.5 * d["cout_travaux"]) * f
    # Charges de détention (précompte immobilier, assurance, énergie, syndic) —
    # absentes de l'ancienne formule.
    d["charges_portage"] = charges_portage_mensuelles * duree_portage_mois
    d["cout_vente"] = d["arv_brut"] * (frais_vente_pct / 100)

    d["marge_eur"] = (
        d["arv_brut"] - d["prix"] - d["cout_enregistrement"] - d["frais_acquisition"]
        - d["cout_travaux"] - d["cout_financier"] - d["charges_portage"] - d["cout_vente"]
    )
    if appliquer_isoc:
        d["marge_eur"] = d["marge_eur"].clip(lower=0) * (1 - isoc_pct / 100) + d["marge_eur"].clip(upper=0)
    d["marge_pct"] = (d["marge_eur"] / d["arv_brut"] * 100)

    # Capital réellement immobilisé — base de rendement bien plus parlante pour un MdB
    # que la marge rapportée à l'ARV.
    d["capital_engage"] = (
        d["prix"] + d["cout_enregistrement"] + d["frais_acquisition"]
        + d["cout_travaux"] + d["charges_portage"]
    )
    d["roi_pct"] = d["marge_eur"] / d["capital_engage"] * 100
    # La rotation du capital EST le métier : 12% en 6 mois bat 20% en 18 mois.
    d["roi_annualise_pct"] = d["roi_pct"] * (12 / max(duree_portage_mois, 1))

    d = d[d["arv_brut"].notna() & d["marge_pct"].notna()]
    return d


def compute_mdb_scores_enrichi(*args, **kwargs):
    """compute_mdb_scores + le contexte que le praticien regarde AVANT de se déplacer :
    l'ARV tient-il face au marché réellement acté, et y a-t-il un risque de sol
    (affaissement minier) qui tue le deal ou justifie une renégociation."""
    d = compute_mdb_scores(*args, **kwargs)
    if d.empty:
        return d
    try:
        ctx = fetch_contexte_mdb()
    except Exception:
        return d
    if ctx.empty:
        return d

    d = d.merge(
        ctx[["commune_norm", "prix_median_maison", "dans_zone_houillere", "anciennete_active_jours"]],
        on="commune_norm", how="left",
    )
    # Garde-fou de sortie : un ARV très au-dessus du prix médian RÉELLEMENT acté de la
    # commune signale une hypothèse de revente agressive (ou un bien atypique) — c'est
    # là que les plans de MdB se cassent, pas sur le coût des travaux.
    d["arv_vs_marche_pct"] = (d["arv_brut"] / d["prix_median_maison"] - 1) * 100
    d["alerte_arv"] = d["arv_vs_marche_pct"] > 40
    return d


def statut_mdb(marge_pct, seuil_go_fort, seuil_go, seuil_limite):
    if marge_pct >= seuil_go_fort:
        return "🔴 GO fort"
    if marge_pct >= seuil_go:
        return "🟡 GO"
    if marge_pct >= seuil_limite:
        return "⚪ Limite"
    return "⬛ Écarté"


# ---------------------------------------------------------------- score colocation
# Formule reprise telle quelle de pipeline/Score-colocation-formula.md (vault Moubri,
# v1.0 2026-06-08) — jamais reliée à rien jusqu'ici. Points de référence (universités,
# pôles d'emploi) fixés à la main sur des coordonnées de campus/centres-villes connus —
# approximatif à l'échelle de la commune, pas une donnée mesurée.

UNIVERSITES = [
    ("UMONS", 50.454, 3.956), ("ULB", 50.8136, 4.3805), ("VUB", 50.8221, 4.3958),
    ("ULiège", 50.6326, 5.5797), ("UCLouvain", 50.6683, 4.6114),
    ("UNamur", 50.4653, 4.8657), ("UCLouvain Charleroi", 50.4108, 4.4446),
    ("Tournai (FUCaM/HE)", 50.6053, 3.3888), ("ULiège Arlon", 49.6833, 5.8167),
]
POLES_EMPLOI = [
    ("Bruxelles", 50.8503, 4.3517), ("Namur", 50.4674, 4.8718), ("Liège", 50.6326, 5.5797),
    ("Charleroi", 50.4108, 4.4446), ("Mons", 50.4542, 3.9523), ("Tournai", 50.6053, 3.3888),
    ("Arlon", 49.6833, 5.8167), ("Wavre", 50.7167, 4.6000), ("La Louvière", 50.4667, 4.1833),
]


def _score_band(val, bands):
    """bands: liste de (seuil_max, score), triée croissant ; dernier = au-delà."""
    for seuil, score in bands[:-1]:
        if val <= seuil:
            return score
    return bands[-1][1]


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_reference(view_name, select):
    url, key = get_credentials()
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    r = requests.get(f"{url}/rest/v1/{view_name}", headers=headers, params={"select": select, "limit": 2000}, timeout=30)
    r.raise_for_status()
    return pd.DataFrame(r.json())


def compute_colocation_scores(df_scope):
    """Score colocation par annonce (location uniquement), formule vault v1.0.
    Le veto 'électricité non conforme' du document original N'EST PAS calculable
    depuis les annonces — jamais appliqué automatiquement, à vérifier en visite."""
    d = df_scope[
        (df_scope["type_transaction"] == "location")
        & df_scope["lat"].notna() & df_scope["lng"].notna()
        & df_scope["type_bien"].isin(["maison", "appartement"])
    ].copy()
    if d.empty:
        return d

    communes_ref = fetch_reference("v_dashboard_communes", "code_ins,nom_commune,densite_hab_km2,distance_gare_km")
    loyers_ref = fetch_reference("v_dashboard_loyers", "code_ins,type_bien,loyer_median,annee").rename(columns={"annee": "annee_loyer"})
    communes_ref["commune_norm"] = communes_ref["nom_commune"].str.strip().str.upper()
    d["commune_norm"] = d["commune"].str.strip().str.upper()
    d = d.merge(communes_ref[["commune_norm", "code_ins", "densite_hab_km2", "distance_gare_km"]], on="commune_norm", how="left")
    d = d.merge(loyers_ref, on=["code_ins", "type_bien"], how="left")

    def nearest(lat, lng, points):
        plat = np.array([p[1] for p in points])
        plng = np.array([p[2] for p in points])
        lat_v = lat.to_numpy()[:, None]
        lng_v = lng.to_numpy()[:, None]
        dd = 111.111 * np.sqrt((lat_v - plat[None, :]) ** 2 + ((lng_v - plng[None, :]) * np.cos(np.radians(lat_v))) ** 2)
        return dd.min(axis=1)

    d["dist_universite_km"] = nearest(d["lat"], d["lng"], UNIVERSITES)
    d["dist_emploi_km"] = nearest(d["lat"], d["lng"], POLES_EMPLOI)

    d["s_uni"] = d["dist_universite_km"].apply(lambda v: _score_band(v, [(10, 10), (20, 7), (40, 4), (999, 1)]))
    d["s_emploi"] = d["dist_emploi_km"].apply(lambda v: _score_band(v, [(5, 10), (15, 7), (30, 4), (999, 1)]))
    d["s_transport"] = d["distance_gare_km"].apply(lambda v: _score_band(v, [(1, 10), (3, 8), (6, 6), (15, 4), (999, 1)]) if pd.notna(v) else 5)
    d["s_densite"] = d["densite_hab_km2"].apply(lambda v: _score_band(v, [(1000, 2), (5000, 5), (10000, 8), (999999, 10)]) if pd.notna(v) else 5)
    d["s_chambres"] = d["nb_chambres"].apply(lambda v: _score_band(v, [(1, 1), (2, 4), (3, 7), (99, 10)]) if pd.notna(v) else 4)
    d["s_loyer"] = d["loyer_median"].apply(lambda v: _score_band(v, [(400, 10), (600, 7), (900, 4), (999999, 1)]) if pd.notna(v) else 5)

    d["score_brut"] = (
        25 * d["s_uni"] + 20 * d["s_emploi"] + 15 * d["s_transport"]
        + 15 * d["s_densite"] + 15 * d["s_chambres"] + 10 * d["s_loyer"]
    ) / 100

    d["score_coloc"] = d["score_brut"]
    d.loc[d["dist_universite_km"] <= 15, "score_coloc"] += 2
    d.loc[(d["densite_hab_km2"] > 5000), "score_coloc"] += 1
    d.loc[(d["densite_hab_km2"] < 500) & (d["dist_emploi_km"] > 40), "score_coloc"] -= 1
    d["score_coloc"] = d["score_coloc"].clip(1, 10).round(1)

    def verdict(s):
        if s >= 7:
            return "🟢 Excellente"
        if s >= 5:
            return "🟡 Viable"
        if s >= 3:
            return "🟠 Location entière préférable"
        return "🔴 Impossible"

    d["verdict_coloc"] = d["score_coloc"].apply(verdict)
    return d


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

st.sidebar.markdown("---")
st.sidebar.subheader("🎯 Analyse marchand de biens")
with st.sidebar.expander("Hypothèses de calcul", expanded=False):
    peb_cibles = st.multiselect("PEB cible (achat dégradé)", ["G", "F", "E", "D", "C"], default=defaults.get("peb_cibles", ["G", "F", "E"]))
    taux_enregistrement_pct = st.number_input("Droits d'enregistrement (%)", min_value=0.0, max_value=20.0, value=defaults.get("taux_enregistrement_pct", 7.5), step=0.5, help="Non confirmé avec un comptable (régime MdB) — voir Shortlist-GO-Mons-2026-06-11.")
    frais_notaire_pct = st.number_input("Honoraires notaire (%)", min_value=0.0, max_value=5.0, value=defaults.get("frais_notaire_pct", 1.6), step=0.1, help="Honoraires dégressifs, hors droits d'enregistrement. Étaient totalement absents du calcul avant le 16/09.")
    frais_acte_eur = st.number_input("Frais d'acte fixes (€)", min_value=0, max_value=10000, value=defaults.get("frais_acte_eur", 1300), step=100, help="Recherches, formalités, transcription hypothécaire.")
    decote_arv_pct = st.number_input("Décote prix demandé → prix acté (%)", min_value=0.0, max_value=40.0, value=defaults.get("decote_arv_pct", 8.0), step=1.0, help="Les comparables sont des PRIX DEMANDÉS. Mesuré sur 81 communes le 16/09 : le demandé médian vaut 1,24× l'acté Statbel (interquartile 1,12–1,38) — une partie est un effet de stock, une partie une vraie marge de négociation. 8% = prudent ; 0% = vous croyez le prix affiché.")
    cout_travaux_m2 = st.number_input("Coût travaux HTVA (€/m²)", min_value=0, max_value=3000, value=defaults.get("cout_travaux_m2", 850), step=50, help="Hors TVA, forfait unique quel que soit l'écart PEB — à affiner par palier si besoin.")
    tva_travaux_pct = st.number_input("TVA travaux (%)", min_value=0.0, max_value=21.0, value=defaults.get("tva_travaux_pct", 6.0), step=15.0, help="6% pour un bâtiment de plus de 10 ans, 21% sinon. 15 points d'écart sur tout le budget travaux.")
    charges_portage_mensuelles = st.number_input("Charges de détention (€/mois)", min_value=0, max_value=3000, value=defaults.get("charges_portage_mensuelles", 180), step=20, help="Précompte immobilier, assurance, énergie, syndic. Absentes du calcul avant le 16/09.")
    frais_vente_pct = st.number_input("Frais de revente (%)", min_value=0.0, max_value=15.0, value=defaults.get("frais_vente_pct", 6.0), step=0.5, help="Agence + notaire à la revente. Absent de l'ancienne formule — c'est le bug déjà noté sur le rapport Fichaux 6.")
    taux_financier_annuel_pct = st.number_input("Coût du capital annuel (%)", min_value=0.0, max_value=15.0, value=defaults.get("taux_financier_annuel_pct", 5.0), step=0.5, help="Taux de financement ou coût d'opportunité si cash.")
    duree_portage_mois = st.number_input("Durée de portage (mois)", min_value=1, max_value=60, value=defaults.get("duree_portage_mois", 9), step=1)
    appliquer_isoc = st.checkbox("Vente via société — appliquer l'ISOC sur la marge", value=defaults.get("appliquer_isoc", False))
    isoc_pct = st.number_input("Taux ISOC (%)", min_value=0.0, max_value=40.0, value=defaults.get("isoc_pct", 25.0), step=1.0, disabled=not appliquer_isoc)
    st.markdown("**Seuils de classement**")
    seuil_go_fort = st.number_input("Seuil GO fort (marge % ≥)", min_value=0, max_value=200, value=defaults.get("seuil_go_fort", 30), step=5)
    seuil_go = st.number_input("Seuil GO (marge % ≥)", min_value=0, max_value=200, value=defaults.get("seuil_go", 15), step=5)
    seuil_limite = st.number_input("Seuil Limite (marge % ≥)", min_value=0, max_value=200, value=defaults.get("seuil_limite", 5), step=5)

if st.sidebar.button("💾 Sauvegarder ces critères par défaut"):
    save_last_filters({
        "transaction": transaction, "regions": regions_sel, "commune_query": commune_query,
        "budget": [budget_min, budget_max], "surf_min": surf_min, "types": types_sel,
        "chambres_min": chambres_min, "annee_min": annee_min, "jours_max": jours_max,
        "peb_cibles": peb_cibles, "taux_enregistrement_pct": taux_enregistrement_pct,
        "cout_travaux_m2": cout_travaux_m2, "frais_vente_pct": frais_vente_pct,
        "frais_notaire_pct": frais_notaire_pct, "frais_acte_eur": frais_acte_eur,
        "decote_arv_pct": decote_arv_pct, "tva_travaux_pct": tva_travaux_pct,
        "charges_portage_mensuelles": charges_portage_mensuelles,
        "taux_financier_annuel_pct": taux_financier_annuel_pct, "duree_portage_mois": duree_portage_mois,
        "appliquer_isoc": appliquer_isoc, "isoc_pct": isoc_pct,
        "seuil_go_fort": seuil_go_fort, "seuil_go": seuil_go, "seuil_limite": seuil_limite,
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

# ---------------------------------------------------------------- analyse MdB

if transaction == "vente":
    st.subheader("🎯 Meilleures opportunités — achat dégradé → rénovation → revente")
    st.caption(
        "Marge = ARV − prix − enregistrement − **honoraires notaire & frais d'acte** − travaux TVAC "
        "− portage/financement − **charges de détention** − frais de revente"
        + (" − ISOC" if appliquer_isoc else "") + ". "
        f"ARV = comparables PEB A-B du secteur (**prix demandés**) minorés de la décote de négociation "
        f"retenue ({decote_arv_pct:.0f}%). Mesuré le 16/09 sur 81 communes : le prix demandé médian vaut "
        "**1,24× le prix réellement acté** (Statbel) — d'où la décote, et la colonne « ARV vs marché acté » "
        "qui compare chaque ARV au marché réel de la commune. **Un tri pour prioriser les visites, jamais une offre.**"
    )

    mdb = compute_mdb_scores_enrichi(
        f, df, peb_cibles, taux_enregistrement_pct, cout_travaux_m2,
        frais_vente_pct, taux_financier_annuel_pct, duree_portage_mois, appliquer_isoc, isoc_pct,
        decote_arv_pct=decote_arv_pct, frais_notaire_pct=frais_notaire_pct,
        frais_acte_eur=frais_acte_eur, tva_travaux_pct=tva_travaux_pct,
        charges_portage_mensuelles=charges_portage_mensuelles,
    )

    if mdb.empty:
        st.info("Aucun bien PEB " + "/".join(peb_cibles) + " (maison ou appartement) dans la sélection actuelle.")
    else:
        mdb["statut"] = mdb["marge_pct"].apply(lambda m: statut_mdb(m, seuil_go_fort, seuil_go, seuil_limite))
        mdb = mdb.sort_values("marge_pct", ascending=False)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("🔴 GO fort", int((mdb["statut"] == "🔴 GO fort").sum()))
        m2.metric("🟡 GO", int((mdb["statut"] == "🟡 GO").sum()))
        m3.metric("⚪ Limite", int((mdb["statut"] == "⚪ Limite").sum()))
        go_df = mdb[mdb["statut"].isin(["🔴 GO fort", "🟡 GO"])]
        m4.metric("Marge médiane (GO)", f"{go_df['marge_pct'].median():.0f} %" if not go_df.empty else "—")

        low_n = int((mdb["n_comparables"] < 5).sum())
        if low_n:
            st.caption(f"⚠️ {low_n} biens notés avec un comparable de secours au niveau région (moins de 5 annonces PEB A-B trouvées dans leur commune) — marge moins fiable pour ceux-là, colonne « Comparable ».")

        show = mdb[mdb["statut"] != "⬛ Écarté"].head(200)
        show = show.copy()
        # Drapeaux que le praticien veut voir avant de se déplacer.
        show["signal"] = (
            show.get("alerte_arv", False).fillna(False).map({True: "⚠️ ARV ", False: ""})
            + show.get("dans_zone_houillere", False).fillna(False).map({True: "⛏️ Minier", False: ""})
        ).str.strip()
        top_table = show[
            ["statut", "signal", "commune", "code_postal", "prix", "peb", "surface_habitable", "nb_chambres",
             "jours_sur_marche", "arv_brut", "arv_vs_marche_pct", "cout_travaux", "capital_engage",
             "marge_eur", "marge_pct", "roi_annualise_pct", "comparable_source", "n_comparables", "url_principale"]
        ].rename(columns={
            "statut": "Statut", "signal": "Signal", "commune": "Commune", "code_postal": "CP", "prix": "Prix (€)",
            "peb": "PEB", "surface_habitable": "Surface (m²)", "nb_chambres": "Ch.",
            "jours_sur_marche": "Jours en ligne", "arv_brut": "ARV (€)",
            "arv_vs_marche_pct": "ARV vs marché acté", "cout_travaux": "Travaux TVAC (€)",
            "capital_engage": "Capital engagé (€)", "marge_eur": "Marge (€)",
            "marge_pct": "Marge %", "roi_annualise_pct": "ROI annualisé %",
            "comparable_source": "Comparable", "n_comparables": "N comp.",
            "url_principale": "Annonce",
        })
        st.dataframe(
            top_table,
            use_container_width=True,
            height=420,
            hide_index=True,
            column_config={
                "Annonce": st.column_config.LinkColumn("Annonce", display_text="Voir ↗"),
                "Marge %": st.column_config.NumberColumn("Marge %", format="%.0f %%"),
                "ROI annualisé %": st.column_config.NumberColumn(
                    "ROI annualisé %", format="%.0f %%",
                    help="Marge / capital réellement engagé, ramenée à l'année. La rotation du capital est le nerf du métier : 12% en 6 mois bat 20% en 18 mois."),
                "Marge (€)": st.column_config.NumberColumn("Marge (€)", format="%d €"),
                "Capital engagé (€)": st.column_config.NumberColumn("Capital engagé (€)", format="%d €"),
                "Prix (€)": st.column_config.NumberColumn("Prix (€)", format="%d €"),
                "ARV (€)": st.column_config.NumberColumn("ARV (€)", format="%d €"),
                "ARV vs marché acté": st.column_config.NumberColumn(
                    "ARV vs marché acté", format="%+.0f %%",
                    help="Écart entre l'ARV retenu et le prix médian réellement acté (Statbel) de la commune. Au-delà de +40%, l'hypothèse de revente est agressive — drapeau ⚠️ ARV."),
                "Travaux TVAC (€)": st.column_config.NumberColumn("Travaux TVAC (€)", format="%d €"),
            },
        )
        st.caption(f"{len(mdb) - len(show)} biens supplémentaires écartés ou hors du top 200 (ajustez les filtres pour affiner).")

st.markdown("---")

# ---------------------------------------------------------------- score colocation

if transaction == "location":
    st.subheader("🎓 Score colocation")
    st.caption(
        "Formule reprise du vault Moubri (`pipeline/Score-colocation-formula.md`, v1.0) : "
        "25% distance université + 20% distance pôle d'emploi + 15% transport (proxy : distance gare la plus proche) "
        "+ 15% densité de population (commune) + 15% nombre de chambres + 10% loyer médian du secteur, sur 10. "
        "Bonus/malus : +2 si université ≤15km, +1 si densité >5000 hab/km², −1 si rural ET emploi >40km. "
        "**Le veto \"électricité non conforme\" du document original n'est pas calculable depuis les annonces — à vérifier impérativement en visite, jamais automatique.** "
        "Le loyer médian de secteur (`communes_loyers_marche`) a une fraîcheur variable selon la commune (2023 à 2026) — voir colonne « Année réf. »."
    )
    with st.spinner("Calcul du score colocation..."):
        coloc = compute_colocation_scores(f)
    if coloc.empty:
        st.info("Aucune annonce de location (maison/appartement) géolocalisée dans la sélection actuelle.")
    else:
        coloc = coloc.sort_values("score_coloc", ascending=False)
        cc1, cc2, cc3 = st.columns(3)
        cc1.metric("🟢 Excellente", int((coloc["verdict_coloc"] == "🟢 Excellente").sum()))
        cc2.metric("🟡 Viable", int((coloc["verdict_coloc"] == "🟡 Viable").sum()))
        cc3.metric("Score médian", f"{coloc['score_coloc'].median():.1f}/10")

        coloc_table = coloc.head(200)[[
            "verdict_coloc", "commune", "code_postal", "prix", "nb_chambres",
            "dist_universite_km", "dist_emploi_km", "distance_gare_km", "loyer_median", "annee_loyer",
            "score_coloc", "url_principale",
        ]].rename(columns={
            "verdict_coloc": "Verdict", "commune": "Commune", "code_postal": "CP", "prix": "Loyer (€)",
            "nb_chambres": "Ch.", "dist_universite_km": "Dist. univ. (km)", "dist_emploi_km": "Dist. emploi (km)",
            "distance_gare_km": "Dist. gare (km)", "loyer_median": "Loyer médian secteur (€)",
            "annee_loyer": "Année réf.", "score_coloc": "Score /10", "url_principale": "Annonce",
        })
        st.dataframe(
            coloc_table, use_container_width=True, height=420, hide_index=True,
            column_config={
                "Annonce": st.column_config.LinkColumn("Annonce", display_text="Voir ↗"),
                "Score /10": st.column_config.NumberColumn(format="%.1f"),
                "Dist. univ. (km)": st.column_config.NumberColumn(format="%.0f"),
                "Dist. emploi (km)": st.column_config.NumberColumn(format="%.0f"),
                "Dist. gare (km)": st.column_config.NumberColumn(format="%.0f"),
                "Loyer médian secteur (€)": st.column_config.NumberColumn(format="%d €"),
                "Loyer (€)": st.column_config.NumberColumn(format="%d €"),
            },
        )
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
st.caption("« vs marché » = écart du prix/m² par rapport à la médiane de la commune (tous types/PEB confondus). Négatif = sous le marché local.")
table_df = f.sort_values("jours_sur_marche", na_position="last")[
    ["commune", "code_postal", "type_bien", "prix", "surface_habitable", "nb_chambres",
     "annee_construction", "peb", "jours_sur_marche", "ecart_vs_marche_pct", "url_principale"]
].rename(columns={
    "code_postal": "CP", "type_bien": "Type", "prix": "Prix (€)", "surface_habitable": "Surface (m²)",
    "nb_chambres": "Chambres", "annee_construction": "Année", "peb": "PEB",
    "jours_sur_marche": "Jours en ligne", "ecart_vs_marche_pct": "vs marché (%)",
    "url_principale": "Annonce", "commune": "Commune",
})
st.dataframe(
    table_df,
    use_container_width=True,
    height=420,
    column_config={
        "Annonce": st.column_config.LinkColumn("Annonce", display_text="Voir ↗"),
        "vs marché (%)": st.column_config.NumberColumn("vs marché (%)", format="%+.0f %%"),
    },
    hide_index=True,
)
