"""Opportunités de l'agent sourcing : ce qu'il a trouvé, tes décisions, ses propositions, tes deals.

Lecture : vue v_sourcing_opportunites et tables sourcing_* (clé anon, lecture seule).
Écriture : uniquement par trois fonctions RPC protégées par un jeton (SOURCING_JETON dans st.secrets
ou moubri/.env) — décision sur un bien, décision sur une proposition, saisie d'un deal réel.
"""
import json
from datetime import date

import pandas as pd
import requests
import streamlit as st

from lib import get_credentials, load_env

st.set_page_config(page_title="Opportunités", page_icon="🎯", layout="wide")

LIBELLES = {"flip": "Flip", "decoupe": "Découpe", "location": "Location", "sous_marche": "Sous le marché",
            "baisse": "Baisse de prix", "vente_notariale": "Vente notariale", "terrain": "Terrain"}
NIVEAUX = {"go": "🟢 Feu vert", "a_etudier": "🟡 À étudier", "a_verifier": "⚪ À vérifier"}
DECISIONS = {"interesse": "👍 Intéressé", "visite": "🏠 Visité", "offre": "✍️ Offre faite", "achete": "🔑 Acheté",
             "ecarte": "👎 Écarté"}
RAISONS_ECART = ["", "Trop de travaux", "Quartier / rue", "Prix trop haut", "Rue passante / nationale",
                 "Bien atypique", "Déjà vu / connu", "Erreur de données", "Autre (préciser)"]

URL, CLE = get_credentials()
ENTETES = {"apikey": CLE, "Authorization": f"Bearer {CLE}"}


def jeton():
    try:
        if "SOURCING_JETON" in st.secrets:
            return st.secrets["SOURCING_JETON"]
    except Exception:
        pass
    return load_env().get("SOURCING_JETON")


@st.cache_data(ttl=300, show_spinner=False)
def lire(table, select="*", **filtres):
    lignes, debut = [], 0
    while True:
        r = requests.get(f"{URL}/rest/v1/{table}", headers={**ENTETES, "Range": f"{debut}-{debut + 999}"},
                         params={"select": select, **filtres}, timeout=30)
        r.raise_for_status()
        page = r.json()
        lignes += page
        if len(page) < 1000:
            return pd.DataFrame(lignes)
        debut += 1000


def rpc(nom, **params):
    j = jeton()
    if not j:
        st.error("Jeton d'écriture absent (SOURCING_JETON) : la décision n'a pas été enregistrée.")
        return False
    r = requests.post(f"{URL}/rest/v1/rpc/{nom}", headers={**ENTETES, "Content-Type": "application/json"},
                      data=json.dumps({"p_jeton": j, **params}, default=str), timeout=30)
    if r.status_code >= 300:
        st.error(f"Échec de l'enregistrement : {r.text[:200]}")
        return False
    lire.clear()
    return True


def eur(x):
    return f"{x:,.0f} €".replace(",", " ") if pd.notna(x) else "—"


st.title("🎯 Opportunités de l'agent sourcing")
onglets = st.tabs(["Opportunités", "Propositions de l'agent", "Mes deals", "Suivi de l'agent"])

# =========================================================================== opportunités
with onglets[0]:
    df = lire("v_sourcing_opportunites", order="score.desc")
    if df.empty:
        st.info("L'agent n'a encore rien détecté.")
    else:
        focus = st.query_params.get("annonce")
        c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
        strategies = c1.multiselect("Stratégie", list(LIBELLES), format_func=LIBELLES.get)
        niveaux = c2.multiselect("Niveau", list(NIVEAUX), default=["go", "a_etudier"], format_func=NIVEAUX.get)
        statut = c3.selectbox("Statut", ["active", "exclue", "disparue"],
                              format_func={"active": "En cours", "exclue": "Écartées par l'agent",
                                           "disparue": "Sorties du marché"}.get)
        sans_decision = c4.checkbox("Seulement sans décision de ma part", value=True)

        vue = df[df["statut"] == statut]
        if strategies:
            vue = vue[vue["strategie"].isin(strategies)]
        if niveaux and statut == "active":
            vue = vue[vue["niveau"].isin(niveaux)]
        if sans_decision:
            vue = vue[vue["derniere_decision"].isna()]
        if focus:
            vue = df[df["annonce_id"] == focus]
            st.info("Affichage du bien ouvert depuis le brief. Retire « ?annonce=… » de l'adresse pour tout revoir.")

        m = df[df["statut"] == "active"]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("🟢 Feux verts", int((m["niveau"] == "go").sum()))
        k2.metric("🟡 À étudier", int((m["niveau"] == "a_etudier").sum()))
        k3.metric("⚪ À vérifier", int((m["niveau"] == "a_verifier").sum()))
        k4.metric("Écartées par l'agent", int((df["statut"] == "exclue").sum()))

        tableau = vue.assign(
            Niveau=vue["niveau"].map(NIVEAUX), Stratégie=vue["strategie"].map(LIBELLES),
            Lieu=vue["code_postal"].fillna("") + " " + vue["commune"].fillna(""),
            Alertes=[" · ".join([x["texte"] for x in (r or []) if x.get("gravite") in ("forte", "moyenne")]
                                + [x["texte"] for x in (p or [])]) for r, p in zip(vue["risques"], vue["pieges"])],
            Lu=vue["lue"].map({True: "oui", False: "pas encore"}),
        )
        colonnes = ["Niveau", "Stratégie", "Lieu", "prix", "prix_max", "marge_pct", "rendement_brut", "Alertes", "Lu",
                    "motif_exclusion", "url_principale"]
        st.dataframe(
            tableau[colonnes], hide_index=True, use_container_width=True, height=420,
            column_config={
                "prix": st.column_config.NumberColumn("Prix", format="%d €"),
                "prix_max": st.column_config.NumberColumn("Prix max", format="%d €",
                                                          help="Prix d'achat qui garde le seuil de marge (params_utilisateur)."),
                "marge_pct": st.column_config.NumberColumn("Marge", format="percent"),
                "rendement_brut": st.column_config.NumberColumn("Rendement brut", format="percent"),
                "motif_exclusion": "Motif",
                "url_principale": st.column_config.LinkColumn("Annonce", display_text="Voir ↗"),
            })
        st.caption(f"{len(vue)} biens. Un tri pour prioriser les visites, jamais une offre.")

        if not vue.empty:
            st.subheader("Détail et décision")
            choix = st.selectbox(
                "Bien", vue.index,
                format_func=lambda i: f"{LIBELLES[vue.at[i, 'strategie']]} — {vue.at[i, 'code_postal']} "
                                      f"{vue.at[i, 'commune']} — {eur(vue.at[i, 'prix'])}")
            o = vue.loc[choix]
            g, d = st.columns([3, 2])
            with g:
                st.markdown(f"**{NIVEAUX.get(o['niveau'], '')} · {LIBELLES[o['strategie']]}** — "
                            f"[annonce]({o['url_principale']})")
                st.write(o["resume"])
                for x in (o["risques"] or []):
                    st.warning(f"Risque : {x['texte']}")
                for x in (o["pieges"] or []):
                    (st.error if x.get("bloquant") else st.warning)(f"{x['texte']}" + (
                        f" — « {x['extrait'][:160]} »" if x.get("extrait") else ""))
                with st.expander("Calcul détaillé"):
                    st.json(o["details"])
            with d:
                if o["derniere_decision"]:
                    st.info(f"Ta dernière décision : {DECISIONS.get(o['derniere_decision'])}")
                with st.form(f"decision_{o['id']}"):
                    dec = st.radio("Ta décision", list(DECISIONS), format_func=DECISIONS.get, horizontal=True)
                    raison = st.selectbox("Raison (surtout si écarté)", RAISONS_ECART)
                    commentaire = st.text_area("Commentaire", placeholder="Ce qui t'a décidé, ce que l'agent a raté…")
                    if st.form_submit_button("Enregistrer"):
                        if dec == "ecarte" and not raison and not commentaire:
                            st.warning("Donne une raison : c'est elle qui permet à l'agent d'apprendre.")
                        elif rpc("sourcing_enregistrer_decision", p_annonce=o["annonce_id"], p_decision=dec,
                                 p_raison=raison or None, p_commentaire=commentaire or None):
                            st.success("Enregistré ✓")

# =========================================================================== propositions
with onglets[1]:
    props = lire("sourcing_propositions", order="cree_le.desc")
    if props.empty:
        st.info("Pas encore de proposition : la revue de l'agent a lieu le dimanche matin.")
    else:
        attente = props[props["statut"] == "en_attente"]
        st.caption("Une proposition acceptée est d'abord testée sur le jeu de référence (biens qu'on sait bons ou "
                   "mauvais) ; elle n'est activée que si elle ne fait rater aucun bon bien.")
        for _, p in attente.iterrows():
            with st.container(border=True):
                st.markdown(f"**{p['titre']}** · semaine du {p['semaine']}")
                st.write(p["constat"])
                st.markdown(f"*Proposition :* {p['proposition']}")
                if p.get("impact") and isinstance(p["impact"], dict) and p["impact"].get("attendu"):
                    st.markdown(f"*Impact attendu :* {p['impact']['attendu']}")
                if p["regle"]:
                    with st.expander("Règle exacte"):
                        st.json(p["regle"])
                note = st.text_input("Commentaire (facultatif)", key=f"c{p['id']}")
                a, r = st.columns(2)
                if a.button("✅ Accepter", key=f"a{p['id']}"):
                    if rpc("sourcing_decider_proposition", p_id=int(p["id"]), p_statut="acceptee", p_commentaire=note or None):
                        st.success("Acceptée : elle sera testée puis activée à la prochaine détection.")
                if r.button("❌ Refuser", key=f"r{p['id']}"):
                    if rpc("sourcing_decider_proposition", p_id=int(p["id"]), p_statut="refusee", p_commentaire=note or None):
                        st.success("Refusée.")
        autres = props[props["statut"] != "en_attente"]
        if not autres.empty:
            st.subheader("Historique")
            st.dataframe(autres[["semaine", "titre", "statut", "commentaire", "decide_le"]], hide_index=True,
                         use_container_width=True)

# =========================================================================== deals
with onglets[2]:
    st.write("Tes opérations réelles. C'est la donnée la plus précieuse : l'agent compare ses estimations "
             "(revente, travaux) à la réalité et recale ses hypothèses.")
    with st.form("deal"):
        a, b, c = st.columns(3)
        ref = a.text_input("Référence interne", placeholder="Fichaux 6")
        type_bien = b.selectbox("Type", ["maison", "appartement", "immeuble", "terrain"])
        surface = c.number_input("Surface habitable (m²)", min_value=0.0, step=1.0)
        a, b, c = st.columns(3)
        peb_av = a.selectbox("PEB avant", ["", "A", "B", "C", "D", "E", "F", "G"])
        peb_ap = b.selectbox("PEB après", ["", "A", "B", "C", "D", "E", "F", "G"])
        code_ins = c.text_input("Code INS de la commune (facultatif)")
        a, b, c, d = st.columns(4)
        prix_achat = a.number_input("Prix d'achat (€)", min_value=0.0, step=1000.0)
        droits = b.number_input("Droits payés (€)", min_value=0.0, step=100.0)
        notaire = c.number_input("Frais notaire achat (€)", min_value=0.0, step=100.0)
        divers = d.number_input("Frais divers (€)", min_value=0.0, step=100.0)
        a, b, c, d = st.columns(4)
        travaux = a.number_input("Travaux réels TVAC (€)", min_value=0.0, step=1000.0)
        travaux_est = b.number_input("Travaux estimés à l'achat (€)", min_value=0.0, step=1000.0)
        arv_est = c.number_input("Revente estimée à l'achat (€)", min_value=0.0, step=1000.0)
        prix_vente = d.number_input("Prix de revente réel (€)", min_value=0.0, step=1000.0)
        a, b, c = st.columns(3)
        d_achat = a.date_input("Date de l'acte d'achat", value=None)
        d_vente = b.date_input("Date de l'acte de vente", value=None)
        notes = c.text_area("Notes")
        if st.form_submit_button("Enregistrer le deal"):
            data = dict(reference_interne=ref, type_bien=type_bien, surface_m2=surface or None,
                        peb_avant_travaux=peb_av or None, peb_apres_travaux=peb_ap or None, code_ins=code_ins or None,
                        prix_achat_reel=prix_achat or None, droits_enregistrement_reel=droits or None,
                        frais_notaire_achat_reel=notaire or None, frais_divers_reels=divers or None,
                        cout_travaux_reel=travaux or None, cout_travaux_estime=travaux_est or None,
                        arv_estime_a_lachat=arv_est or None, prix_vente_reel=prix_vente or None,
                        date_acte_achat=d_achat, date_acte_vente=d_vente, notes=notes or None)
            if not ref:
                st.warning("Donne au moins une référence.")
            elif rpc("sourcing_saisir_transaction", p_data=data):
                st.success("Deal enregistré ✓")

# =========================================================================== suivi
with onglets[3]:
    ex = lire("sourcing_executions", order="debut.desc", limit="30")
    dec = lire("sourcing_decisions", select="decision,created_at")
    if not dec.empty:
        recentes = dec[pd.to_datetime(dec["created_at"]) >= pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=28)]
        utiles = recentes["decision"].isin(["interesse", "visite", "offre", "achete"]).sum()
        ecartes = (recentes["decision"] == "ecarte").sum()
        st.metric("Part des biens jugés intéressants (4 semaines)",
                  f"{utiles / (utiles + ecartes):.0%}" if utiles + ecartes else "—",
                  help="La mesure de progrès de l'agent : elle doit monter au fil des semaines.")
    det = ex[ex["type"] == "detection"] if not ex.empty else ex
    if not det.empty:
        t = det.iloc[0]["test_reference"] or {}
        st.subheader("Jeu de référence (dernière détection)")
        st.write(f"{len(t.get('ok', []))} conformes · {len(t.get('echecs', []))} écarts · "
                 f"{len(t.get('hors_marche', []))} plus en vente")
        if t.get("echecs"):
            st.dataframe(pd.DataFrame(t["echecs"]), hide_index=True, use_container_width=True)
        st.subheader("Dernières exécutions")
        st.dataframe(ex[["type", "debut", "fin", "stats"]], hide_index=True, use_container_width=True)
    regles = lire("sourcing_regles", order="cree_le.desc")
    if not regles.empty:
        st.subheader("Règles actives")
        st.dataframe(regles[["code", "description", "type", "actif", "origine", "active_le"]], hide_index=True,
                     use_container_width=True)
