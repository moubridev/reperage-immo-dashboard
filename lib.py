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
