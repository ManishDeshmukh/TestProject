from ..models import NLDMTable, TimingArc, Pin, Cell
from .base import Check


def _nldm_table_query(session, library_id):
    q = (
        session.query(NLDMTable)
        .join(TimingArc, NLDMTable.timing_arc_id == TimingArc.id)
        .join(Pin, TimingArc.pin_id == Pin.id)
        .join(Cell, Pin.cell_id == Cell.id)
    )
    if library_id is not None:
        q = q.filter(Cell.library_id == library_id)
    return q


def _label(table: NLDMTable) -> str:
    arc = table.timing_arc
    pin = arc.pin
    return f"{pin.cell.name}.{pin.name} (related_pin={arc.related_pin}) / {table.table_type}"


class NLDMTableDimensionCheck(Check):
    """The values matrix shape must match the declared index_1 x index_2 grid."""

    rule_id = "NLDM001"
    severity = "error"
    description = "NLDM table values dimensions must match index_1/index_2 lengths."

    def run(self, session, library_id=None):
        violations = []
        for table in _nldm_table_query(session, library_id):
            if table.num_index_1 and table.num_value_rows and table.num_index_1 != table.num_value_rows:
                violations.append(self.violation(
                    "nldm_table", table.id, _label(table),
                    f"values has {table.num_value_rows} rows but index_1 has {table.num_index_1} points",
                ))
            if table.num_index_2 and table.num_value_cols and table.num_index_2 != table.num_value_cols:
                violations.append(self.violation(
                    "nldm_table", table.id, _label(table),
                    f"values has {table.num_value_cols} cols but index_2 has {table.num_index_2} points",
                ))
        return violations


class NLDMIndexMonotonicityCheck(Check):
    """index_1/index_2 grids must be strictly increasing."""

    rule_id = "NLDM002"
    severity = "error"
    description = "NLDM index_1/index_2 grids must be strictly increasing."

    @staticmethod
    def _is_strictly_increasing(values):
        return all(b > a for a, b in zip(values, values[1:]))

    def run(self, session, library_id=None):
        violations = []
        for table in _nldm_table_query(session, library_id):
            for axis_name, axis in (("index_1", table.index_1), ("index_2", table.index_2)):
                if axis and not self._is_strictly_increasing(axis):
                    violations.append(self.violation(
                        "nldm_table", table.id, _label(table),
                        f"{axis_name} grid is not strictly increasing: {axis}",
                    ))
        return violations


ALL_NLDM_CHECKS = [
    NLDMTableDimensionCheck(),
    NLDMIndexMonotonicityCheck(),
]
