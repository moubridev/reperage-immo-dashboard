"""
Tests de la reclassification `type_bien_fiable` (app.py).

`app.py` exécute du code Streamlit au chargement (sidebar, fetch réseau) : il n\x27est
pas importable tel quel dans un test. On extrait la fonction et ses tables de
correspondance par AST plutôt que d\x27importer le module — voir aussi la dette notée
dans REVUE_ARCHITECTURE_2026-09-16.md (fonctions pures mélangées au script Streamlit).

Lancer : /home/antoine/venv/bin/python3 -m pytest tests/test_type_bien.py -q
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
        cible = None
        if isinstance(node, ast.Assign):
            cible = getattr(node.targets[0], "id", None)
        elif isinstance(node, ast.FunctionDef):
            cible = node.name
        if cible in noms:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<extrait>", "exec"), ns)
    return ns


NS = _extraire("NON_RESIDENTIEL_SLUGS", "RECLASSEMENT_SLUGS", "type_bien_fiable")
type_bien_fiable = NS["type_bien_fiable"]


@pytest.mark.parametrize("type_bien,url,attendu", [
    # Reclassement "autre" -> type réel (28/09 : sourcing terrain + immeuble)
    ("autre", "https://www.immoweb.be/fr/classified/building-land/for-sale/x/6000/1", "terrain"),
    ("autre", "https://www.immoweb.be/fr/classified/apartment-block/for-sale/x/6000/1", "immeuble"),
    ("autre", "https://www.immoweb.be/fr/classified/mansion/for-sale/x/6000/1", "maison"),
    # Slugs non résidentiels réels : ne doivent PAS être reclassés en terrain/immeuble
    ("autre", "https://www.immoweb.be/fr/classified/farm/for-sale/x/6000/1", "autre"),
    ("autre", "https://www.immoweb.be/fr/classified/hotel-restaurant-cafe/for-sale/x/6000/1", "autre"),
    ("autre", "https://www.immoweb.be/fr/classified/commercial-premises/for-sale/x/6000/1", "autre"),
    # Dégradation maison/appartement mal étiqueté (comportement d\x27origine, 16/09)
    ("maison", "https://www.immoweb.be/fr/classified/house/for-sale/x/6000/1", "maison"),
    ("maison", "https://www.immoweb.be/fr/classified/mixed-use-building/for-sale/x/6000/1", "autre"),
    ("appartement", "https://www.immoweb.be/fr/classified/farm/for-sale/x/6000/1", "autre"),
    # Sources sans URL Immoweb : le type déjà attribué par le scraper reste tel quel
    ("terrain", "https://immo.notaire.be/fr/opportunite/x/1", "terrain"),
    ("appartement", None, "appartement"),
    ("autre", None, "autre"),
])
def test_type_bien_fiable(type_bien, url, attendu):
    assert type_bien_fiable(type_bien, url) == attendu


def test_reclassement_couvre_les_trois_categories_mesurees():
    """Non-régression sur la mesure du 28/09 : building-land, apartment-block et
    mansion doivent rester dans la table, avec leur cible exacte."""
    assert NS["RECLASSEMENT_SLUGS"] == {
        "building-land": "terrain",
        "apartment-block": "immeuble",
        "mansion": "maison",
    }
