# liberty-lint

A Python tool that parses Synopsys Liberty (`.lib`) standard-cell files,
loads the data into a SQLite database, and runs lint checks against it —
with a focus on CCS (`output_current_rise`/`output_current_fall`) current
waveform tables, plus a few NLDM table-lookup checks.

## How it works

1. **Parse** — `.lib` text is parsed into a generic `Group`/`Attribute` tree
   using the [`liberty-parser`](https://pypi.org/project/liberty-parser/)
   package.
2. **Ingest** (`liberty_lint/ingest.py`) — the tree is walked and loaded into
   a relational schema (`liberty_lint/models.py`, SQLAlchemy ORM):
   `Library -> Cell -> Pin -> TimingArc -> {NLDMTable, CCSTable -> CCSVector}`.
   Index/value arrays are stored as JSON columns alongside precomputed
   length columns (e.g. `num_index_3_samples`, `num_value_samples`), so the
   most common questions can be answered directly in SQL.
3. **Check** (`liberty_lint/checks/`) — each rule is a small class that
   queries the database for one specific defect. Results can be printed and
   are also persisted to a `check_result` table.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

```bash
# Parse a .lib file into the database (default: sqlite:///liberty.db)
liberty-lint ingest path/to/cells.lib

# Run all checks against the ingested library
liberty-lint check --library-name my_lib

# Run a subset of rules
liberty-lint check --library-name my_lib --rules CCS001,CCS002

# List available rules
liberty-lint list-checks
```

`check` exits non-zero if any error-severity violation is found, so it can
be used as a CI gate.

## Checks

| Rule    | Severity | Description |
|---------|----------|--------------------------------------------------------------|
| CCS001  | error    | A CCS vector's `index_3` (time) sample count must equal its `values` (current) sample count. |
| CCS002  | warning  | All vectors in one CCS table should share the same sample count. |
| CCS003  | error    | A CCS table must have exactly one vector per `(index_1, index_2)` grid point — no missing or duplicate combinations. |
| CCS004  | error    | `index_1`/`index_2` grids and each vector's `index_3` time axis must be strictly increasing. |
| CCS005  | warning  | A vector's time axis should start at a non-negative value. |
| CCS006  | error    | Current waveform samples must be finite (no NaN/Inf). |
| NLDM001 | error    | An NLDM table's `values` matrix shape must match its `index_1`/`index_2` lengths. |
| NLDM002 | error    | NLDM `index_1`/`index_2` grids must be strictly increasing. |

New checks are added by subclassing `liberty_lint.checks.base.Check` and
registering the instance in `ALL_CCS_CHECKS`/`ALL_NLDM_CHECKS`.

## Tests

```bash
pytest tests/
```

`tests/fixtures/sample.lib` is a small synthetic library with a "good" and
a "bad" cell for both NLDM and CCS, used to exercise every check.
