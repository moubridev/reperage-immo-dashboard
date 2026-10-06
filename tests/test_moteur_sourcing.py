"""Tests du moteur de calcul unique (dashboard + agent sourcing).

Chaque test encode une règle décidée par Antoine le 2026-10-06 ou un piège déjà rencontré :
une régression ici produirait des prix max faux mais crédibles.
Lancer : /home/antoine/venv/bin/python3 -m pytest tests/test_moteur_sourcing.py -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from moteur_sourcing import (Hypotheses, ReferencesRevente, bande_surface, bilan, decoupe, flip,  # noqa: E402
                             frais_achat, honoraires_notaire, niveau_marge, prix_max, rendement_location)


class TestBaremeNotaire:
    def test_premiere_tranche(self):
        assert honoraires_notaire(5_000) == pytest.approx(5_000 * 0.0456)

    def test_200k_somme_des_tranches(self):
        attendu = (7_500 * .0456 + 10_000 * .0285 + 12_500 * .0228 + 15_495 * .0171 + 18_600 * .0114
                   + (200_000 - 64_095) * .0057)
        assert honoraires_notaire(200_000) == pytest.approx(attendu)
        assert 2_100 < honoraires_notaire(200_000) < 2_200

    def test_au_dela_de_250k_taux_tres_faible(self):
        assert honoraires_notaire(500_000) - honoraires_notaire(250_095) == pytest.approx((500_000 - 250_095) * 0.00057)


class TestFraisAchat:
    def test_restitution_trois_cinquiemes(self):
        f = frais_achat(100_000, Hypotheses())
        assert f["droits_brut"] == pytest.approx(12_500)
        assert f["droits_net"] == pytest.approx(5_000)

    def test_location_pas_de_restitution(self):
        assert frais_achat(100_000, Hypotheses(), conserve=True)["droits_net"] == pytest.approx(12_500)

    def test_notaire_tva_et_frais_acte(self):
        h = Hypotheses()
        assert frais_achat(100_000, h)["notaire"] == pytest.approx(honoraires_notaire(100_000) * 1.21 + 1300)


class TestBilan:
    def test_pas_de_frais_de_revente_par_defaut(self):
        assert bilan(100_000, 200_000, 0, Hypotheses())["frais_revente"] == 0

    def test_decote_appliquee_a_la_revente(self):
        assert bilan(100_000, 200_000, 0, Hypotheses())["arv"] == pytest.approx(184_000)

    def test_marge_diminue_quand_le_prix_monte(self):
        h = Hypotheses()
        assert bilan(120_000, 250_000, 30_000, h)["marge"] > bilan(140_000, 250_000, 30_000, h)["marge"]


class TestPrixMax:
    def test_prix_max_donne_exactement_le_seuil(self):
        h = Hypotheses()
        pm = prix_max(250_000, 40_000, h)
        assert bilan(pm, 250_000, 40_000, h)["marge_pct"] == pytest.approx(h.seuil_go, abs=0.002)

    def test_inatteignable(self):
        assert prix_max(100_000, 200_000, Hypotheses()) is None


class TestGrilleTravaux:
    def test_grille_recalee_2026_10_06(self):
        h = Hypotheses()
        assert (h.travaux_m2_pour("D"), h.travaux_m2_pour("G"), h.travaux_m2_pour(None)) == (600, 1300, 900)

    def test_depuis_params(self):
        h = Hypotheses.depuis_params([{"param_nom": "forfait_travaux_peb_e", "param_valeur": "950"},
                                      {"param_nom": "seuil_marge_go", "param_valeur": "0.25"},
                                      {"param_nom": "frais_revente_agence_taux", "param_valeur": "0.03"}])
        assert h.travaux_m2_pour("E") == 950 and h.seuil_go == 0.25 and h.frais_revente_taux == 0.03

    def test_flip_utilise_la_grille(self):
        f = flip(100_000, 100, "F", 2_500, Hypotheses())
        assert f["travaux"] == pytest.approx(100 * 1300 * 1.06)


class TestDecoupeEtLocation:
    def test_decoupe_compte_les_frais_de_division(self):
        d = decoupe(300_000, 300, "C", 4, 150_000, Hypotheses())
        assert d["fixes"] == pytest.approx(12_000 + 4 * 4_000)

    def test_rendement_location(self):
        r = rendement_location(200_000, 2_000, Hypotheses())
        assert r["rendement_sur_prix"] == pytest.approx(0.12)
        assert r["rendement_brut"] < 0.12   # frais d'achat inclus


class TestNiveau:
    def test_seuils(self):
        h = Hypotheses()
        assert (niveau_marge(0.25, h), niveau_marge(0.12, h), niveau_marge(0.05, h)) == ("go", "a_etudier", None)


class TestReferences:
    def test_jamais_de_regroupement_de_communes(self):
        refs = ReferencesRevente(nb_min=5)
        for i in range(10):
            refs.ajouter("7700", "Mouscron", "maison", 130, 260_000 + i, "B")
        assert refs.prix_m2("7700", "Mouscron", "maison", 130)[0] is not None
        assert refs.prix_m2("7711", "Dottignies", "maison", 130)[2] == "non calculable"

    def test_seuls_les_biens_renoves_et_anciens(self):
        refs = ReferencesRevente(nb_min=1)
        refs.ajouter("7700", "Mouscron", "maison", 130, 200_000, "F")
        refs.ajouter("7700", "Mouscron", "maison", 130, 200_000, "A", neuf=True)
        assert refs.prix_m2("7700", "Mouscron", "maison", 130)[2] == "non calculable"

    def test_repli_sur_la_commune_toutes_tailles(self):
        refs = ReferencesRevente(nb_min=3)
        for s in (100, 150, 200):
            refs.ajouter("7000", "Mons", "maison", s, s * 2_000, "B")
        m2, n, niveau = refs.prix_m2("7000", "Mons", "maison", 130)
        assert (round(m2), n, niveau) == (2000, 3, "commune")

    def test_bande(self):
        assert bande_surface("maison", 125) == (120, 180)
