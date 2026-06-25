---
name: liberty-lint
description: Use this skill when working with Synopsys Liberty (.lib) standard-cell files in this repository — parsing them, loading them into the liberty_lint SQLite database, or running/adding lint checks (especially CCS output_current_rise/output_current_fall sample-count, grid, monotonicity, causality checks, or NLDM table-lookup checks). Trigger for requests like "check this .lib file", "parse this liberty file", "why did CCS00x fail", "add a new Liberty check", or "generate a report from the check results".
---

# liberty-lint

A tool in this repo (`liberty_lint/`) that parses Synopsys Liberty (`.lib`)
standard-cell files, loads them into a SQLite database via SQLAlchemy, and
runs lint checks against the stored data — with a focus on CCS
(`output_current_rise`/`output_current_fall`) current-waveform tables, plus
a few NLDM table-lookup checks.

## Architecture

```
.lib file
   │  liberty_parser.parse_multi_liberty()  (PyPI: liberty-parser)
   ▼
Group/Attribute tree  (generic, library/cell/pin/timing/... groups)
   │  liberty_lint/ingest.py
   ▼
SQLite DB via SQLAlchemy  (liberty_lint/models.py)
   Library -> Cell -> Pin -> TimingArc -> { NLDMTable, CCSTable -> CCSVector }
   │  liberty_lint/checks/*.py (query the DB)
   ▼
Violation list -> printed + persisted to check_result table
   │  liberty_lint/report.py
   ▼
CSV / HTML report
```

Index/value arrays (e.g. `index_3`, `values`) are stored as JSON columns
**alongside precomputed length columns** (`num_index_3_samples`,
`num_value_samples`, `num_vectors`, ...) specifically so the common
"how many samples" questions can be answered directly in SQL without
decoding JSON first.

## Running it

```bash
source .venv/bin/activate   # or: pip install -e ".[dev]"

liberty-lint ingest path/to/cells.lib                       # default db: sqlite:///liberty.db
liberty-lint check --library-name <lib_name>                # exits 1 if any error-severity violation
liberty-lint check --library-name <lib_name> --rules CCS001,CCS002
liberty-lint report --library-name <lib_name> --format html --output report.html
liberty-lint list-checks
```

`--db sqlite:///path.db` works on every subcommand if you don't want the
default `liberty.db` in the cwd.

## Check catalog

| Rule    | Severity | What it catches |
|---------|----------|------------------|
| CCS001  | error    | A CCS vector's `index_3` (time) sample count != its `values` (current) sample count — the core "number of samples" check. |
| CCS002  | warning  | Vectors within one CCS table have inconsistent sample counts. |
| CCS003  | error    | CCS table doesn't have exactly one vector per `(index_1, index_2)` grid point — missing or duplicate combinations. |
| CCS004  | error    | `index_1`/`index_2` grids, or a vector's `index_3` time axis, are not strictly increasing. |
| CCS005  | warning  | A vector's time axis starts at a negative value. |
| CCS006  | error    | Current waveform contains non-finite values (NaN/Inf). |
| CCS007  | warning  | A vector's current doesn't start near zero relative to its peak (causality/quiescent-baseline sanity). |
| CCS008  | warning  | `output_current_rise` and `output_current_fall` on the same timing arc use different `index_1`/`index_2` grids. |
| NLDM001 | error    | NLDM `values` matrix shape doesn't match `index_1`/`index_2` lengths. |
| NLDM002 | error    | NLDM `index_1`/`index_2` grid is not strictly increasing. |

Rule IDs, severities, and descriptions live as class attributes in
`liberty_lint/checks/ccs.py` and `liberty_lint/checks/nldm.py` — read those
files directly when debugging why a specific rule fired, the `_label()`
helper in each file builds the human-readable `object_label` shown in
output (`<cell>.<pin> (related_pin=...) / <table_type>`).

## Debugging a failing check

1. Find the rule in `liberty_lint/checks/ccs.py` or `nldm.py` by its
   `rule_id` to see exactly what it compares.
2. Query the DB directly — the precomputed columns make this fast, e.g.:
   ```sql
   SELECT id, vector_index, num_index_3_samples, num_value_samples
   FROM ccs_vector WHERE num_index_3_samples != num_value_samples;
   ```
3. Cross-reference against `check_result` (persisted after `check` runs)
   for the exact message/object that was flagged.

## Adding a new check

1. Subclass `liberty_lint.checks.base.Check` in `ccs.py` or `nldm.py` (or a
   new file under `liberty_lint/checks/`).
2. Set `rule_id` (next free `CCSxxx`/`NLDMxxx`), `severity`
   (`"error"`/`"warning"`), and `description`.
3. Implement `run(self, session, library_id=None) -> List[Violation]`,
   querying the ORM models in `liberty_lint/models.py`. Use the existing
   `_ccs_table_query`/`_nldm_table_query`/`_label` helpers for consistency.
4. Register the instance in `ALL_CCS_CHECKS` / `ALL_NLDM_CHECKS` (and import
   it in `liberty_lint/checks/__init__.py` if it's a new module).
5. Add cases to `tests/fixtures/sample.lib` that should and shouldn't
   trigger it, then a test in `tests/test_checks.py` asserting on
   `run_checks(session, library_id=lib.id, rule_ids=["YOURRULE"])`.
6. If the new rule should pass on the CI smoke-test library, mirror any
   needed "good" cell data into `tests/fixtures/clean.lib` too.

## Test fixtures

- `tests/fixtures/sample.lib` — synthetic library with a "good" and "bad"
  cell for both NLDM and CCS; every check has at least one fixture case
  that triggers it and one that doesn't.
- `tests/fixtures/clean.lib` — only the good cells; used by CI to assert
  `liberty-lint check` exits 0 on a clean library.

Run `pytest tests/ -v` after any change to the parser, schema, or checks.

## Schema reference (liberty_lint/models.py)

- `Library` — one row per `library()` group (name, units, nominal PVT).
- `Cell` — `area`, `cell_leakage_power`, `dont_use`, `is_macro_cell`.
- `Pin` — `direction`, `capacitance`, `function`.
- `TimingArc` — one `timing()` group: `related_pin`, `timing_sense`,
  `timing_type`, `when_condition`.
- `NLDMTable` — `cell_rise`/`cell_fall`/`*_transition`/`*_constraint`
  tables: `index_1_json`, `index_2_json`, `values_json` + length columns.
- `CCSTable` — `output_current_rise`/`output_current_fall`: declared
  `index_1`/`index_2` grid + `num_vectors`.
- `CCSVector` — one `vector()` sub-group: `index_1_value`, `index_2_value`,
  `reference_time`, `index_3_json` (time), `values_json` (current) + length
  columns `num_index_3_samples`/`num_value_samples`.
- `CheckResult` — persisted violations (`rule_id`, `severity`,
  `object_type`/`object_id`/`object_label`, `message`) per library.

## Useful background

CCS `output_current_rise`/`output_current_fall` tables hold one current
waveform (`vector`) per `(input_net_transition, total_output_net_capacitance)`
grid point, each with its own `index_3` (time) and `values` (current)
arrays. STA/delay-calc tools generally expect each vector's `index_3` and
`values` to have matching, and often uniform, lengths — that's exactly
what CCS001/CCS002 check. NLDM `cell_rise`/`cell_fall` tables hold a single
2D delay matrix indexed by `index_1` (input slew) x `index_2` (output
load) — NLDM001/NLDM002 check that matrix's shape and grid ordering.
