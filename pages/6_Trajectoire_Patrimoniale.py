"""
Repérage Immo — Trajectoire patrimoniale : suis-je sur la trajectoire de mon
objectif de revenu, et avec quelle probabilité ?

Construite le 2026-09-16 (revue financière). Comble le manque n°5 identifié :
tous les outils existants raisonnent bien-par-bien, aucun ne répond à
« où en suis-je par rapport à mon objectif de revenu ? ».

Trois choses que cette page fait et que rien d'autre ne faisait :
1. Le CAPITAL REQUIS pour un objectif de revenu, net de fiscalité et de friction.
2. Le COÛT RÉEL d'un capital emprunté (OLO + marge) — et l'écart de patrimoine
   que cela crée par rapport au même plan financé en fonds propres.
3. Une PROBABILITÉ d'atteindre l'objectif, par simulation sous contrainte
   d'exécution (le facteur limitant est le nombre d'opérations réalisables,
   pas le capital disponible).

Toutes les sorties sont des ESTIMATIONS sous hypothèses affichées, jamais des
prévisions. Les paramètres de marché (OLO, taux hypothécaire, rendements) sont
lus en base, pas codés en dur.
"""

from datetime import datetime

import numpy as np
import pandas as pd
import requests
import streamlit as st

from lib import get_credentials, annuite_facteur, get_with_retry

st.set_page_config(page_title="Trajectoire patrimoniale — Repérage Immo",
                   page_icon="📈", layout="wide")

TAUX_VIEW = "v_dashboard_taux"
RENDEMENT_VIEW = "v_dashboard_communes"

# Friction d'acquisition en Wallonie pour un bien NON destiné à l'habitation
# propre et unique : 12,5 % de droits + ~1,6 % d'honoraires de notaire.
# Le taux réduit à 3 % (réforme 2025) ne s'applique PAS à un investissement locatif.
DROITS_ENREGISTREMENT = 12.5
FRAIS_NOTAIRE = 1.6
FRICTION_PCT = DROITS_ENREGISTREMENT + FRAIS_NOTAIRE


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
        r = get_with_retry(requests.get, f"{url}/rest/v1/{view_name}", headers, params, timeout=60)
        page = r.json()
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return pd.DataFrame(rows), datetime.now()


def eur(montant, suffixe=" €"):
    """Formate un montant avec espace comme séparateur de milliers.

    À utiliser nombre par nombre : appliquer `.replace(",", " ")` à une phrase entière
    supprimerait aussi les virgules du texte français.
    """
    return f"{montant:,.0f}".replace(",", " ") + suffixe


def taux_en_base(df_taux, type_taux, defaut):
    """Lit un taux de marché en base ; retombe sur une valeur par défaut si absent."""
    if df_taux.empty or "type_taux" not in df_taux:
        return defaut, None
    ligne = df_taux[df_taux["type_taux"] == type_taux]
    if ligne.empty:
        return defaut, None
    return float(ligne.iloc[0]["valeur"]), ligne.iloc[0].get("date")


st.title("📈 Trajectoire patrimoniale")
st.caption(
    "À partir d'un capital de départ et d'une stratégie, cette page calcule le capital "
    "nécessaire à un objectif de revenu, la trajectoire probable pour y arriver, et ce que "
    "coûte le fait d'emprunter ce capital plutôt que de le détenir."
)

taux_df, _ = fetch_view(TAUX_VIEW, select="type_taux,valeur,date,source")
olo_defaut, olo_date = taux_en_base(taux_df, "olo_10a", 3.76)
hypo_defaut, hypo_date = taux_en_base(taux_df, "hypothecaire_be_nouveau", 3.64)
bce_defaut, bce_date = taux_en_base(taux_df, "bce_facilite_depot", 2.50)

# ---------------------------------------------------------------- paramètres
with st.sidebar:
    st.header("Objectif")
    objectif_mensuel = st.number_input("Revenu net visé (€/mois)", 500, 20000, 3000, 250)
    horizon = st.slider("Horizon (années)", 5, 25, 10)
    tenir_compte_inflation = st.checkbox(
        "Raisonner à pouvoir d'achat constant", value=True,
        help="Coché : l'objectif est indexé sur l'inflation, donc il faut davantage de capital. "
             "Décoché : l'objectif est nominal (3 000 € de 2036, qui valent moins qu'aujourd'hui).")
    inflation = st.slider("Inflation supposée (%/an)", 0.0, 5.0, 2.0, 0.25,
                          disabled=not tenir_compte_inflation)

    st.header("Capital de départ")
    capital_0 = st.number_input("Montant (€)", 0, 5_000_000, 500_000, 25_000)
    origine = st.radio(
        "Origine du capital", ["Emprunté", "Fonds propres"], index=0,
        help="C'est le paramètre le plus lourd de cette page. Un capital emprunté doit être "
             "remboursé ET servir des intérêts pendant toute la durée du plan.")
    emprunte = origine == "Emprunté"

    type_taux_capital = st.radio(
        "Référence du taux", ["BCE + marge (votre crédit, variable)", "OLO + marge (variable)",
                              "Taux fixe (comparaison)"], index=0,
        disabled=not emprunte,
        help="Votre crédit réel suit la facilité de dépôt BCE + 1 point, et **varie** avec les "
             "décisions futures de la BCE — ce n'est pas un taux figé à la signature. "
             "L'option « Taux fixe » sert uniquement à mesurer, par comparaison, ce que "
             "coûterait l'absence de ce risque.")
    marge_credit = st.slider("Marge au-dessus de la référence (points)", 0.0, 3.0, 1.0, 0.05,
                             disabled=not emprunte or type_taux_capital == "Taux fixe (comparaison)")
    if type_taux_capital == "BCE + marge (votre crédit, variable)":
        bce = st.slider("Taux BCE — facilité de dépôt (%)", 0.0, 6.0, float(bce_defaut), 0.05,
                        disabled=not emprunte,
                        help=f"Valeur en base : {bce_defaut:.2f} %"
                             + (f" ({bce_date})" if bce_date else "")
                             + ". Relevé par la BCE le 10/09/2026, effectif au 16/09/2026.")
        taux_capital = bce + marge_credit
        risque_taux_capital = True
        reference_nom, reference_valeur = "BCE", bce
        olo = olo_defaut  # conservé pour l'affichage de sensibilité, sans effet sur taux_capital
    elif type_taux_capital == "OLO + marge (variable)":
        olo = st.slider("OLO 10 ans (%)", 0.0, 8.0, float(olo_defaut), 0.05,
                        disabled=not emprunte,
                        help=f"Valeur en base : {olo_defaut:.2f} %"
                             + (f" ({olo_date})" if olo_date else ""))
        taux_capital = olo + marge_credit
        risque_taux_capital = True
        reference_nom, reference_valeur = "OLO", olo
        bce = bce_defaut
    else:
        taux_capital = st.slider("Taux fixe supposé (%)", 0.0, 8.0, float(bce_defaut + 1.0), 0.05,
                                 disabled=not emprunte)
        risque_taux_capital = False
        reference_nom, reference_valeur = "fixe", taux_capital
        olo, bce = olo_defaut, bce_defaut

    st.header("Stratégie")
    strategie = st.radio("Moteur de constitution du capital",
                         ["Marchand de biens", "Locatif à conserver"])

    if strategie == "Marchand de biens":
        ops_an = st.slider("Opérations menées par an", 0.5, 6.0, 2.0, 0.5,
                           help="Le vrai facteur limitant. Une opération dure ~9 mois : avec N "
                                "opérations en parallèle on fait N × 1,33 opérations par an.")
        ticket = st.number_input("Capital engagé par opération (€)", 50_000, 1_500_000, 200_000, 25_000)
        marge_op = st.number_input("Marge nette par opération (€)", 0, 500_000, 36_000, 1_000,
                                   help="Après impôt. Médiane mesurée sur le gisement réel "
                                        "(tickets < 250 k€, ROI ≥ 10 %/an) : 36 000 €.")
        reussite = st.slider("Taux de réussite des opérations (%)", 30, 100, 85, 5)
        perte_echec = st.slider("Perte sur une opération ratée (% du ticket)", 0, 40, 12, 1)
    else:
        ltv = st.slider("LTV du crédit hypothécaire (%)", 0, 90, 70, 5)
        taux_hypo = st.slider("Taux hypothécaire (%)", 0.5, 8.0, float(hypo_defaut), 0.05,
                              help=f"Valeur en base : {hypo_defaut:.2f} %"
                                   + (f" ({hypo_date})" if hypo_date else ""))
        duree_hypo = st.select_slider("Durée du crédit (ans)", [10, 15, 20, 25, 30], value=20)
        rdt_brut = st.slider("Rendement locatif brut visé (%)", 2.0, 12.0, 6.45, 0.05,
                             help="Médiane des communes mesurables en 2026 : 5,07 %. "
                                  "Meilleur échantillon solide (Charleroi appt) : 6,45 %.")
        appreciation = st.slider("Appréciation des prix (%/an)", -2.0, 6.0, 2.0, 0.25)

    st.header("Hypothèses communes")
    ratio_net = st.slider("Part du loyer qui reste après charges (%)", 50, 95, 75, 1,
                          help="Précompte immobilier, assurance, entretien, gestion, vacance. "
                               "Calibration de l'outil : 75 %.")
    regime = st.radio("Régime fiscal des loyers", ["Personne physique", "Société (ISOC)"],
                      help="Personne physique : imposition sur le revenu cadastral indexé × 1,4 "
                           "(~12 % du loyer réel). Société : ISOC 25 % sur le résultat réel.")
    taux_impot = 12.0 if regime == "Personne physique" else 25.0

# ------------------------------------------------------------ calculs de base
net_charges_pct = rdt_brut * ratio_net / 100 if strategie == "Locatif à conserver" else 6.45 * ratio_net / 100
net_net_pct = net_charges_pct * (1 - taux_impot / 100)

objectif_annuel = objectif_mensuel * 12
if tenir_compte_inflation:
    objectif_annuel_cible = objectif_annuel * (1 + inflation / 100) ** horizon
else:
    objectif_annuel_cible = objectif_annuel

capital_requis = objectif_annuel_cible / (net_net_pct / 100)
capital_requis_brut = capital_requis * (1 + FRICTION_PCT / 100)

st.subheader("1 · Ce que l'objectif exige")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Objectif annuel", eur(objectif_annuel, " €"),
          help="Ce que vous avez demandé, en euros d'aujourd'hui.")
c2.metric(f"À atteindre en {datetime.now().year + horizon}",
          eur(objectif_annuel_cible, " €"),
          delta=f"{objectif_annuel_cible - objectif_annuel:+,.0f} € d'inflation".replace(",", " ")
          if tenir_compte_inflation else "nominal",
          delta_color="off",
          help="Les loyers belges s'indexant automatiquement, un patrimoine locatif suit "
               "l'inflation — contrairement à un placement à taux fixe.")
c3.metric("Rendement net-net retenu", f"{net_net_pct:.2f} %",
          help=f"Brut → net de charges ({ratio_net} %) → net d'impôt ({taux_impot:.0f} %).")
c4.metric("Capital à détenir sans dette", eur(capital_requis, " €"),
          help=f"Soit {eur(capital_requis_brut)} en incluant la friction d'acquisition "
               f"de {FRICTION_PCT:.1f} %.")

st.caption(
    f"Friction d'acquisition retenue : **{FRICTION_PCT:.1f} %** du prix "
    f"({DROITS_ENREGISTREMENT:.1f} % de droits d'enregistrement en Wallonie pour un bien "
    f"d'investissement — le taux réduit à 3 % ne concerne que l'habitation propre et unique — "
    f"plus {FRAIS_NOTAIRE:.1f} % d'honoraires de notaire et frais d'acte). "
    f"Elle est perdue le jour de l'acte : au rendement retenu, il faut "
    f"**{FRICTION_PCT / net_charges_pct:.1f} années de loyer net** pour la seule récupérer."
)

# --------------------------------------------------- 2. le coût du capital
st.subheader("2 · Ce que coûte votre capital")
if emprunte:
    interets_an = capital_0 * taux_capital / 100
    interets_total = interets_an * horizon
    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Taux du capital", f"{taux_capital:.2f} %",
              delta=f"{taux_capital - hypo_defaut:+.2f} pts vs crédit hypothécaire",
              delta_color="inverse",
              help=f"{reference_nom} {reference_valeur:.2f} % + marge {marge_credit:.2f} pt. "
                   f"Crédit hypothécaire de référence (BCE MIR) : {hypo_defaut:.2f} %."
                   if risque_taux_capital else
                   f"Taux fixe hypothétique, à titre de comparaison uniquement.")
    d2.metric("Intérêts annuels", eur(interets_an, " €"),
              help="En bullet (intérêts seuls). Amorti, la charge serait bien supérieure.")
    d3.metric(f"Coût total sur {horizon} ans", eur(interets_total, " €"))
    d4.metric("À rembourser en plus", eur(capital_0, " €"),
              help="Le capital emprunté doit être remboursé AVANT que le patrimoine "
                   "ne devienne réellement le vôtre.")

    if risque_taux_capital:
        st.warning(
            f"**Votre crédit est variable, indexé sur {reference_nom} + {marge_credit:.2f} pt.** "
            f"Il n'est PAS figé à la signature : chaque décision future de la BCE change ce coût "
            f"pendant toute la durée du plan. Voir la section 6 pour la sensibilité réelle.")
    else:
        st.caption(
            "Scénario de comparaison uniquement : votre crédit réel est variable "
            "(voir l'avertissement ci-dessus dans les autres options).")

    if taux_capital > hypo_defaut:
        st.warning(
            f"**Votre capital coûte {(taux_capital - hypo_defaut) * 100:.0f} points de base de plus "
            f"qu'un crédit hypothécaire ordinaire ({hypo_defaut:.2f} %).** Sur {horizon} ans et "
            f"{eur(capital_0)}, cet écart seul représente "
            f"{eur(capital_0 * (taux_capital - hypo_defaut) / 100 * horizon)}. "
            f"Chaque 0,25 point négocié vaut {eur(capital_0 * 0.0025 * horizon)}.")
    elif taux_capital <= hypo_defaut:
        st.info(
            f"**Ce capital est moins cher qu'un crédit hypothécaire ordinaire** "
            f"({hypo_defaut:.2f} %) : ce n'est plus la contrainte du plan. "
            f"Voir la section 4 pour identifier la vraie contrainte.")

    if strategie == "Locatif à conserver":
        st.error(
            f"**Fonds propres réels négatifs.** Un capital emprunté utilisé comme apport ne crée "
            f"aucun fonds propre : la friction de {FRICTION_PCT:.1f} % est perdue immédiatement, "
            f"donc vous démarrez à **−{FRICTION_PCT:.1f} % de fonds propres**. Une baisse de prix "
            f"de {FRICTION_PCT:.1f} % suffit à mettre la position en négatif net.")
else:
    st.success(
        f"**Capital détenu en fonds propres.** Aucun service de dette ne pèse sur la trajectoire — "
        f"c'est l'hypothèse la plus favorable, et l'écart avec un capital emprunté "
        f"est mesuré au point 4.")
    taux_capital = 0.0

# ------------------------------------------------- 3. portage / DSCR locatif
if strategie == "Locatif à conserver":
    st.subheader("3 · Le bien se paie-t-il tout seul ?")
    annuite_pct = annuite_facteur(taux_hypo, duree_hypo) * 12 * 100
    portage = net_charges_pct - annuite_pct * ltv / 100
    dscr = (net_charges_pct / (annuite_pct * ltv / 100)) if ltv > 0 else float("inf")
    e1, e2, e3 = st.columns(3)
    e1.metric("Loyer net de charges", f"{net_charges_pct:.2f} % /an",
              help="Avant impôt et avant service de la dette.")
    e2.metric("Service de la dette", f"{annuite_pct * ltv / 100:.2f} % /an",
              help=f"{annuite_pct:.2f} % du montant emprunté, à {ltv} % de LTV.")
    e3.metric("DSCR", f"{dscr:.2f}" if np.isfinite(dscr) else "∞",
              delta="bancable" if dscr >= 1.2 else "sous le seuil bancaire",
              delta_color="normal" if dscr >= 1.2 else "inverse",
              help="Revenu net / service de la dette. Une banque exige typiquement ≥ 1,2. "
                   "Sous 1, le bien ne couvre pas sa dette et vous financez la différence.")
    if portage < 0:
        st.warning(
            f"**Portage négatif de {portage:.2f} point par an.** À {ltv} % de LTV, ce bien vous "
            f"coûte de l'argent chaque mois. Le levier construit du capital par amortissement, "
            f"il ne produit pas de revenu tant que le crédit tourne. "
            f"LTV d'équilibre (cash-flow nul) : **{net_charges_pct / annuite_pct * 100:.0f} %**.")
    else:
        st.success(f"Portage positif de {portage:.2f} point par an à {ltv} % de LTV.")

# ---------------------------------------------------------- 4. trajectoire
st.subheader("4 · La trajectoire simulée")

N_SIM = 4000
rng = np.random.default_rng(12345)


def simule_mdb(taux_service):
    """Contrainte d'exécution : le nombre d'opérations, pas le capital.

    `taux_service` est passé en paramètre (et non lu globalement) pour pouvoir
    rejouer la même simulation à coût de dette nul — c'est le contrefactuel du point 5.
    """
    trajectoires = np.zeros((N_SIM, horizon + 1))
    trajectoires[:, 0] = capital_0
    for i in range(N_SIM):
        cap = capital_0
        for an in range(1, horizon + 1):
            n = rng.poisson(ops_an)
            realisables = min(n, int(cap // ticket)) if ticket > 0 else 0
            if realisables > 0:
                echecs = rng.random(realisables) > reussite / 100
                gains = np.where(echecs, -perte_echec / 100 * ticket, marge_op)
                cap += gains.sum()
            cap -= capital_0 * taux_service / 100
            cap = max(cap, 0.0)
            trajectoires[i, an] = cap
    return trajectoires


def simule_locatif():
    """Le patrimoine net = valeur du bien − solde du crédit − capital emprunté."""
    prix = capital_0 / (1 - ltv / 100 + FRICTION_PCT / 100)
    dette = prix * ltv / 100
    r_m = taux_hypo / 100 / 12
    n_m = duree_hypo * 12
    trajectoires = np.zeros((N_SIM, horizon + 1))
    for i in range(N_SIM):
        # aléa sur l'appréciation : volatilité historique du résidentiel belge ~4 %
        chocs = rng.normal(appreciation / 100, 0.04, horizon)
        valeur = prix
        for an in range(1, horizon + 1):
            valeur *= (1 + chocs[an - 1])
            k = min(an * 12, n_m)
            solde = (dette * ((1 + r_m) ** n_m - (1 + r_m) ** k) / ((1 + r_m) ** n_m - 1)
                     if k < n_m else 0.0)
            trajectoires[i, an] = max(valeur - solde - capital_0, 0.0)
        trajectoires[i, 0] = max(prix - dette - capital_0, 0.0)
    return trajectoires


traj = simule_mdb(taux_capital) if strategie == "Marchand de biens" else simule_locatif()

# patrimoine net = capital brut − capital emprunté à rembourser
patrimoine_net = np.maximum(traj - (capital_0 if emprunte else 0.0), 0.0) \
    if strategie == "Marchand de biens" else traj
revenus_mensuels = patrimoine_net * (net_net_pct / 100) / 12

final = revenus_mensuels[:, -1]
p_succes = float(np.mean(final >= objectif_annuel_cible / 12))

f1, f2, f3, f4 = st.columns(4)
f1.metric("Revenu médian à l'horizon", eur(np.median(final), " €/mois"))
f2.metric("Scénario défavorable (p10)", eur(np.percentile(final, 10), " €/mois"))
f3.metric("Scénario favorable (p90)", eur(np.percentile(final, 90), " €/mois"))
f4.metric("Probabilité d'atteindre l'objectif", f"{p_succes * 100:.0f} %",
          delta_color="normal" if p_succes >= 0.5 else "inverse",
          delta="plan tenable" if p_succes >= 0.5 else "plan trop tendu")

annees = np.arange(horizon + 1)
courbe = pd.DataFrame({
    "Année": annees,
    "Défavorable (p10)": np.percentile(revenus_mensuels, 10, axis=0),
    "Médian": np.median(revenus_mensuels, axis=0),
    "Favorable (p90)": np.percentile(revenus_mensuels, 90, axis=0),
    "Objectif": np.full(horizon + 1, objectif_annuel_cible / 12),
}).set_index("Année")
st.line_chart(courbe, height=320)
st.caption(f"Revenu mensuel net atteignable, {eur(N_SIM, '')} tirages. La bande p10–p90 contient "
           f"80 % des trajectoires simulées.")

if strategie == "Marchand de biens":
    cycles_max = capital_0 / ticket * (12 / 9) if ticket else 0
    ops_requises = (capital_requis + (capital_0 if emprunte else 0)) / horizon / marge_op \
        if marge_op else float("inf")
    st.info(
        f"**Contrainte de vélocité.** Avec {eur(capital_0)} et un ticket de {eur(ticket)}, "
        f"vous pouvez mener **{capital_0 / ticket:.1f} opérations en parallèle**, soit au plus "
        f"**{cycles_max:.1f} par an** (cycle de 9 mois). L'objectif en demande "
        f"**{ops_requises:.1f} par an**. "
        + ("✅ La capacité couvre le besoin."
           if cycles_max >= ops_requises else
           f"⚠️ Il manque {(ops_requises / cycles_max - 1) * 100:.0f} % de vélocité : "
           f"réduire le ticket ou raccourcir le cycle augmente la capacité "
           f"plus sûrement que chercher de meilleures marges."))

# ------------------------------------------- 5. coût d'emprunter le capital
if emprunte:
    st.subheader("5 · Ce que coûte le fait d'emprunter ce capital")
    st.caption("Même stratégie, même marché, même durée — seule l'origine du capital change.")
    if strategie == "Marchand de biens":
        # même simulation, sans aucun service de dette
        patrimoine_fp = simule_mdb(0.0)
    else:
        patrimoine_fp = traj + capital_0
    rev_fp = np.median(patrimoine_fp[:, -1]) * (net_net_pct / 100) / 12
    rev_emp = np.median(final)
    g1, g2, g3 = st.columns(3)
    g1.metric("Revenu médian, capital emprunté", eur(rev_emp, " €/mois"))
    g2.metric("Revenu médian, fonds propres", eur(rev_fp, " €/mois"))
    g3.metric("Écart", eur(rev_fp - rev_emp, " €/mois"),
              delta=f"−{(1 - rev_emp / rev_fp) * 100:.0f} %" if rev_fp > 0 else "n.c.",
              delta_color="inverse")

# ------------------------------------------------ 6. sensibilité aux taux
st.subheader("6 · Sensibilité au taux")

if emprunte and risque_taux_capital:
    hausse_recente = ("Le taux BCE vient d'être relevé de 0,25 point le 10/09/2026 (effectif "
                      "le 16/09) — deuxième hausse de l'année." if reference_nom == "BCE" else
                      "L'OLO est passé de 3,51 % en mars 2026 à 3,76 % en août.")
    st.caption(f"**Votre crédit réel suit {reference_nom} + {marge_credit:.2f} pt, variable.** "
               f"{hausse_recente} Cette sensibilité n'est pas théorique — c'est le principal "
               f"risque non couvert du plan.")
    lignes = []
    for choc in (-1.0, -0.5, 0.0, 0.5, 1.0, 2.0):
        ref_choc = max(reference_valeur + choc, 0.0)
        t = ref_choc + marge_credit
        cout = capital_0 * t / 100 * horizon
        cout_actuel = capital_0 * taux_capital / 100 * horizon
        lignes.append({
            reference_nom: f"{ref_choc:.2f} %",
            "Taux de votre capital": f"{t:.2f} %",
            f"Coût total sur {horizon} ans": eur(cout, " €"),
            "Écart vs aujourd'hui": (eur(cout - cout_actuel, " €") if cout >= cout_actuel
                                     else f"−{eur(cout_actuel - cout, ' €')}"),
        })
    st.dataframe(pd.DataFrame(lignes), hide_index=True, width="stretch")
elif emprunte and not risque_taux_capital:
    st.info(
        f"**Scénario de comparaison :** à {taux_capital:.2f} % fixe, aucune sensibilité au "
        f"taux. Ce n'est pas votre crédit réel — il est variable (voir les deux autres options).")
else:
    st.caption("Capital détenu en fonds propres : aucun service de dette, donc aucune "
               "sensibilité au taux sur ce capital.")

if strategie == "Locatif à conserver":
    st.caption(
        f"**Le risque de taux réel de cette stratégie porte sur le crédit hypothécaire "
        f"({taux_hypo:.2f} %), pas sur votre capital de départ.** Il finance la part "
        f"achetée à crédit (LTV {ltv} %) et détermine directement le DSCR — voir section 3.")
    lignes_hypo = []
    for choc in (-1.0, 0.0, 1.0, 2.0):
        t = max(taux_hypo + choc, 0.1)
        a = annuite_facteur(t, duree_hypo) * 12 * 100
        dscr_choc = (net_charges_pct / (a * ltv / 100)) if ltv > 0 else float("inf")
        lignes_hypo.append({
            "Taux hypothécaire": f"{t:.2f} %",
            "Service de la dette": f"{a * ltv / 100:.2f} % /an",
            "DSCR": f"{dscr_choc:.2f}" if np.isfinite(dscr_choc) else "∞",
            "Bancable (≥1,2)": "✅" if dscr_choc >= 1.2 else "❌",
        })
    st.dataframe(pd.DataFrame(lignes_hypo), hide_index=True, width="stretch")
elif strategie == "Marchand de biens":
    st.caption(
        "Dans cette stratégie, les opérations sont financées par le capital engagé "
        "(le ticket), pas par un crédit hypothécaire supplémentaire modélisé ici — "
        "le seul taux qui joue est celui du capital de départ, traité ci-dessus.")

# --------------------------------------------------- 7. suivi reel vs plan
st.subheader("7 · Où en êtes-vous réellement ?")
st.caption(
    "Renseignez votre avancement à chaque visite de cette page pour voir si le rythme réel "
    "tient le plan simulé plus haut. Rien n'est enregistré : ces valeurs ne sont pas "
    "sauvegardées d'une session à l'autre — à noter vous-même si vous voulez les retrouver."
)
h1, h2 = st.columns(2)
mois_ecoules = h1.number_input("Mois écoulés depuis le début du plan", 0, horizon * 12, 0, 1)
if strategie == "Marchand de biens":
    ops_reelles = h2.number_input("Opérations réellement clôturées à ce jour", 0, 500, 0, 1)
    ops_an_requis_plan = (capital_requis + (capital_0 if emprunte else 0)) / horizon / marge_op \
        if marge_op else float("inf")
    if mois_ecoules > 0:
        rythme_reel_an = ops_reelles / (mois_ecoules / 12)
        i1, i2, i3 = st.columns(3)
        i1.metric("Rythme requis", f"{ops_an_requis_plan:.2f} op./an")
        i2.metric("Rythme réel constaté", f"{rythme_reel_an:.2f} op./an",
                  delta=f"{rythme_reel_an - ops_an_requis_plan:+.2f}",
                  delta_color="normal" if rythme_reel_an >= ops_an_requis_plan else "inverse")
        mois_restants = horizon * 12 - mois_ecoules
        ops_manquantes = max(ops_an_requis_plan * horizon - ops_reelles, 0)
        rythme_necessaire_reste = (ops_manquantes / (mois_restants / 12)) if mois_restants > 0 else float("inf")
        i3.metric("Rythme nécessaire sur le temps restant", f"{rythme_necessaire_reste:.2f} op./an",
                  help="Ce qu'il faudrait tenir à partir de maintenant pour rattraper le plan "
                       "initial sans changer l'horizon.")
        if rythme_reel_an < ops_an_requis_plan * 0.8:
            st.warning(
                f"**Retard significatif.** Au rythme constaté, rattraper le plan initial "
                f"demanderait {rythme_necessaire_reste:.1f} opérations/an sur le temps restant — "
                f"au-delà de {ops_an_requis_plan:.1f}, c'est un signal pour ajuster : allonger "
                f"l'horizon (section 1), réduire le ticket (section « Stratégie »), ou revoir "
                f"l'objectif de revenu plutôt que de forcer la cadence.")
        elif rythme_reel_an >= ops_an_requis_plan:
            st.success("Le rythme réel tient ou dépasse le rythme requis par le plan initial.")
    else:
        st.caption("Renseignez le nombre de mois écoulés pour comparer votre rythme réel au plan.")
else:
    st.caption("Le suivi de cadence s'applique à la stratégie « Marchand de biens » — "
               "la stratégie locative est un choix d'allocation initial, pas un rythme récurrent.")

# ------------------------------------------------------------ méthodologie
with st.expander("Méthodologie, hypothèses et limites — à lire avant de décider"):
    st.markdown(f"""
**Ce que fait cette page.** Elle calcule le capital nécessaire à un objectif de revenu, puis
simule {eur(N_SIM, '')} trajectoires pour estimer la probabilité de l'atteindre. Ce sont des
**estimations sous hypothèses affichées**, pas des prévisions.

**Paramètres de marché — lus en base, pas codés en dur**
- OLO 10 ans : **{olo_defaut:.2f} %**{f" ({olo_date})" if olo_date else ""} — BCE, série IRS
  `M.BE.L.L40.CI.0000.EUR.N.Z` (rendement des emprunts publics à long terme).
- Taux hypothécaire : **{hypo_defaut:.2f} %**{f" ({hypo_date})" if hypo_date else ""} — BCE MIR,
  nouveaux contrats, ménages belges.
- Rendements locatifs : `communes_rendement_locatif`, millésime 2026 (prix Statbel T1 2026 ×
  loyers Immoweb 2026), seuil de qualité de 5 transactions et 5 annonces de loyer.

**Friction d'acquisition : {FRICTION_PCT:.1f} %**
{DROITS_ENREGISTREMENT:.1f} % de droits d'enregistrement — taux wallon applicable à un bien
d'investissement. Le taux réduit à 3 % issu de la réforme 2025 est réservé à l'habitation
**propre et unique** et ne s'applique donc pas ici. S'y ajoutent {FRAIS_NOTAIRE:.1f} %
d'honoraires de notaire et de frais d'acte.

**Fiscalité — hypothèse, pas un calcul individuel**
En personne physique, les loyers d'habitation sont imposés en Belgique sur le **revenu cadastral
indexé × 1,4**, pas sur le loyer réel : d'où l'ordre de grandeur de 12 % du loyer retenu ici.
En société, l'ISOC à 25 % porte sur le résultat réel, intérêts et amortissements déduits.
⚠️ **Risque de régime** : une réforme vers l'imposition du loyer réel — discutée de longue date
en Belgique — ferait mécaniquement bondir le capital requis d'environ 30 %.

**Ce que la simulation modélise, et ce qu'elle ignore**
- Modélisé : aléa sur le nombre d'opérations (loi de Poisson), sur leur réussite, et — en locatif —
  sur l'appréciation des prix (volatilité de 4 %/an).
- **Non modélisé — et c'est important si votre crédit est variable** : la trajectoire de la
  section 4 tient le taux du capital **constant** sur tout l'horizon, au niveau choisi en
  section 2. Or votre crédit réel suit la BCE et **varie** : c'est la section 6 (sensibilité),
  pas la section 4, qui montre l'ampleur réelle de ce risque. Aucune volatilité future n'est
  simulée ici faute d'historique BCE suffisamment long et vérifié pour la calibrer honnêtement —
  mieux vaut l'absence d'un chiffre que d'en inventer un.
- **Non modélisé également** : une crise de liquidité (ne pas pouvoir revendre au moment voulu),
  une vacance locative prolongée, un dépassement de travaux corrélé entre opérations, un
  changement de politique de crédit bancaire. Les scénarios défavorables réels sont **corrélés** ;
  la simulation les traite comme indépendants et **sous-estime donc la queue de risque**.

**Limites héritées des données sources**
- Le rendement locatif ne couvre que **77 communes** en 2026 : celles où au moins 5 annonces de
  location et 5 transactions permettent un calcul honnête. Ailleurs, aucun chiffre n'est publié
  plutôt qu'un chiffre construit sur deux annonces.
- Le précompte immobilier n'est **pas** calculé commune par commune : `communes_fiscalite_immo`
  est vide. Il est absorbé dans le forfait de charges de {100 - ratio_net} %.
- La vacance locative n'est **mesurée nulle part** (`taux_vacance_locative_pct` vide à 100 %) :
  elle est également absorbée dans ce forfait, alors que c'est une variable de risque de
  premier ordre.
- Les marges MdB par défaut viennent des **prix demandés** des annonces, pas de transactions
  réelles. L'écart mesuré entre prix demandé et prix acté est de 1,24× — d'où la décote
  appliquée en amont dans le calcul de marge.
""")

st.caption(f"Sources de marché lues en base le {datetime.now():%d/%m/%Y à %H:%M}. "
           "Page créée le 16/09/2026 (revue financière).")
