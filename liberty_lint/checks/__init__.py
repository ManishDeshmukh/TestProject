from ..models import CheckResult
from .base import Check, Violation
from .ccs import ALL_CCS_CHECKS
from .nldm import ALL_NLDM_CHECKS

ALL_CHECKS = ALL_CCS_CHECKS + ALL_NLDM_CHECKS
CHECKS_BY_ID = {c.rule_id: c for c in ALL_CHECKS}


def run_checks(session, library_id=None, rule_ids=None, persist=True):
    """Run the given (or all) checks and optionally persist results as CheckResult rows.

    Returns the list of Violation objects found.
    """
    checks = ALL_CHECKS if not rule_ids else [CHECKS_BY_ID[r] for r in rule_ids]

    violations = []
    for check in checks:
        violations.extend(check.run(session, library_id=library_id))

    if persist and library_id is not None:
        session.query(CheckResult).filter(CheckResult.library_id == library_id).delete()
        for v in violations:
            session.add(CheckResult(
                library_id=library_id,
                rule_id=v.rule_id,
                severity=v.severity,
                object_type=v.object_type,
                object_id=v.object_id,
                object_label=v.object_label,
                message=v.message,
            ))
        session.commit()

    return violations
