"""Shared pytest fixtures. Each test gets an isolated temp data_path + coordinator."""

import os
import tempfile

import pytest

from jobpilot.config import Config
from jobpilot.coordinator import Coordinator


@pytest.fixture()
def tmp_config(tmp_path, monkeypatch):
    data = tmp_path / "jpdata"
    monkeypatch.setenv("JOBPILOT_DATA", str(data))
    # config.ini absent → resolution falls back to JOBPILOT_DATA.
    cfg = Config(config_path=tmp_path / "config.ini")
    return cfg


@pytest.fixture()
def coordinator(tmp_config):
    co = Coordinator(tmp_config)
    yield co
    co.stop()
