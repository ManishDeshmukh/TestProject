import os

import pytest

from liberty_lint.db import make_session
from liberty_lint.ingest import ingest_file
from liberty_lint.models import Cell, NLDMTable

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.lib")


@pytest.fixture
def session():
    s = make_session("sqlite:///:memory:")
    ingest_file(s, FIXTURE)
    return s


def test_library_and_cells_ingested(session):
    cells = {c.name for c in session.query(Cell).all()}
    assert cells == {"GOOD_INV", "BAD_INV", "GOOD_BUF", "BAD_BUF"}


def test_nldm_good_inv_has_matching_dimensions(session):
    cell = session.query(Cell).filter(Cell.name == "GOOD_INV").one()
    table = cell.pins[1].timing_arcs[0].nldm_tables[0]
    assert table.num_index_1 == 2
    assert table.num_index_2 == 2
    assert table.num_value_rows == 2
    assert table.num_value_cols == 2


def test_nldm_bad_inv_dimension_mismatch(session):
    cell = session.query(Cell).filter(Cell.name == "BAD_INV").one()
    table = cell.pins[1].timing_arcs[0].nldm_tables[0]
    assert table.num_index_1 == 3
    assert table.num_value_rows == 2


def test_ccs_good_buf_vectors(session):
    cell = session.query(Cell).filter(Cell.name == "GOOD_BUF").one()
    table = cell.pins[1].timing_arcs[0].ccs_tables[0]
    assert table.num_vectors == 4
    for vector in table.vectors:
        assert vector.num_index_3_samples == vector.num_value_samples == 4


def test_ccs_bad_buf_sample_mismatch(session):
    cell = session.query(Cell).filter(Cell.name == "BAD_BUF").one()
    table = cell.pins[1].timing_arcs[0].ccs_tables[0]
    v0 = next(v for v in table.vectors if v.vector_index == 0)
    assert v0.num_index_3_samples != v0.num_value_samples
