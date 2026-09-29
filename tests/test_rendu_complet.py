"""
Rendu complet du dashboard contre les vraies données (Streamlit AppTest).

Ajouté le 29/09 : une clé de dictionnaire sans guillemets dans la section
« Immeubles de rapport » (r[nb_unites_estime]) faisait planter la page en
production dès qu'un immeuble avait un nombre d'unités extrait du texte —
tout ce qui suivait (analyse MdB, carte, liste) disparaissait. Les tests
unitaires, qui n'extraient que des fonctions pures, ne pouvaient pas le voir.

Lent (~1 min, lit toute la vue) et dépend du réseau : ignoré si les
identifiants Supabase ne sont pas disponibles.

Lancer : /home/antoine/venv/bin/python3 -m pytest tests/test_rendu_complet.py -q
"""
import sys
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DASHBOARD))


def _identifiants_disponibles():
    try:
        from lib import get_credentials
        url, key = get_credentials()
        return bool(url and key)
    except Exception:
        return False


@pytest.mark.skipif(not _identifiants_disponibles(), reason="identifiants Supabase absents")
def test_la_page_entiere_se_rend_sans_exception():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(DASHBOARD / "app.py"), default_timeout=240).run()
    assert [e.value for e in at.exception] == []
    titres = [s.value for s in at.subheader]
    # Les sections du bas de page n'apparaissent que si rien n'a planté avant elles
    for attendu in ("💎 Biens sous le marché local", "Carte", "Liste des annonces"):
        assert attendu in titres
