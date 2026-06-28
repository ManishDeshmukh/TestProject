"""Prediction Agent — pre-submission predictions + accuracy tracking.

Predicts compute time and memory from command-derived features (P6) and, when the
license level is >= 2, adds a license-wait component so TAT is decomposed into
``compute + license_wait + queue_pend``.  A confidence label is always returned.
"""

from __future__ import annotations

from .base import BaseAgent, ok
from ..ml.predict import Predictor


class PredictionAgent(BaseAgent):
    def __init__(self, db_agent, config):
        super().__init__(db_agent, config)
        self.predictor = Predictor(db_agent, config)

    def execute(self, action: str, payload: dict) -> dict:
        if action == "predict":
            return ok(self.predict_resources(payload["job"], payload.get("license_agent")))
        if action == "record_actuals":
            return self.record_actuals(payload["job"])
        return ok(None)

    def predict_resources(self, job: dict, license_agent=None):
        compute_sec, mem_mb, conf, n = self.predictor.predict_compute_and_mem(job)
        compute_min = round(compute_sec / 60.0, 1)

        out = {
            "predicted_compute_min": compute_min,
            "predicted_mem_mb": mem_mb,
            "predicted_license_wait_p50": None,
            "predicted_license_wait_p90": None,
            "predicted_total_p50_min": compute_min,
            "includes_license_wait": False,
            "license_confidence": "UNAVAILABLE",
            "confidence": conf,
            "sample_count": n,
            "note": "",
        }

        # License-aware decomposition (Level >= 2).
        if license_agent is not None:
            lic = license_agent.predict_wait(job)
            if lic and lic.get("p50_wait_sec") is not None:
                p50 = round(lic["p50_wait_sec"] / 60.0, 1)
                p90 = round(lic["p90_wait_sec"] / 60.0, 1)
                out.update({
                    "predicted_license_wait_p50": p50,
                    "predicted_license_wait_p90": p90,
                    "predicted_total_p50_min": round(compute_min + p50, 1),
                    "includes_license_wait": True,
                    "license_confidence": lic.get("confidence", "LOW"),
                })
            else:
                out["note"] = "License data building — estimates available soon"

        if conf == "NONE":
            out["note"] = (out["note"] + " | Using tool defaults (cold start)").strip(" |")

        # Log the prediction for later accuracy comparison. During pre-flight the
        # job row does not exist yet, so use NULL job_id (FK allows it).
        jid = job.get("job_id")
        if jid and not self.db.get_job(jid):
            jid = None
        self.db.write_prediction({
            "job_id": jid,
            "predicted_compute_sec": compute_sec,
            "predicted_mem_mb": mem_mb,
            "predicted_license_wait": int((out["predicted_license_wait_p50"] or 0) * 60),
            "predicted_total_sec": int(out["predicted_total_p50_min"] * 60),
            "suggested_queue": job.get("queue"),
            "prediction_confidence": conf,
            "license_confidence": out["license_confidence"],
        })
        return out

    def record_actuals(self, job: dict):
        self.db.update_prediction_actuals(
            job["job_id"],
            actual_compute_sec=job.get("actual_compute_sec"),
            actual_mem_mb=job.get("mem_peak_mb"),
            actual_total_sec=job.get("runtime_sec"),
            actual_queue=job.get("queue"),
        )
        return ok(True)
