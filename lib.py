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


def get_indexation_revenu(annee_reference, ttl_cache=None):
    """Coefficient d'indexation du revenu depuis `annee_reference` (moyenne annuelle)
    jusqu'au dernier point disponible, via l'IPCH Belgique (Eurostat, proxy officiel de
    l'indexation salariale belge — l'indice sante legal exclut alcool/tabac/carburants
    et differe legerement de l'IPCH, mais suit la meme tendance de tres pres).
    Retourne (facteur, annee_reference, periode_la_plus_recente) ou (1.0, None, None) si echec —
    ne JAMAIS bloquer la simulation si Eurostat est indisponible, juste ne pas indexer."""
    import requests as _requests
    try:
        r = _requests.get(
            "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_midx",
            params={"format": "JSON", "geo": "BE", "unit": "I15", "coicop": "CP00",
                    "sinceTimePeriod": f"{annee_reference}-01"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        periods = data["dimension"]["time"]["category"]["index"]
        values = data["value"]
        inv = {v: k for k, v in periods.items()}
        all_vals = {inv[int(p)]: v for p, v in values.items()}
        ref_vals = [v for k, v in all_vals.items() if k.startswith(str(annee_reference))]
        if not ref_vals:
            return 1.0, None, None
        ref_avg = sum(ref_vals) / len(ref_vals)
        derniere_periode = sorted(all_vals.keys())[-1]
        facteur = all_vals[derniere_periode] / ref_avg
        return facteur, annee_reference, derniere_periode
    except Exception:
        return 1.0, None, None


def get_with_retry(session_get, url, headers, params, timeout=60, tries=3):
    """GET avec retry (backoff court) sur 500/503 — v_dashboard_communes a un vrai probleme
    de timeout intermittent cote Postgres (trouve le 2026-09-15, partiellement corrige par un
    index, pas totalement elimine). Ne masque pas l'erreur si elle persiste apres `tries` essais."""
    import time as _time
    last_exc = None
    for attempt in range(tries):
        try:
            r = session_get(url, headers=headers, params=params, timeout=timeout)
            if r.status_code in (500, 503) and attempt < tries - 1:
                _time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            last_exc = e
            if attempt < tries - 1:
                _time.sleep(1.5 * (attempt + 1))
    raise last_exc


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
