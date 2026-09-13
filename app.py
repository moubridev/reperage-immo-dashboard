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


def compute_mdb_scores(
    df_scope, df_all, peb_cibles, taux_enregistrement_pct, cout_travaux_m2,
    frais_vente_pct, taux_financier_annuel_pct, duree_portage_mois, appliquer_isoc, isoc_pct,
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
    comp_base = df_all[df_all["peb"].isin(PEB_RENOVES)]
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
        df_scope["type_bien"].isin(["maison", "appartement"])
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

    d["arv_brut"] = comp_m2 * d["surface_habitable"] * 1.05
    d["cout_travaux"] = d["surface_habitable"] * cout_travaux_m2
    d["cout_enregistrement"] = d["prix"] * e
    # Portage : capital immobilisé = prix payé dès le jour 1 + travaux tirés
    # progressivement (approximé à la moitié du budget travaux, comme dans
    # charge_fonciere.py : "0,5·Cc·f").
    d["cout_financier"] = (d["prix"] + 0.5 * d["cout_travaux"]) * f
    d["cout_vente"] = d["arv_brut"] * (frais_vente_pct / 100)

    d["marge_eur"] = (
        d["arv_brut"] - d["prix"] - d["cout_enregistrement"] - d["cout_travaux"]
        - d["cout_financier"] - d["cout_vente"]
    )
    if appliquer_isoc:
        d["marge_eur"] = d["marge_eur"].clip(lower=0) * (1 - isoc_pct / 100) + d["marge_eur"].clip(upper=0)
    d["marge_pct"] = (d["marge_eur"] / d["arv_brut"] * 100)

    d = d[d["arv_brut"].notna() & d["marge_pct"].notna()]
    return d


def statut_mdb(marge_pct, seuil_go_fort, seuil_go, seuil_limite):
    if marge_pct >= seuil_go_fort:
        return "🔴 GO fort"
    if marge_pct >= seuil_go:
        return "🟡 GO"
    if marge_pct >= seuil_limite:
        return "⚪ Limite"
    return "⬛ Écarté"


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
    cout_travaux_m2 = st.number_input("Coût travaux (€/m²)", min_value=0, max_value=3000, value=defaults.get("cout_travaux_m2", 850), step=50, help="Forfait unique quel que soit l'écart PEB — à affiner par palier si besoin.")
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

st.subheader("🎯 Meilleures opportunités — achat dégradé → rénovation → revente")
st.caption(
    "Marge = ARV brut − prix − enregistrement − travaux − portage/financement − frais de revente"
    + (" − ISOC" if appliquer_isoc else "") + ". "
    "ARV comparé sur des **annonces** PEB A-B du secteur (prix demandés, pas des ventes réelles — "
    "sous-estime probablement la fiabilité). **Un tri pour prioriser les visites, jamais une offre.**"
)

mdb = compute_mdb_scores(
    f, df, peb_cibles, taux_enregistrement_pct, cout_travaux_m2,
    frais_vente_pct, taux_financier_annuel_pct, duree_portage_mois, appliquer_isoc, isoc_pct,
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
    top_table = show[
        ["statut", "commune", "code_postal", "prix", "peb", "surface_habitable", "nb_chambres",
         "jours_sur_marche", "arv_brut", "cout_travaux", "cout_financier", "cout_vente",
         "marge_pct", "comparable_source", "n_comparables", "url_principale"]
    ].rename(columns={
        "statut": "Statut", "commune": "Commune", "code_postal": "CP", "prix": "Prix (€)",
        "peb": "PEB", "surface_habitable": "Surface (m²)", "nb_chambres": "Ch.",
        "jours_sur_marche": "Jours en ligne", "arv_brut": "ARV brut (€)",
        "cout_travaux": "Travaux (€)", "cout_financier": "Portage (€)", "cout_vente": "Frais revente (€)",
        "marge_pct": "Marge %", "comparable_source": "Comparable", "n_comparables": "N comp.",
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
            "Prix (€)": st.column_config.NumberColumn("Prix (€)", format="%d €"),
            "ARV brut (€)": st.column_config.NumberColumn("ARV brut (€)", format="%d €"),
            "Travaux (€)": st.column_config.NumberColumn("Travaux (€)", format="%d €"),
            "Portage (€)": st.column_config.NumberColumn("Portage (€)", format="%d €"),
            "Frais revente (€)": st.column_config.NumberColumn("Frais revente (€)", format="%d €"),
        },
    )
    st.caption(f"{len(mdb) - len(show)} biens supplémentaires écartés ou hors du top 200 (ajustez les filtres pour affiner).")

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
