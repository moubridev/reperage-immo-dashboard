"""
Tests de la classification « prix ferme vs mise à prix » (app.py).

Ajouté le 29/09 : les ventes publiques annoncées sur Immoweb affichent une mise
à prix, comme Biddit et notaire.be. Détectées par verifier_statuts_immoweb.py
(colonne sous_type_vente), elles doivent rejoindre la section enchères et ne
jamais entrer dans le classement principal « sous le marché ».

Lancer : /home/antoine/venv/bin/python3 -m pytest tests/test_source_prix.py -q
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


NS = _extraire("classify_source_prix", "source_prix_effective", "SOURCES_ENCHERE")
source_prix_effective = NS["source_prix_effective"]
SOURCES_ENCHERE = NS["SOURCES_ENCHERE"]

IMMOWEB = "https://www.immoweb.be/en/classified/house/for-sale/mons/7000/21613902"
BIDDIT = "https://www.biddit.be/fr/catalog/detail/12345"
NOTAIRE = "https://immo.notaire.be/fr/opportunite/67890"


@pytest.mark.parametrize("url,sous_type,attendu", [
    (IMMOWEB, None, "immoweb"),
    (IMMOWEB, "vente_publique", "enchere_immoweb"),
    # Le viager est exclu en amont par la vue : ici il ne change pas la source
    (IMMOWEB, "viager", "immoweb"),
    (BIDDIT, None, "enchere_biddit"),
    (BIDDIT, "vente_publique", "enchere_biddit"),
    (NOTAIRE, None, "enchere_notaire"),
    (None, None, "immoweb"),
    (float("nan"), float("nan"), "immoweb"),
])
def test_source_prix_effective(url, sous_type, attendu):
    assert source_prix_effective(url, sous_type) == attendu


def test_toutes_les_encheres_sont_majorees_dans_le_scoring_mdb():
    for url, st in [(BIDDIT, None), (NOTAIRE, None), (IMMOWEB, "vente_publique")]:
        assert source_prix_effective(url, st) in SOURCES_ENCHERE
    assert source_prix_effective(IMMOWEB, None) not in SOURCES_ENCHERE
