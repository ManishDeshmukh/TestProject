"""Framework-agnostic API logic.

Each method returns a plain JSON-serialisable structure so the *same* logic backs
both the FastAPI server (production) and the stdlib ``http.server`` fallback (dev /
air-gapped without pip).  All data comes from the local SQLite hot tier.
"""

from __future__ import annotations

import json


class DashboardAPI:
    def __init__(self, coordinator):
        self.co = coordinator
        self.db = coordinator.db

    # ── /api/summary ──────────────────────────────────────────────────────────
    def summary(self):
        jobs = self.db.get_all_jobs(limit=1000)
        counts = {"total": len(jobs), "pending": 0, "running": 0, "done": 0,
                  "failed": 0, "restarted": 0, "warnings": 0}
        for j in jobs:
            s = j["status"]
            if s == "PEND":
                counts["pending"] += 1
            elif s == "RUN":
                counts["running"] += 1
            elif s == "DONE":
                counts["done"] += 1
            elif s in ("EXIT", "FAILED_FINAL", "FAILED_BUDGET"):
                counts["failed"] += 1
            elif s == "RESTARTED":
                counts["restarted"] += 1
            elif s == "WARNED":
                counts["warnings"] += 1
        counts.update({
            "license_level": self.co.license_level, "user": self.co.user,
            "host": self.co.host, "coordinator_pid": self.co.pid,
        })
        return counts

    def queues(self):
        return self.db.latest_queues(5)

    def disks(self):
        return self.db.latest_disks(5)

    def licenses(self):
        if self.co.license_level == 0:
            return []
        rows = self.db.latest_license_snapshots(5)
        out = []
        for r in rows:
            total = r.get("total") or 0
            util = (r["in_use"] / total) if total else 0
            risk = ("BLOCKED" if r["available"] == 0 else
                    "HIGH" if util >= 0.85 else "MEDIUM" if util >= 0.6 else "LOW")
            out.append({**r, "risk": risk, "util_pct": round(util * 100, 1)})
        return out

    # ── /api/jobs (job trees) ─────────────────────────────────────────────────
    def jobs(self, page=1, status=None, queue=None):
        page = max(int(page), 1)
        roots, total = self.db.get_root_jobs(limit=25, offset=(page - 1) * 25,
                                             status=status, queue=queue)
        out = []
        for r in roots:
            node = self._job_node(r)
            node["children"] = [self._job_node(c) for c in self.db.get_children(r["job_id"])]
            out.append(node)
        return {"jobs": out, "total": total, "page": page,
                "pages": (total + 24) // 25 if total else 1}

    @staticmethod
    def _job_node(j):
        return {
            "job_id": j["job_id"], "job_name": j.get("job_name"), "status": j["status"],
            "tool": j.get("tool"), "queue": j.get("queue"),
            "mem_peak_mb": j.get("mem_peak_mb"), "mem_requested_mb": j.get("mem_requested_mb"),
            "actual_compute_sec": j.get("actual_compute_sec"),
            "restart_count": j.get("restart_count"), "depth": j.get("job_depth", 0),
        }

    def analysis(self, limit=8):
        rows = self.db.get_recent_analyses(limit)
        out = []
        for a in rows:
            out.append({
                "job_id": a["job_id"], "tool": a.get("tool"),
                "error_category": a.get("error_category"), "root_cause": a.get("root_cause"),
                "license_contributed": bool(a.get("license_contributed")),
                "confidence": a.get("confidence"),
            })
        return out

    def warnings(self):
        rows = self.db.get_early_warnings()
        return [{
            "job_id": r["job_id"], "tool": r.get("tool"), "risk_level": r["risk_level"],
            "mem_util_pct": r["mem_util_pct"], "eta_to_limit_min": r.get("eta_to_limit_min"),
            "suggested_mem_mb": int((r.get("mem_requested_mb") or 0) * 1.5) or None,
        } for r in rows]

    def agents(self):
        live = {a["name"]: a for a in self.db.agent_summary()}
        names = ["Coordinator", "MonitorAgent", "DiscoveryAgent", "EarlyWarningAgent",
                 "DatabaseAgent", "AnalysisAgent", "PredictionAgent", "RestartAgent",
                 "AlertAgent", "LicenseMonitorAgent", "ChatbotAgent"]
        out = []
        for n in names:
            row = live.get(n, {})
            out.append({
                "name": n, "status": "active" if (row.get("errors") or 0) == 0 else "degraded",
                "last_action": row.get("last_action") or "-",
                "jobs_handled": row.get("handled", 0), "errors": row.get("errors", 0),
                "avg_latency_ms": row.get("avg_latency_ms", 0),
            })
        return out

    # ── /api/analytics ────────────────────────────────────────────────────────
    def analytics(self):
        jobs = self.db.get_all_jobs(limit=2000)
        done = [j for j in jobs if j["status"] == "DONE"]
        failed = [j for j in jobs if j["status"] in ("EXIT", "FAILED_FINAL", "FAILED_BUDGET")]
        terminal = len(done) + len(failed)
        success_rate = round(len(done) / terminal * 100, 1) if terminal else 0.0
        computes = [j["actual_compute_sec"] for j in done if j.get("actual_compute_sec")]
        avg_tat = round(sum(computes) / len(computes) / 60, 1) if computes else 0.0

        cats = {}
        for a in self.db.get_recent_analyses(200):
            c = a.get("error_category") or "UNKNOWN"
            cats[c] = cats.get(c, 0) + 1
        top_fail = max(cats, key=cats.get) if cats else "-"
        recovered = sum(1 for j in jobs if j["status"] == "RESTARTED")

        queue_dist = {}
        for j in jobs:
            q = j.get("queue") or "default"
            queue_dist[q] = queue_dist.get(q, 0) + 1

        # TAT breakdown: compute vs license wait.
        lic_rows = self.db._query(
            "SELECT AVG(actual_compute_sec) c, AVG(license_wait_sec) w FROM job_license_usage")
        tat_breakdown = {"compute_sec": 0, "license_wait_sec": 0}
        if lic_rows and lic_rows[0]["c"]:
            tat_breakdown = {"compute_sec": int(lic_rows[0]["c"] or 0),
                             "license_wait_sec": int(lic_rows[0]["w"] or 0)}

        return {
            "success_rate": success_rate, "avg_tat_min": avg_tat,
            "top_fail_reason": top_fail, "auto_recovered": recovered,
            "fail_cats": cats, "queue_dist": queue_dist,
            "tat_breakdown": tat_breakdown,
            "mem_accuracy": self.db.prediction_accuracy_rows(100),
        }

    def history(self, page=1, tool=None, status=None, days=None):
        jobs = self.db.get_all_jobs(limit=1000)
        if tool:
            jobs = [j for j in jobs if j.get("tool") == tool]
        if status:
            jobs = [j for j in jobs if j.get("status") == status]
        page = max(int(page), 1)
        total = len(jobs)
        page_jobs = jobs[(page - 1) * 25: page * 25]
        rows = []
        for j in page_jobs:
            lic = self.db._query_one(
                "SELECT license_wait_sec FROM job_license_usage WHERE job_id=? LIMIT 1", (j["job_id"],))
            a = self.db.get_analysis_for(j["job_id"])
            rows.append({
                "job_id": j["job_id"], "name": j.get("job_name"), "tool": j.get("tool"),
                "queue": j.get("queue"), "status": j["status"],
                "compute_time": j.get("actual_compute_sec"),
                "license_wait": lic.get("license_wait_sec") if lic else None,
                "mem_peak": j.get("mem_peak_mb"), "restarts": j.get("restart_count"),
                "rca": a.get("error_category") if a else None,
            })
        return {"jobs": rows, "total": total, "page": page, "pages": (total + 24) // 25 or 1}

    # ── chat (HTTP fallback when WebSocket unavailable) ───────────────────────
    def chat(self, text, session="default"):
        return self.co.chatbot.respond(text, session)

    def dispatch(self, path, params):
        """Route a path to a method (used by the stdlib fallback server)."""
        p = path.rstrip("/")
        if p in ("", "/api/summary"):
            return self.summary()
        if p == "/api/queues":
            return self.queues()
        if p == "/api/disks":
            return self.disks()
        if p == "/api/licenses":
            return self.licenses()
        if p == "/api/jobs":
            return self.jobs(params.get("page", 1), params.get("status"), params.get("queue"))
        if p == "/api/analysis":
            return self.analysis(int(params.get("limit", 8)))
        if p == "/api/warnings":
            return self.warnings()
        if p == "/api/agents":
            return self.agents()
        if p == "/api/analytics":
            return self.analytics()
        if p == "/api/history":
            return self.history(params.get("page", 1), params.get("tool"), params.get("status"))
        return None
