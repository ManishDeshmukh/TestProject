"""SQLAlchemy ORM schema for parsed Liberty (.lib) data.

Array-valued attributes (index/values tables) are stored as JSON text
columns alongside precomputed length columns, so checks can filter/group
in SQL without re-parsing JSON for the common "how many samples" questions.
"""
import datetime
import json

from sqlalchemy import (
    Column, Integer, Float, String, Boolean, ForeignKey, DateTime, Text,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class JSONArrayMixin:
    """Helpers for columns that store a JSON-encoded list of floats."""

    @staticmethod
    def dump(values):
        return None if values is None else json.dumps(list(values))

    @staticmethod
    def load(text):
        return None if text is None else json.loads(text)


class Library(Base):
    __tablename__ = "library"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    technology = Column(String)
    delay_model = Column(String)
    time_unit = Column(String)
    voltage_unit = Column(String)
    current_unit = Column(String)
    nom_process = Column(Float)
    nom_voltage = Column(Float)
    nom_temperature = Column(Float)
    ingested_at = Column(DateTime, default=datetime.datetime.utcnow)

    cells = relationship("Cell", back_populates="library", cascade="all, delete-orphan")


class Cell(Base):
    __tablename__ = "cell"

    id = Column(Integer, primary_key=True)
    library_id = Column(Integer, ForeignKey("library.id"), nullable=False)
    name = Column(String, nullable=False)
    area = Column(Float)
    cell_leakage_power = Column(Float)
    dont_use = Column(Boolean)
    is_macro_cell = Column(Boolean)

    library = relationship("Library", back_populates="cells")
    pins = relationship("Pin", back_populates="cell", cascade="all, delete-orphan")


class Pin(Base):
    __tablename__ = "pin"

    id = Column(Integer, primary_key=True)
    cell_id = Column(Integer, ForeignKey("cell.id"), nullable=False)
    name = Column(String, nullable=False)
    direction = Column(String)
    capacitance = Column(Float)
    function = Column(String)

    cell = relationship("Cell", back_populates="pins")
    timing_arcs = relationship("TimingArc", back_populates="pin", cascade="all, delete-orphan")


class TimingArc(Base):
    """One `timing()` group on an output pin (a single related_pin/when/timing_type combination)."""

    __tablename__ = "timing_arc"

    id = Column(Integer, primary_key=True)
    pin_id = Column(Integer, ForeignKey("pin.id"), nullable=False)
    related_pin = Column(String)
    timing_sense = Column(String)
    timing_type = Column(String)
    when_condition = Column(String)

    pin = relationship("Pin", back_populates="timing_arcs")
    nldm_tables = relationship("NLDMTable", back_populates="timing_arc", cascade="all, delete-orphan")
    ccs_tables = relationship("CCSTable", back_populates="timing_arc", cascade="all, delete-orphan")


class NLDMTable(Base, JSONArrayMixin):
    """A `table_lookup` style 2D table: cell_rise, cell_fall, rise_transition, fall_transition, ..."""

    __tablename__ = "nldm_table"

    id = Column(Integer, primary_key=True)
    timing_arc_id = Column(Integer, ForeignKey("timing_arc.id"), nullable=False)
    table_type = Column(String, nullable=False)

    index_1_json = Column(Text)   # input net transition grid
    index_2_json = Column(Text)   # output capacitance grid
    values_json = Column(Text)    # row-major flattened matrix

    num_index_1 = Column(Integer)
    num_index_2 = Column(Integer)
    num_value_rows = Column(Integer)
    num_value_cols = Column(Integer)

    timing_arc = relationship("TimingArc", back_populates="nldm_tables")

    @property
    def index_1(self):
        return self.load(self.index_1_json)

    @property
    def index_2(self):
        return self.load(self.index_2_json)

    @property
    def value_rows(self):
        return self.load(self.values_json)


class CCSTable(Base, JSONArrayMixin):
    """A CCS current table: output_current_rise or output_current_fall."""

    __tablename__ = "ccs_table"

    id = Column(Integer, primary_key=True)
    timing_arc_id = Column(Integer, ForeignKey("timing_arc.id"), nullable=False)
    table_type = Column(String, nullable=False)

    index_1_json = Column(Text)  # declared input net transition (slew) grid
    index_2_json = Column(Text)  # declared total output net capacitance (load) grid

    num_index_1 = Column(Integer)
    num_index_2 = Column(Integer)
    num_vectors = Column(Integer)

    timing_arc = relationship("TimingArc", back_populates="ccs_tables")
    vectors = relationship("CCSVector", back_populates="ccs_table", cascade="all, delete-orphan")

    @property
    def index_1(self):
        return self.load(self.index_1_json)

    @property
    def index_2(self):
        return self.load(self.index_2_json)


class CCSVector(Base, JSONArrayMixin):
    """One `vector()` sub-group: the current waveform for a single (slew, load) point."""

    __tablename__ = "ccs_vector"

    id = Column(Integer, primary_key=True)
    ccs_table_id = Column(Integer, ForeignKey("ccs_table.id"), nullable=False)
    vector_index = Column(Integer, nullable=False)

    index_1_value = Column(Float)  # this vector's slew point
    index_2_value = Column(Float)  # this vector's load point
    reference_time = Column(Float)

    index_3_json = Column(Text)  # time samples
    values_json = Column(Text)   # current samples

    num_index_3_samples = Column(Integer)
    num_value_samples = Column(Integer)

    ccs_table = relationship("CCSTable", back_populates="vectors")

    @property
    def index_3(self):
        return self.load(self.index_3_json)

    @property
    def values(self):
        return self.load(self.values_json)


class CheckResult(Base):
    """Persisted outcome of running a check rule against a library in the DB."""

    __tablename__ = "check_result"

    id = Column(Integer, primary_key=True)
    library_id = Column(Integer, ForeignKey("library.id"), nullable=False)
    rule_id = Column(String, nullable=False)
    severity = Column(String, nullable=False)
    object_type = Column(String)
    object_id = Column(Integer)
    object_label = Column(String)
    message = Column(Text)
    run_at = Column(DateTime, default=datetime.datetime.utcnow)
