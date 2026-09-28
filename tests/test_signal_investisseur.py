"""
Tests de la reclassification par signal investisseur (app.py).

Garde-fou pour l'avenir (demande utilisateur du 28/09 : "comment est-ce qu'on
est sûr qu'on ne passera pas à côté des immeubles ?") : cette fonction est
LIVE dans build_frame(), rejouée à chaque chargement du dashboard sur les
données fraîches — donc automatiquement appliquée à toute nouvelle annonce.
Mais avant ce fichier, rien ne testait sa logique : le bug trouvé le même
jour sur type_bien_fiable (deux branches qui ne s'enchaînaient jamais) aurait
pu se reproduire ici sans qu'aucun test ne l'attrape.

Lancer : /home/antoine/venv/bin/python3 -m pytest tests/test_signal_investisseur.py -q
"""
import ast
from pathlib import Path

import pytest

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def _extraire(*noms):
    src = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    ns = {}
    for node in tree.body:
        cible = node.name if isinstance(node, ast.FunctionDef) else None
        if cible in noms:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<extrait>", "exec"), ns)
    return ns


NS = _extraire("type_bien_via_signal_investisseur")
type_bien_via_signal_investisseur = NS["type_bien_via_signal_investisseur"]


@pytest.mark.parametrize("type_bien,is_investment_property,nb_unites_estime,attendu", [
    # Signal is_investment_property : coché par l'agence, mauvais type choisi
    ("maison", True, None, "immeuble"),
    ("appartement", True, None, "immeuble"),
    ("maison", True, 1, "immeuble"),  # les deux signaux peuvent coexister
    # Signal nb_unites_estime : plusieurs unités déclarées
    ("maison", False, 2, "immeuble"),
    ("appartement", None, 5, "immeuble"),
    # Ni l'un ni l'autre : le type déclaré reste inchangé
    ("maison", False, None, "maison"),
    ("maison", False, 1, "maison"),      # 1 unité = pas un immeuble
    ("appartement", None, None, "appartement"),
    ("appartement", False, 0, "appartement"),
    # Types hors périmètre : jamais reclassés par ce signal, quel que soit
    # is_investment_property (qui n'a de sens que pour requalifier
    # maison/appartement, pas pour toucher un terrain ou un immeuble déjà bon)
    ("terrain", True, 5, "terrain"),
    ("immeuble", True, 5, "immeuble"),
    ("autre", True, 5, "autre"),
    # Données mal formées : jamais de plantage, jamais de reclassement halluciné
    ("maison", False, "invalide", "maison"),
    ("maison", False, float("nan"), "maison"),
])
def test_type_bien_via_signal_investisseur(type_bien, is_investment_property, nb_unites_estime, attendu):
    resultat = type_bien_via_signal_investisseur(type_bien, is_investment_property, nb_unites_estime)
    if isinstance(attendu, str):
        assert resultat == attendu
