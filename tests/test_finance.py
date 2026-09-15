"""
Tests des fonctions financières/statistiques du dashboard.

Ces fonctions pilotent des décisions d'achat réelles (capacité d'emprunt, part de
ménages solvables, taux d'effort) : une régression silencieuse ici produirait des
chiffres faux mais crédibles. Chaque test encode une vérification qui a été faite
à la main le 2026-09-15 contre des données réelles.

Lancer : /home/antoine/venv/bin/python3 -m pytest tests/ -q
"""

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lib import (  # noqa: E402
    annuite_facteur,
    capacite_emprunt,
    revenu_requis_pour_prix,
    pct_menages_au_dessus,
)

# Facteur d'annuité reverse-engineeré le 2026-09-13 depuis les valeurs déjà en base
# (33% du revenu mensuel, 20 ans, taux implicite ~3,67%/an).
FACTEUR_HISTORIQUE_20ANS = 0.0058925


class TestAnnuiteFacteur:
    def test_coherent_avec_le_facteur_historique(self):
        """La formule généralisée doit retrouver le facteur figé utilisé par
        capacite_emprunt_20ans en base, sinon les deux chiffres divergent dans l'UI."""
        facteur = annuite_facteur(3.67, 20)
        assert facteur == pytest.approx(FACTEUR_HISTORIQUE_20ANS, rel=0.002)

    def test_taux_plus_eleve_donne_mensualite_plus_elevee(self):
        assert annuite_facteur(5.0, 20) > annuite_facteur(3.0, 20)

    def test_duree_plus_longue_donne_mensualite_plus_faible(self):
        assert annuite_facteur(3.64, 25) < annuite_facteur(3.64, 20)

    def test_taux_zero_ne_divise_pas_par_zero(self):
        """Cas limite : à 0%, on rembourse simplement le capital / nombre de mois."""
        assert annuite_facteur(0, 20) == pytest.approx(1 / 240)


class TestCapaciteEtRevenuRequis:
    def test_aller_retour_coherent(self):
        """capacite_emprunt et revenu_requis_pour_prix doivent être réciproques :
        le revenu requis pour le prix qu'un revenu permet d'emprunter = ce revenu."""
        revenu = 27135.0  # Mons 2023, valeur réelle
        prix = capacite_emprunt(revenu, 3.64, 20)
        assert revenu_requis_pour_prix(prix, 3.64, 20) == pytest.approx(revenu, rel=1e-9)

    def test_valeur_reelle_mons(self):
        """Mons 2023 : revenu médian 27 135 € -> mensualité 746,21 € (vérifié en base)."""
        mensualite = (27135 / 12) * 0.33
        assert mensualite == pytest.approx(746.21, abs=0.01)
        capacite = capacite_emprunt(27135, 3.67, 20)
        assert capacite == pytest.approx(126749, rel=0.01)

    def test_taux_effort_respecte(self):
        """La règle des 33% doit être appliquée, pas 100% du revenu."""
        revenu = 30000
        capacite = capacite_emprunt(revenu, 4.0, 20)
        mensualite_impliquee = capacite * annuite_facteur(4.0, 20)
        assert mensualite_impliquee == pytest.approx((revenu / 12) * 0.33, rel=1e-9)

    def test_hausse_de_taux_reduit_la_capacite(self):
        """Effet macro attendu : à revenu constant, un taux plus haut réduit la capacité."""
        assert capacite_emprunt(30000, 5.0, 20) < capacite_emprunt(30000, 3.0, 20)


class TestDistributionLogNormale:
    """Paramètres réels de Mons (médiane 27 135, Q1 20 000, Q3 36 133)."""

    MU = 10.20858
    SIGMA = 0.438454

    def test_reproduit_la_mediane(self):
        """Par construction, 50% des ménages sont au-dessus de la médiane."""
        pct = pct_menages_au_dessus(27135, self.MU, self.SIGMA)
        assert pct == pytest.approx(50.0, abs=0.5)

    def test_reproduit_le_premier_quartile(self):
        """75% des ménages sont au-dessus de Q1 — tolérance 3% : l'ajustement
        log-normal est une approximation, pas la distribution observée."""
        pct = pct_menages_au_dessus(20000, self.MU, self.SIGMA)
        assert pct == pytest.approx(75.0, abs=3.0)

    def test_reproduit_le_troisieme_quartile(self):
        pct = pct_menages_au_dessus(36133, self.MU, self.SIGMA)
        assert pct == pytest.approx(25.0, abs=3.0)

    def test_monotone_decroissante(self):
        """Plus le seuil de revenu est haut, moins de ménages le dépassent."""
        seuils = [15000, 25000, 40000, 80000]
        pcts = [pct_menages_au_dessus(s, self.MU, self.SIGMA) for s in seuils]
        assert pcts == sorted(pcts, reverse=True)

    def test_bornee_0_100(self):
        assert pct_menages_au_dessus(1, self.MU, self.SIGMA) <= 100.0
        assert pct_menages_au_dessus(10_000_000, self.MU, self.SIGMA) >= 0.0

    def test_renvoie_none_si_parametres_manquants(self):
        """Une commune sans Q1/Q3 doit afficher 'n.c.', jamais un chiffre inventé."""
        assert pct_menages_au_dessus(30000, None, None) is None
        assert pct_menages_au_dessus(30000, self.MU, 0) is None

    def test_indexation_decale_correctement_la_distribution(self):
        """Indexer le revenu de +8,7% revient à translater mu de ln(1,087) :
        le seuil équivalent indexé doit redonner le même pourcentage."""
        facteur = 1.087
        mu_indexe = self.MU + math.log(facteur)
        pct_brut = pct_menages_au_dessus(27135, self.MU, self.SIGMA)
        pct_indexe = pct_menages_au_dessus(27135 * facteur, mu_indexe, self.SIGMA)
        assert pct_indexe == pytest.approx(pct_brut, abs=1e-6)

    def test_indexation_augmente_la_part_solvable(self):
        """À prix constant, indexer le revenu doit augmenter la part de ménages
        solvables — sinon la correction de millésime serait inutile ou inversée."""
        mu_indexe = self.MU + math.log(1.087)
        assert pct_menages_au_dessus(42704, mu_indexe, self.SIGMA) > pct_menages_au_dessus(
            42704, self.MU, self.SIGMA
        )
