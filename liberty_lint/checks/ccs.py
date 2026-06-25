import math

from ..models import CCSTable, CCSVector, TimingArc, Pin, Cell
from .base import Check, Violation


def _ccs_table_query(session, library_id):
    q = (
        session.query(CCSTable)
        .join(TimingArc, CCSTable.timing_arc_id == TimingArc.id)
        .join(Pin, TimingArc.pin_id == Pin.id)
        .join(Cell, Pin.cell_id == Cell.id)
    )
    if library_id is not None:
        q = q.filter(Cell.library_id == library_id)
    return q


def _label(table: CCSTable) -> str:
    arc = table.timing_arc
    pin = arc.pin
    return f"{pin.cell.name}.{pin.name} (related_pin={arc.related_pin}) / {table.table_type}"


class CCSSampleCountMatchCheck(Check):
    """Each vector's index_3 (time) and values (current) arrays must be the same length."""

    rule_id = "CCS001"
    severity = "error"
    description = "CCS vector index_3 sample count must equal values sample count."

    def run(self, session, library_id=None):
        violations = []
        for table in _ccs_table_query(session, library_id):
            for vector in table.vectors:
                n3 = vector.num_index_3_samples
                nv = vector.num_value_samples
                if n3 != nv:
                    violations.append(self.violation(
                        "ccs_vector", vector.id, _label(table),
                        f"vector[{vector.vector_index}] index_3 has {n3} samples but "
                        f"values has {nv} samples",
                    ))
        return violations


class CCSUniformSampleCountCheck(Check):
    """All vectors within one CCS table should share the same number of time samples."""

    rule_id = "CCS002"
    severity = "warning"
    description = "All vectors in a CCS output_current table should have a uniform sample count."

    def run(self, session, library_id=None):
        violations = []
        for table in _ccs_table_query(session, library_id):
            counts = {v.num_index_3_samples for v in table.vectors}
            if len(counts) > 1:
                violations.append(self.violation(
                    "ccs_table", table.id, _label(table),
                    f"vectors have inconsistent sample counts: {sorted(counts)}",
                ))
        return violations


class CCSVectorGridCompletenessCheck(Check):
    """Number of vectors should equal len(index_1) * len(index_2), with no duplicate points."""

    rule_id = "CCS003"
    severity = "error"
    description = "CCS table must have exactly one vector per (index_1, index_2) grid point."

    def run(self, session, library_id=None):
        violations = []
        for table in _ccs_table_query(session, library_id):
            expected = (table.num_index_1 or 0) * (table.num_index_2 or 0)
            actual = table.num_vectors
            if table.num_index_1 and table.num_index_2 and expected != actual:
                violations.append(self.violation(
                    "ccs_table", table.id, _label(table),
                    f"expected {expected} vectors ({table.num_index_1} x {table.num_index_2} grid) "
                    f"but found {actual}",
                ))

            seen = set()
            for vector in table.vectors:
                key = (vector.index_1_value, vector.index_2_value)
                if key in seen:
                    violations.append(self.violation(
                        "ccs_vector", vector.id, _label(table),
                        f"duplicate vector for (index_1={key[0]}, index_2={key[1]})",
                    ))
                seen.add(key)
        return violations


class CCSIndexMonotonicityCheck(Check):
    """index_1/index_2 grids and each vector's index_3 time axis must be strictly increasing."""

    rule_id = "CCS004"
    severity = "error"
    description = "CCS index axes (index_1, index_2, index_3) must be strictly increasing."

    @staticmethod
    def _is_strictly_increasing(values):
        return all(b > a for a, b in zip(values, values[1:]))

    def run(self, session, library_id=None):
        violations = []
        for table in _ccs_table_query(session, library_id):
            for axis_name, axis in (("index_1", table.index_1), ("index_2", table.index_2)):
                if axis and not self._is_strictly_increasing(axis):
                    violations.append(self.violation(
                        "ccs_table", table.id, _label(table),
                        f"{axis_name} grid is not strictly increasing: {axis}",
                    ))

            for vector in table.vectors:
                t = vector.index_3
                if t and not self._is_strictly_increasing(t):
                    violations.append(self.violation(
                        "ccs_vector", vector.id, _label(table),
                        f"vector[{vector.vector_index}] index_3 (time) is not strictly increasing: {t}",
                    ))
        return violations


class CCSTimeOriginCheck(Check):
    """Each vector's time axis should start at or after 0."""

    rule_id = "CCS005"
    severity = "warning"
    description = "CCS vector index_3 (time) should start at a non-negative value."

    def run(self, session, library_id=None):
        violations = []
        for table in _ccs_table_query(session, library_id):
            for vector in table.vectors:
                t = vector.index_3
                if t and t[0] < 0:
                    violations.append(self.violation(
                        "ccs_vector", vector.id, _label(table),
                        f"vector[{vector.vector_index}] index_3 starts at negative time {t[0]}",
                    ))
        return violations


class CCSValuesFiniteCheck(Check):
    """Current waveform samples must be finite numbers (no NaN/Inf)."""

    rule_id = "CCS006"
    severity = "error"
    description = "CCS vector current values must be finite."

    def run(self, session, library_id=None):
        violations = []
        for table in _ccs_table_query(session, library_id):
            for vector in table.vectors:
                values = vector.values or []
                if any(not math.isfinite(v) for v in values):
                    violations.append(self.violation(
                        "ccs_vector", vector.id, _label(table),
                        f"vector[{vector.vector_index}] contains non-finite current values",
                    ))
        return violations


ALL_CCS_CHECKS = [
    CCSSampleCountMatchCheck(),
    CCSUniformSampleCountCheck(),
    CCSVectorGridCompletenessCheck(),
    CCSIndexMonotonicityCheck(),
    CCSTimeOriginCheck(),
    CCSValuesFiniteCheck(),
]
