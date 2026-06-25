from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Violation:
    rule_id: str
    severity: str  # "error" | "warning"
    object_type: str
    object_id: int
    object_label: str
    message: str


class Check:
    """Base class for a lint rule. Subclasses implement `run`."""

    rule_id: str = ""
    description: str = ""
    severity: str = "error"

    def run(self, session, library_id: Optional[int] = None) -> List[Violation]:
        raise NotImplementedError

    def violation(self, object_type, object_id, object_label, message) -> Violation:
        return Violation(
            rule_id=self.rule_id,
            severity=self.severity,
            object_type=object_type,
            object_id=object_id,
            object_label=object_label,
            message=message,
        )
