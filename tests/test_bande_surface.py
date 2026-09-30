"""
Tranches de surface du comparateur « sous le marché » (app.py), ajoutées le 30/09 :
le haut du classement était rempli de grandes maisons, comparées à la médiane de
toutes les maisons (et appartements) de la commune.

Lancer : /home/antoine/venv/bin/python3 -m pytest tests/test_bande_surface.py -q
"""
import ast
from pathlib import Path

import pytest

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


def _extraire(*noms):
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    ns = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in noms:
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<extrait>", "exec"), ns)
        elif isinstance(node, ast.Assign) and any(getattr(t, "id", None) in noms for t in node.targets):
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<extrait>", "exec"), ns)
    return ns


NS = _extraire("BANDES_SURFACE_HABITABLE", "bande_surface_habitable")
bande = NS["bande_surface_habitable"]


@pytest.mark.parametrize("type_bien,surface,attendu", [
    ("maison", 95, "< 120 m²"),
    ("maison", 120, "120-180 m²"),
    ("maison", 249.9, "180-250 m²"),
    ("maison", 320, "> 250 m²"),
    ("appartement", 45, "< 60 m²"),
    ("appartement", 75, "60-90 m²"),
    ("appartement", 130, "> 130 m²"),
    ("terrain", 800, None),       # les terrains ont leur propre comparateur
    ("maison", None, None),
    ("maison", float("nan"), None),
    ("maison", "abc", None),
])
def test_bandes(type_bien, surface, attendu):
    assert bande(type_bien, surface) == attendu


@pytest.mark.parametrize("type_bien", ["maison", "appartement"])
def test_bandes_contigues_sans_trou(type_bien):
    bandes = NS["BANDES_SURFACE_HABITABLE"][type_bien]
    for (_, haut, _), (bas_suivant, _, _) in zip(bandes, bandes[1:]):
        assert haut == bas_suivant
    assert bandes[0][0] == 0
