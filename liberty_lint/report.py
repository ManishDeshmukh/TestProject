"""Generate summary reports from persisted check_result rows."""
import csv
import html

from .models import CheckResult


def _fetch_results(session, library_id):
    q = session.query(CheckResult)
    if library_id is not None:
        q = q.filter(CheckResult.library_id == library_id)
    return q.order_by(CheckResult.rule_id, CheckResult.object_label).all()


def write_csv(session, output_path, library_id=None):
    results = _fetch_results(session, library_id)
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["rule_id", "severity", "object_type", "object_label", "message"])
        for r in results:
            writer.writerow([r.rule_id, r.severity, r.object_type, r.object_label, r.message])
    return len(results)


def write_html(session, output_path, library_id=None):
    results = _fetch_results(session, library_id)

    counts = {}
    for r in results:
        counts[r.rule_id] = counts.get(r.rule_id, 0) + 1

    rows = "\n".join(
        f"<tr class='{html.escape(r.severity)}'>"
        f"<td>{html.escape(r.rule_id)}</td>"
        f"<td>{html.escape(r.severity)}</td>"
        f"<td>{html.escape(r.object_label or '')}</td>"
        f"<td>{html.escape(r.message or '')}</td></tr>"
        for r in results
    )
    summary_rows = "\n".join(
        f"<tr><td>{html.escape(rule_id)}</td><td>{count}</td></tr>"
        for rule_id, count in sorted(counts.items())
    )

    document = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>liberty-lint report</title>
<style>
body {{ font-family: sans-serif; margin: 2em; }}
table {{ border-collapse: collapse; width: 100%; margin-bottom: 2em; }}
th, td {{ border: 1px solid #ccc; padding: 4px 8px; text-align: left; }}
tr.error {{ background: #fde8e8; }}
tr.warning {{ background: #fff8e1; }}
</style>
</head>
<body>
<h1>liberty-lint report</h1>
<p>{len(results)} violation(s) found.</p>
<h2>Summary by rule</h2>
<table><tr><th>Rule</th><th>Count</th></tr>
{summary_rows}
</table>
<h2>Details</h2>
<table><tr><th>Rule</th><th>Severity</th><th>Object</th><th>Message</th></tr>
{rows}
</table>
</body>
</html>
"""
    with open(output_path, "w") as f:
        f.write(document)
    return len(results)
