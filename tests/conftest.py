"""
conftest.py — configurazione globale pytest.
Necessario per il corretto discovery dei test async.
"""
import pytest

# Questo file è necessario per far funzionare pytest-asyncio
# in modalità AUTO con la struttura a classi dei test.
