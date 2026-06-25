"""Walk a liberty-parser Group tree and load it into the SQLAlchemy schema."""
import json

from liberty.parser import parse_multi_liberty

from .models import Library, Cell, Pin, TimingArc, NLDMTable, CCSTable, CCSVector
from .values import unwrap, unwrap_str, unwrap_float, unwrap_bool, array_1d, array_2d

NLDM_TABLE_TYPES = (
    "cell_rise", "cell_fall",
    "rise_transition", "fall_transition",
    "rise_constraint", "fall_constraint",
)
CCS_TABLE_TYPES = ("output_current_rise", "output_current_fall")


def ingest_file(session, file_path: str):
    """Parse a .lib file and persist every `library()` group it contains.

    Returns the list of created Library rows.
    """
    with open(file_path, "r") as f:
        text = f.read()

    groups = parse_multi_liberty(text)
    libraries = []
    for group in groups:
        if group.group_name == "library":
            libraries.append(_ingest_library(session, group, file_path))

    session.commit()
    return libraries


def _ingest_library(session, lib_group, file_path):
    library = Library(
        name=lib_group.args[0] if lib_group.args else "unnamed",
        file_path=file_path,
        technology=unwrap_str(lib_group, "technology"),
        delay_model=unwrap_str(lib_group, "delay_model"),
        time_unit=unwrap_str(lib_group, "time_unit"),
        voltage_unit=unwrap_str(lib_group, "voltage_unit"),
        current_unit=unwrap_str(lib_group, "current_unit"),
        nom_process=unwrap_float(lib_group, "nom_process"),
        nom_voltage=unwrap_float(lib_group, "nom_voltage"),
        nom_temperature=unwrap_float(lib_group, "nom_temperature"),
    )
    session.add(library)

    for cell_group in lib_group.get_groups("cell"):
        _ingest_cell(session, library, cell_group)

    return library


def _ingest_cell(session, library, cell_group):
    cell = Cell(
        library=library,
        name=cell_group.args[0],
        area=unwrap_float(cell_group, "area"),
        cell_leakage_power=unwrap_float(cell_group, "cell_leakage_power"),
        dont_use=unwrap_bool(cell_group, "dont_use"),
        is_macro_cell=unwrap_bool(cell_group, "is_macro_cell"),
    )
    session.add(cell)

    for pin_group in cell_group.get_groups("pin"):
        _ingest_pin(session, cell, pin_group)


def _ingest_pin(session, cell, pin_group):
    pin = Pin(
        cell=cell,
        name=pin_group.args[0],
        direction=unwrap_str(pin_group, "direction"),
        capacitance=unwrap_float(pin_group, "capacitance"),
        function=unwrap_str(pin_group, "function"),
    )
    session.add(pin)

    for timing_group in pin_group.get_groups("timing"):
        _ingest_timing(session, pin, timing_group)


def _ingest_timing(session, pin, timing_group):
    arc = TimingArc(
        pin=pin,
        related_pin=unwrap_str(timing_group, "related_pin"),
        timing_sense=unwrap_str(timing_group, "timing_sense"),
        timing_type=unwrap_str(timing_group, "timing_type"),
        when_condition=unwrap_str(timing_group, "when"),
    )
    session.add(arc)

    for table_type in NLDM_TABLE_TYPES:
        for table_group in timing_group.get_groups(table_type):
            _ingest_nldm_table(session, arc, table_type, table_group)

    for table_type in CCS_TABLE_TYPES:
        for table_group in timing_group.get_groups(table_type):
            _ingest_ccs_table(session, arc, table_type, table_group)


def _ingest_nldm_table(session, arc, table_type, table_group):
    index_1 = array_1d(table_group, "index_1")
    index_2 = array_1d(table_group, "index_2")
    values = array_2d(table_group, "values")

    table = NLDMTable(
        timing_arc=arc,
        table_type=table_type,
        index_1_json=json.dumps(index_1) if index_1 is not None else None,
        index_2_json=json.dumps(index_2) if index_2 is not None else None,
        values_json=json.dumps(values) if values is not None else None,
        num_index_1=len(index_1) if index_1 is not None else None,
        num_index_2=len(index_2) if index_2 is not None else None,
        num_value_rows=len(values) if values is not None else None,
        num_value_cols=len(values[0]) if values else None,
    )
    session.add(table)


def _ingest_ccs_table(session, arc, table_type, table_group):
    index_1 = array_1d(table_group, "index_1")
    index_2 = array_1d(table_group, "index_2")
    vector_groups = table_group.get_groups("vector")

    table = CCSTable(
        timing_arc=arc,
        table_type=table_type,
        index_1_json=json.dumps(index_1) if index_1 is not None else None,
        index_2_json=json.dumps(index_2) if index_2 is not None else None,
        num_index_1=len(index_1) if index_1 is not None else None,
        num_index_2=len(index_2) if index_2 is not None else None,
        num_vectors=len(vector_groups),
    )
    session.add(table)

    for i, vector_group in enumerate(vector_groups):
        v_index_1 = array_1d(vector_group, "index_1")
        v_index_2 = array_1d(vector_group, "index_2")
        v_index_3 = array_1d(vector_group, "index_3")
        v_values = array_1d(vector_group, "values")

        vector = CCSVector(
            ccs_table=table,
            vector_index=i,
            index_1_value=v_index_1[0] if v_index_1 else None,
            index_2_value=v_index_2[0] if v_index_2 else None,
            reference_time=unwrap_float(vector_group, "reference_time"),
            index_3_json=json.dumps(v_index_3) if v_index_3 is not None else None,
            values_json=json.dumps(v_values) if v_values is not None else None,
            num_index_3_samples=len(v_index_3) if v_index_3 is not None else None,
            num_value_samples=len(v_values) if v_values is not None else None,
        )
        session.add(vector)
