"""Shared fixtures for Layer 2 tests.

Like Layer 1's tests, these run the REAL pipeline on the REAL generated corpus. Every test gets its own empty Layer 1
stores (config.set_root) AND its own Layer 2 stores (set_stores_dir), a fresh graph and a fresh vector index, so nothing
a test does can touch the real stores. Enrichment runs inline (set_sync) so a test sees the result at once.

The data catalog is read from the real file. Tests that edit the catalog use the `catalog_sandbox` fixture, which points
config at temporary copies.
"""
import shutil
from pathlib import Path

import pytest

from layer1 import config as l1config, generate_data, lifecycle_manager as lm
from layer1.api import main as api_main
from layer2 import config as l2config, enrichment_pipeline as pipeline, entity_extractor, kg as kgmod, vector_index
from layer2.catalog import catalog_store


@pytest.fixture(scope="session")
def corpus(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("corpus")
    generate_data.generate(out)
    return out


def _clear_caches():
    catalog_store._load.cache_clear()
    entity_extractor._index_for.cache_clear()


@pytest.fixture()
def world(tmp_path, corpus):
    old_root, old_stores = l1config.ROOT, l2config._stores["dir"]
    l1config.set_root(tmp_path / "layer1")
    l2config.set_stores_dir(tmp_path / "layer2_stores")
    kgmod.reset_singleton()
    vector_index.reset_singleton()
    lm.clear_state_hooks()
    pipeline.install_hooks()
    pipeline.set_sync(True)
    api_main.TOKENS.clear()
    _clear_caches()
    yield tmp_path
    lm.clear_state_hooks()
    pipeline.set_sync(False)
    l1config.set_root(old_root)
    l2config.set_stores_dir(old_stores)
    kgmod.reset_singleton()
    vector_index.reset_singleton()
    _clear_caches()


@pytest.fixture()
def catalog_sandbox(world, monkeypatch):
    """Temporary copies of the catalog and its extension, for tests that approve entities."""
    cat, ext = world / "pharma_data_catalog.json", world / "watson_extension.json"
    shutil.copy(l2config.CATALOG_JSON, cat)
    shutil.copy(l2config.EXTENSION_JSON, ext)
    monkeypatch.setattr(l2config, "CATALOG_JSON", cat)
    monkeypatch.setattr(l2config, "EXTENSION_JSON", ext)
    _clear_caches()
    yield cat, ext
    _clear_caches()


@pytest.fixture()
def add_doc(world, corpus):
    """add_doc('study_001.pdf', sensitivity='RESTRICTED') -> document uri, ACTIVE and enriched (hooks run inline)."""
    def _add(filename, sensitivity="INTERNAL", ta=None, geo=None, user="u_admin", approve=True, updates=None):
        rec = lm.ingest(corpus / filename)
        uri = rec["document_uri"]
        if approve:
            out = lm.review(uri, True, user, sensitivity_level=sensitivity, allowed_therapeutic_areas=ta,
                            allowed_geographies=geo, metadata_updates=updates, notes="ok")
            assert out["state"] == "ACTIVE"
        return uri
    return _add
