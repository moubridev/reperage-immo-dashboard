"""Rendu complet de la page Opportunités contre les vraies données (Streamlit AppTest).
Ignoré si les identifiants Supabase ne sont pas disponibles.
Lancer : /home/antoine/venv/bin/python3 -m pytest tests/test_page_opportunites.py -q
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
def test_la_page_opportunites_se_rend_sans_exception():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(DASHBOARD / "pages" / "7_Opportunites.py"), default_timeout=120).run()
    assert [e.value for e in at.exception] == []
    assert any("Opportunités" in t.value for t in at.title)
    assert len(at.dataframe) >= 1
