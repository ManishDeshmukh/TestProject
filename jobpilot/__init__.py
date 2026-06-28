"""JobPilot — Autonomous, self-learning LSF job management for air-gapped VLSI/EDA.

A closed-loop agentic AI platform that replaces ``bsub`` with ``jp submit`` and
autonomously monitors, analyses, restarts, predicts and chats about LSF jobs.

The package is written stdlib-first: every external dependency (FastAPI, scikit
-learn, XGBoost, Ollama, PostgreSQL, LSF itself) is optional and degrades
gracefully so the system runs end-to-end on a developer workstation and lights
up additional capability when those services are present.
"""

__version__ = "3.0.0"
__all__ = ["__version__"]
