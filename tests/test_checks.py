import os

import pytest

from liberty_lint.db import make_session
from liberty_lint.ingest import ingest_file
from liberty_lint.checks import run_checks
from liberty_lint.models import Library

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.lib")


@pytest.fixture
def session_and_lib():
    s = make_session("sqlite:///:memory:")
    [lib] = ingest_file(s, FIXTURE)
    return s, lib


def test_no_violations_for_good_cells(session_and_lib):
    session, lib = session_and_lib
    violations = run_checks(session, library_id=lib.id, persist=False)
    bad_object_labels = {v.object_label for v in violations}
    assert not any("GOOD_INV" in label or "GOOD_BUF" in label for label in bad_object_labels)


def test_ccs001_sample_count_mismatch_detected(session_and_lib):
    session, lib = session_and_lib
    violations = run_checks(session, library_id=lib.id, rule_ids=["CCS001"], persist=False)
    assert len(violations) == 1
    assert "BAD_BUF" in violations[0].object_label


def test_ccs002_uniform_sample_count_detected(session_and_lib):
    session, lib = session_and_lib
    violations = run_checks(session, library_id=lib.id, rule_ids=["CCS002"], persist=False)
    assert len(violations) == 1
    assert "BAD_BUF" in violations[0].object_label


def test_ccs003_grid_completeness_and_duplicate_detected(session_and_lib):
    session, lib = session_and_lib
    violations = run_checks(session, library_id=lib.id, rule_ids=["CCS003"], persist=False)
    messages = [v.message for v in violations]
    assert any("expected 4 vectors" in m for m in messages)
    assert any("duplicate vector" in m for m in messages)


def test_ccs004_monotonicity_detected(session_and_lib):
    session, lib = session_and_lib
    violations = run_checks(session, library_id=lib.id, rule_ids=["CCS004"], persist=False)
    messages = [v.message for v in violations]
    assert any("index_2 grid is not strictly increasing" in m for m in messages)
    assert any("index_3 (time) is not strictly increasing" in m for m in messages)


def test_ccs005_negative_time_origin_detected(session_and_lib):
    session, lib = session_and_lib
    violations = run_checks(session, library_id=lib.id, rule_ids=["CCS005"], persist=False)
    assert len(violations) == 1
    assert "negative time" in violations[0].message


def test_ccs006_non_finite_values_detected(session_and_lib):
    session, lib = session_and_lib
    violations = run_checks(session, library_id=lib.id, rule_ids=["CCS006"], persist=False)
    assert len(violations) == 1


def test_nldm001_dimension_mismatch_detected(session_and_lib):
    session, lib = session_and_lib
    violations = run_checks(session, library_id=lib.id, rule_ids=["NLDM001"], persist=False)
    assert len(violations) == 1
    assert "BAD_INV" in violations[0].object_label


def test_nldm002_monotonicity_detected(session_and_lib):
    session, lib = session_and_lib
    violations = run_checks(session, library_id=lib.id, rule_ids=["NLDM002"], persist=False)
    assert len(violations) == 1
    assert "BAD_INV" in violations[0].object_label


def test_persisted_check_results_written(session_and_lib):
    session, lib = session_and_lib
    run_checks(session, library_id=lib.id, persist=True)
    from liberty_lint.models import CheckResult
    results = session.query(CheckResult).filter(CheckResult.library_id == lib.id).all()
    assert len(results) > 0
