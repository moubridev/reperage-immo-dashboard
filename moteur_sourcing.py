"""Moteur de calcul unique Moubri : marge d'un flip, découpe, rendement locatif, prix maximum.

Partagé par le dashboard (app.py, section « Analyse approfondie MdB ») et par l'agent
sourcing du serveur (src/sourcing_detecteur.py), pour qu'il n'existe qu'UNE formule.
Aucune dépendance Streamlit : importable partout.

Hypothèses = table `params_utilisateur` (référence unique décidée le 2026-10-06) :
- droits d'enregistrement 12,5 % payés à l'achat, 3/5 restitués à la revente (MdB) ;
  le financement porte sur les 12,5 % avancés ;
- honoraires notaire = barème légal (barème J, AR du 16/12/1950) + TVA 21 % + frais d'acte fixes ;
- travaux = grille €/m² HTVA par PEB (recalée le 2026-10-06) + TVA 6 % ;
- revente minorée de la décote prix demandé -> prix acté (comparables = prix DEMANDÉS) ;
- frais de revente paramétrables (0 : vente en interne).
"""
from __future__ import annotations

import math
import statistics as st
from dataclasses import dataclass, field

# Barème J (vente de gré à gré), tranches cumulatives : (plafond de la tranche, taux)
BAREME_HONORAIRES = (
    (7_500, 0.0456), (17_500, 0.0285), (30_000, 0.0228), (45_495, 0.0171),
    (64_095, 0.0114), (250_095, 0.0057), (math.inf, 0.00057),
)

PEB_RENOVES = ("A++", "A+", "A", "B")
BANDES_SURFACE = {
    "maison": ((0, 120), (120, 180), (180, 250), (250, 350), (350, 600)),
    "appartement": ((0, 60), (60, 90), (90, 130), (130, 180), (180, 350)),
}


def honoraires_notaire(prix: float) -> float:
    """Honoraires HTVA du barème légal pour un prix donné."""
    total, bas = 0.0, 0.0
    for plafond, taux in BAREME_HONORAIRES:
        if prix <= bas:
            break
        total += (min(prix, plafond) - bas) * taux
        bas = plafond
    return total


@dataclass
class Hypotheses:
    droits_taux: float = 0.125
    restitution: float = 0.6
    tva_honoraires: float = 0.21
    frais_acte: float = 1300.0
    frais_divers_taux: float = 0.005
    travaux_m2: dict = field(default_factory=lambda: {"A++": 100, "A+": 100, "A": 100, "B": 150, "C": 600,
                                                      "D": 600, "E": 900, "F": 1300, "G": 1300})
    travaux_inconnu: float = 900.0
    tva_travaux: float = 0.06
    decote_arv: float = 0.08
    frais_revente_taux: float = 0.0
    taux_financement: float = 0.035
    portage_mois: float = 9
    charges_mois: float = 180.0
    seuil_go: float = 0.20
    seuil_limite: float = 0.10
    majoration_encheres: float = 0.0
    isoc: float | None = None
    division_acte_base: float = 12_000.0
    division_par_lot: float = 4_000.0
    division_portage_mois: float = 12
    nb_min_comparables: int = 5

    @classmethod
    def depuis_params(cls, lignes: list[dict]) -> "Hypotheses":
        """Construit les hypothèses depuis les lignes de `params_utilisateur` (param_nom, param_valeur)."""
        p = {}
        for l in lignes:
            try:
                p[l["param_nom"]] = float(l["param_valeur"])
            except (TypeError, ValueError, KeyError):
                continue
        h = cls()

        def lire(nom, attr):
            if nom in p:
                setattr(h, attr, p[nom])

        lire("droits_enregistrement_taux", "droits_taux")
        lire("restitution_droits_fraction", "restitution")
        lire("tva_honoraires_notaire", "tva_honoraires")
        lire("frais_acte_fixes_eur", "frais_acte")
        lire("frais_divers_taux", "frais_divers_taux")
        lire("forfait_travaux_peb_inconnu", "travaux_inconnu")
        lire("tva_travaux", "tva_travaux")
        lire("decote_arv", "decote_arv")
        lire("duree_portage_mois", "portage_mois")
        lire("charges_portage_mensuelles", "charges_mois")
        lire("seuil_marge_go", "seuil_go")
        lire("seuil_marge_limite", "seuil_limite")
        lire("majoration_encheres", "majoration_encheres")
        lire("division_acte_base_eur", "division_acte_base")
        lire("division_par_lot_eur", "division_par_lot")
        lire("division_portage_mois", "division_portage_mois")
        if "taux_credit_hypothecaire_ref" in p:
            h.taux_financement = p["taux_credit_hypothecaire_ref"] / 100
        if "nb_biens_min_reference" in p:
            h.nb_min_comparables = int(p["nb_biens_min_reference"])
        h.frais_revente_taux = p.get("frais_revente_agence_taux", 0.0) + p.get("frais_notaire_vente_taux", 0.0)
        for peb, suffixe in (("A++", "ap"), ("A+", "ap"), ("A", "a"), ("B", "b"), ("C", "c"), ("D", "d"),
                             ("E", "e"), ("F", "f"), ("G", "g")):
            if f"forfait_travaux_peb_{suffixe}" in p:
                h.travaux_m2[peb] = p[f"forfait_travaux_peb_{suffixe}"]
        return h

    def travaux_m2_pour(self, peb) -> float:
        return float(self.travaux_m2.get(str(peb or "").upper().strip(), self.travaux_inconnu))


# ------------------------------------------------------------------ frais d'achat


def frais_achat(prix: float, h: Hypotheses, conserve: bool = False) -> dict:
    """Frais d'acquisition. `conserve=True` (location) : pas de restitution des droits."""
    droits_brut = prix * h.droits_taux
    droits_net = droits_brut if conserve else droits_brut * (1 - h.restitution)
    notaire = honoraires_notaire(prix) * (1 + h.tva_honoraires) + h.frais_acte
    divers = prix * h.frais_divers_taux
    return dict(droits_brut=droits_brut, droits_net=droits_net, notaire=notaire, divers=divers)


# ------------------------------------------------------------------ bilan d'une opération achat-revente


def bilan(prix: float, valeur_revente_demandee: float, travaux_ht: float, h: Hypotheses,
          mois: float | None = None, fixes: float = 0.0) -> dict:
    """Bilan complet d'une opération achat -> travaux -> revente.

    valeur_revente_demandee : valeur de revente au niveau des PRIX DEMANDÉS (comparables) ;
    la décote vers le prix acté est appliquée ici.
    fixes : coûts fixes hors travaux (acte de base d'une découpe, frais par lot...)."""
    mois = h.portage_mois if mois is None else mois
    arv = valeur_revente_demandee * (1 - h.decote_arv)
    fa = frais_achat(prix, h)
    travaux = travaux_ht * (1 + h.tva_travaux)
    financement = (prix + fa["droits_brut"] + fa["notaire"] + 0.5 * (travaux + fixes)) * h.taux_financement * mois / 12
    charges = h.charges_mois * mois
    revente = arv * h.frais_revente_taux
    marge = arv - revente - prix - fa["droits_net"] - fa["notaire"] - fa["divers"] - travaux - fixes - financement - charges
    if h.isoc:
        marge = marge * (1 - h.isoc) if marge > 0 else marge
    capital = prix + fa["droits_brut"] + fa["notaire"] + fa["divers"] + travaux + fixes + charges
    return dict(prix=prix, arv=arv, droits_net=fa["droits_net"], droits_brut=fa["droits_brut"], notaire=fa["notaire"],
                divers=fa["divers"], travaux=travaux, fixes=fixes, financement=financement, charges=charges,
                frais_revente=revente, marge=marge, marge_pct=marge / arv if arv > 0 else None,
                capital_engage=capital, roi=marge / capital if capital > 0 else None, mois=mois)


def prix_max(valeur_revente_demandee: float, travaux_ht: float, h: Hypotheses, mois: float | None = None,
             fixes: float = 0.0, cible: float | None = None) -> float | None:
    """Prix d'achat maximum qui laisse exactement `cible` (défaut : seuil GO) de marge sur l'ARV.
    Recherche par dichotomie (le barème notarial n'est pas linéaire). None si même à 0 € la cible est hors d'atteinte."""
    cible = h.seuil_go if cible is None else cible

    def ecart(p):
        b = bilan(p, valeur_revente_demandee, travaux_ht, h, mois, fixes)
        return (b["marge_pct"] or -1) - cible

    if valeur_revente_demandee <= 0 or ecart(1.0) < 0:
        return None
    bas, haut = 1.0, valeur_revente_demandee
    for _ in range(60):
        milieu = (bas + haut) / 2
        if ecart(milieu) >= 0:
            bas = milieu
        else:
            haut = milieu
    return round(bas, -2)


# ------------------------------------------------------------------ stratégies


def flip(prix: float, surface: float, peb, prix_m2_revente: float, h: Hypotheses) -> dict:
    """Achat à rénover, revente au prix/m² des biens rénovés comparables."""
    travaux_ht = surface * h.travaux_m2_pour(peb)
    valeur = prix_m2_revente * surface
    b = bilan(prix, valeur, travaux_ht, h)
    b["prix_max"] = prix_max(valeur, travaux_ht, h)
    b["travaux_m2_ht"] = h.travaux_m2_pour(peb)
    return b


def decoupe(prix: float, surface: float, peb, n_lots: int, valeur_lot: float, h: Hypotheses) -> dict:
    """Immeuble revendu lot par lot : valeur = n lots x prix médian d'un appartement comparable."""
    travaux_ht = surface * h.travaux_m2_pour(peb)
    fixes = h.division_acte_base + n_lots * h.division_par_lot
    valeur = n_lots * valeur_lot
    b = bilan(prix, valeur, travaux_ht, h, mois=h.division_portage_mois, fixes=fixes)
    b["prix_max"] = prix_max(valeur, travaux_ht, h, mois=h.division_portage_mois, fixes=fixes)
    b["n_lots"] = n_lots
    return b


def rendement_location(prix: float, loyer_mensuel_total: float, h: Hypotheses, travaux_ht: float = 0.0) -> dict:
    """Rendement brut d'une détention locative : droits NON restitués (le bien est conservé)."""
    fa = frais_achat(prix, h, conserve=True)
    investi = prix + fa["droits_net"] + fa["notaire"] + fa["divers"] + travaux_ht * (1 + h.tva_travaux)
    loyers = 12 * loyer_mensuel_total
    return dict(investi=investi, loyers_annuels=loyers, rendement_brut=loyers / investi if investi > 0 else None,
                rendement_sur_prix=loyers / prix if prix > 0 else None)


def niveau_marge(marge_pct, h: Hypotheses) -> str | None:
    if marge_pct is None:
        return None
    if marge_pct >= h.seuil_go:
        return "go"
    if marge_pct >= h.seuil_limite:
        return "a_etudier"
    return None


# ------------------------------------------------------------------ comparables (précision : CP + commune)


def bande_surface(type_bien: str, surface) -> tuple | None:
    try:
        s = float(surface)
    except (TypeError, ValueError):
        return None
    return next((b for b in BANDES_SURFACE.get(type_bien, ()) if b[0] <= s < b[1]), None)


def cle_commune(code_postal, commune) -> tuple:
    return (str(code_postal or "")[:4], str(commune or "?").strip().upper())


class ReferencesRevente:
    """Prix/m² de revente des biens RÉNOVÉS (PEB A-B, anciens), par CP + commune exacts.
    Jamais de regroupement de communes ni de repli régional (préférence utilisateur) :
    sans assez de comparables, la revente est « non calculable »."""

    def __init__(self, nb_min: int = 5):
        self.nb_min = nb_min
        self.par_bande: dict = {}
        self.par_type: dict = {}

    def ajouter(self, code_postal, commune, type_bien, surface, prix, peb, neuf=False):
        if neuf or str(peb or "").upper() not in PEB_RENOVES or type_bien not in BANDES_SURFACE:
            return
        try:
            s, p = float(surface), float(prix)
        except (TypeError, ValueError):
            return
        if s <= 0 or not (400 <= p / s <= 10_000):
            return
        c = cle_commune(code_postal, commune)
        self.par_bande.setdefault((c, type_bien, bande_surface(type_bien, s)), []).append(p / s)
        self.par_type.setdefault((c, type_bien), []).append(p / s)

    def prix_m2(self, code_postal, commune, type_bien, surface) -> tuple[float | None, int, str]:
        """(prix/m² médian, nb comparables, niveau) — niveau 'tranche' ou 'commune' (même type, toutes tailles)."""
        c = cle_commune(code_postal, commune)
        v = self.par_bande.get((c, type_bien, bande_surface(type_bien, surface)), [])
        if len(v) >= self.nb_min:
            return st.median(v), len(v), "tranche"
        v = self.par_type.get((c, type_bien), [])
        if len(v) >= self.nb_min:
            return st.median(v), len(v), "commune"
        return None, len(v), "non calculable"
