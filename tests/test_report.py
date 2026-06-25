import os

import pytest

from liberty_lint.db import make_session
from liberty_lint.ingest import ingest_file
from liberty_lint.checks import run_checks
from liberty_lint.report import write_csv, write_html

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.lib")


@pytest.fixture
def session_and_lib():
    s = make_session("sqlite:///:memory:")
    [lib] = ingest_file(s, FIXTURE)
    run_checks(s, library_id=lib.id, persist=True)
    return s, lib


def test_write_csv(session_and_lib, tmp_path):
    session, lib = session_and_lib
    out = tmp_path / "report.csv"
    count = write_csv(session, str(out), library_id=lib.id)
    assert count > 0
    content = out.read_text()
    assert "rule_id" in content.splitlines()[0]
    assert "CCS001" in content


def test_write_html(session_and_lib, tmp_path):
    session, lib = session_and_lib
    out = tmp_path / "report.html"
    count = write_html(session, str(out), library_id=lib.id)
    assert count > 0
    content = out.read_text()
    assert "<html>" in content
    assert "CCS001" in content
