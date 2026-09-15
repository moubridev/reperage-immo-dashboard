"""Fonctions partagées entre les pages du dashboard Repérage Immo."""

from pathlib import Path

import streamlit as st

ENV_PATH = Path("/home/antoine/moubri/.env")


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
    Local: fallback sur moubri/.env — lit SUPABASE_ANON_KEY en priorité,
    jamais la service_role, pour que local et déployé restent identiques."""
    try:
        has_secrets = "SUPABASE_URL" in st.secrets and "SUPABASE_ANON_KEY" in st.secrets
    except Exception:
        has_secrets = False
    if has_secrets:
        return st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_ANON_KEY"]
    env = load_env()
    url = env.get("SUPABASE_URL")
    key = env.get("SUPABASE_ANON_KEY") or env.get("SUPABASE_KEY")
    return url, key


def annuite_facteur(taux_annuel_pct, annees):
    """Mensualite par euro emprunte, formule d'annuite standard.
    taux_annuel_pct en % (ex. 3.64), annees = duree du pret.
    Verifie a l'exact contre le facteur reverse-engineere le 13/09/2026
    (0,0058925 pour ~3,67%/20 ans) — meme formule, generalisee a tout taux/duree."""
    r_mensuel = (taux_annuel_pct / 100) / 12
    n_mois = annees * 12
    if r_mensuel == 0:
        return 1 / n_mois
    return r_mensuel / (1 - (1 + r_mensuel) ** -n_mois)


def capacite_emprunt(revenu_annuel_menage, taux_annuel_pct, annees, taux_effort=0.33):
    """Capacite d'emprunt max pour un revenu menage donne (33% du revenu mensuel, meme regle que le reste du dashboard)."""
    mensualite_max = (revenu_annuel_menage / 12) * taux_effort
    return mensualite_max / annuite_facteur(taux_annuel_pct, annees)


def revenu_requis_pour_prix(prix, taux_annuel_pct, annees, taux_effort=0.33):
    """Revenu annuel menage necessaire pour financer `prix` a ce taux/duree, meme regle d'effort 33%."""
    mensualite = prix * annuite_facteur(taux_annuel_pct, annees)
    return (mensualite / taux_effort) * 12


def pct_menages_au_dessus(seuil_revenu, mu, sigma):
    """% de menages dont le revenu (modele log-normal, parametres mu/sigma ajustes sur
    median/Q1/Q3 Statbel reels) depasse `seuil_revenu`. ESTIMATION statistique, pas une
    distribution observee directement — voir commentaire SQL sur communes_capacite_emprunt.lognormal_mu."""
    import math
    if mu is None or sigma is None or sigma <= 0 or seuil_revenu <= 0:
        return None
    z = (math.log(seuil_revenu) - mu) / sigma
    phi = 0.5 * (1 + math.erf(-z / math.sqrt(2)))
    return max(0.0, min(1.0, phi)) * 100


def get_iso_proxy_credentials():
    """Proxy isochrones (Valhalla + géocodage) sur acolys-serveur.
    Streamlit Cloud: st.secrets. Local: fallback moubri/.env."""
    try:
        has_secrets = "ISO_PROXY_URL" in st.secrets and "ISO_PROXY_KEY" in st.secrets
    except Exception:
        has_secrets = False
    if has_secrets:
        return st.secrets["ISO_PROXY_URL"], st.secrets["ISO_PROXY_KEY"]
    env = load_env()
    return env.get("ISO_PROXY_URL"), env.get("ISO_PROXY_KEY")
