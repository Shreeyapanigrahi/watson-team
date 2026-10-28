"""Shared fixtures. Tests run the REAL pipeline on the REAL generated corpus,
but every test gets its own empty stores via config.set_root(tmp_path)."""
import json
import shutil
from pathlib import Path

import pytest

from layer1 import config, generate_data
from layer1.api import main as api_main


@pytest.fixture(scope="session")
def corpus(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("corpus")
    generate_data.generate(out)
    return out


@pytest.fixture(scope="session")
def manifest(corpus) -> dict:
    return {m["filename"]: m for m in json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))}


@pytest.fixture()
def env(tmp_path):
    """Fresh, empty stores for one test. Restores the real root afterwards."""
    old = config.ROOT
    config.set_root(tmp_path / "layer1")
    api_main.TOKENS.clear()
    yield tmp_path
    config.set_root(old)
