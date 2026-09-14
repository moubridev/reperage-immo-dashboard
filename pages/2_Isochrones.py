"""
Isochrones — zone accessible en voiture depuis une adresse précise.

Complète la page Communes (qui travaille au niveau centroïde communal) avec
un calcul à l'adresse exacte. Appelle un proxy déployé sur acolys-serveur
(clé + rate limit) qui géocode l'adresse (Nominatim) puis interroge Valhalla
(moteur de routage OSM) pour le temps de trajet réel, pas à vol d'oiseau.

Voir PLAN_INTELLIGENCE_TERRITORIALE.md (vault Moubri) pour le détail de
l'infra (pourquoi un proxy plutôt qu'exposer Valhalla directement, etc.).
"""

import folium
import requests
import streamlit as st
from streamlit_folium import st_folium

from lib import get_iso_proxy_credentials

st.set_page_config(page_title="Isochrones — Repérage Immo", page_icon="🚗", layout="wide")

st.title("🚗 Isochrones — zone accessible depuis une adresse")
st.caption(
    "Temps de trajet voiture réel (routage OSM via Valhalla), pas une distance à vol d'oiseau. "
    "Calcul à la demande — contrairement aux temps vers les 5 pôles sur la page Communes, qui sont précalculés."
)

proxy_url, proxy_key = get_iso_proxy_credentials()
if not proxy_url or not proxy_key:
    st.error("Identifiants du proxy isochrones introuvables (st.secrets ISO_PROXY_URL/ISO_PROXY_KEY ou moubri/.env).")
    st.stop()

with st.form("iso_form"):
    c1, c2 = st.columns([3, 1])
    address = c1.text_input("Adresse (Belgique)", placeholder="ex : 64 Rue Docteur René Bureau, Écaussinnes")
    minutes = c2.selectbox("Temps de trajet", [5, 10, 15, 20, 30], index=0, format_func=lambda m: f"{m} min")
    submitted = st.form_submit_button("Calculer l'isochrone")

if submitted:
    if not address.strip():
        st.warning("Entre une adresse.")
        st.stop()
    with st.spinner("Géocodage + calcul de l'isochrone..."):
        try:
            r = requests.get(
                f"{proxy_url.rstrip('/')}/isochrone",
                params={"address": address, "minutes": minutes},
                headers={"X-API-Key": proxy_key},
                timeout=25,
            )
        except requests.exceptions.RequestException as e:
            st.error(f"Le service isochrones n'a pas répondu : {e}")
            st.stop()

    if r.status_code == 404:
        st.warning("Adresse introuvable (géocodage OpenStreetMap). Vérifie l'orthographe ou ajoute la commune.")
        st.stop()
    if r.status_code == 429:
        st.warning("Trop de requêtes en peu de temps — réessaie dans une minute.")
        st.stop()
    if r.status_code != 200:
        st.error(f"Erreur du service isochrones (HTTP {r.status_code}).")
        st.stop()

    data = r.json()
    lat, lon = data["lat"], data["lon"]
    feature = data["isochrone"]["features"][0]
    coords = feature["geometry"]["coordinates"][0]
    polygon_latlon = [[c[1], c[0]] for c in coords]

    st.success(f"📍 {data['address_resolved']}")

    m = folium.Map(location=[lat, lon], zoom_start=12, tiles="OpenStreetMap")
    folium.Marker([lat, lon], tooltip="Adresse", icon=folium.Icon(color="red")).add_to(m)
    folium.Polygon(
        locations=polygon_latlon,
        color="#2E86AB",
        weight=2,
        fill=True,
        fill_color="#2E86AB",
        fill_opacity=0.25,
        tooltip=f"{minutes} min en voiture",
    ).add_to(m)
    st_folium(m, use_container_width=True, height=560, returned_objects=[])

    st.caption(
        "Zone atteignable en voiture depuis l'adresse dans le temps indiqué, conditions de circulation normales "
        "(pas de trafic temps réel). Source : Valhalla, tuiles OpenStreetMap Belgique."
    )
