# JobPilot — Autonomous LSF Job Management

JobPilot turns manual LSF job management into a **closed-loop, self-learning,
agentic AI system** for air-gapped VLSI/EDA Linux environments. Engineers replace
`bsub` with `jp submit`; everything else — monitoring, root-cause analysis,
auto-restart, resource/TAT prediction, license intelligence, and a conversational
chatbot — runs autonomously.

```
bsub  -J top -q normal  dc_shell -f run.tcl     →     jp submit -J top -q normal  dc_shell -f run.tcl
```

## Why it's runnable anywhere

JobPilot is written **stdlib-first**. Every external dependency is optional and
degrades gracefully, so the whole closed loop runs on a bare Python 3.9+
interpreter and lights up extra capability when the backing service is present:

| Capability        | Production backend            | Fallback when absent                       |
|-------------------|-------------------------------|--------------------------------------------|
| Job execution     | LSF (`bsub`/`bjobs`/`bkill`)  | Built-in **simulator** (PEND→RUN→DONE/EXIT)|
| Failure RCA       | Ollama (`qwen2.5-coder:32b`)  | Deterministic **rule engine**              |
| Prediction        | scikit-learn / XGBoost        | Historical percentiles → `TOOL_DEFAULTS`   |
| Dashboard         | FastAPI + uvicorn + WebSocket | stdlib `http.server` + HTTP `POST /api/chat`|
| Central warm tier | PostgreSQL                    | Silent no-op (local SQLite is live state)  |
| License intel     | FlexLM `lmstat` / checker     | Level 0 (silent), additive when configured |

## Quick start (no dependencies required)

```bash
# 1. First-run wizard — pick a fast scratch/project disk for runtime data
./jobpilot/jp init

# 2. Watch the full closed loop on simulated jobs
./jobpilot/jp demo -n 6          # seeds jobs, opens dashboard at :8765

# 3. Submit a real job (replaces bsub)
./jobpilot/jp submit -J top -q normal -M 32000 dc_shell -f run.tcl

# 4. Ask the chatbot in plain English
./jobpilot/jp chat
#   you> why did the last calibre job fail?
#   you> license status

# 5. Status / dashboard / nightly retrain
./jobpilot/jp status
./jobpilot/jp dashboard
./jobpilot/jp retrain
```

The dashboard is a single self-contained `index.html` (all CSS/JS inlined, **no
CDN, no npm** — air-gap safe) with five tabs: Live Jobs, Analytics, Agents,
History, and Chat.

## Production install (air-gapped)

```bash
# On an internet-connected machine:
pip download -r jobpilot/requirements.txt -d ./jp_packages/
# Transfer jp_packages/ to the air-gapped host, then:
pip install --no-index --find-links=./jp_packages/ -r jobpilot/requirements.txt
```

Optionally pull the LLM models on the GPU host:
`ollama pull qwen2.5-coder:32b && ollama pull qwen3:8b`.

## Architecture

The **Coordinator** owns the global job state machine and makes every decision;
agents are stateless workers that never call each other and share state only
through the **Database Agent** gateway.

```
                         ┌──────────────────────────────┐
   jp submit ──────────► │         COORDINATOR          │ ◄── dashboard / chatbot
                         │  state machine · audit trail │
                         └──────────────┬───────────────┘
        ┌───────────────┬──────────────┼──────────────┬───────────────┐
    Monitor         Discovery     EarlyWarning      Analysis        Prediction
   (bjobs poll)    (job trees)    (in-flight risk)  (LLM RCA)      (ML + license)
        │               │              │                │               │
    Restart          Alert          License          Chatbot        Database Agent
  (loop-guarded)  (terminal+log)  (optional, L0-3)  (22 intents)   (SQLite + PG sync)
```

### Closed-loop intelligence layers
- **Prescriptive** (pre-submit): predict TAT/memory/disk, detect required
  licenses, query live availability, run pre-flight risk checks.
- **Predictive** (in-flight): memory-slope snapshots at T+2/5/10 min, ETA to
  limit, warn before LSF kills the job.
- **Reactive** (post-failure): feature extraction from `bjobs -l` (never the job
  name), advisory LLM RCA, loop-guarded auto-restart with corrected params.
- **Learning** (continuous): completed jobs sync to central PostgreSQL, nightly
  warm-start retrain on **`actual_compute_sec`** (license wait excluded).

## Design principles enforced in code

`P1` air-gap first · `P2` local SQLite is live state · `P3` LLM is advisory ·
`P4` mandatory 3-check restart loop guard · `P5` wrapper jobs are first-class
(job trees) · `P6` predict from `bjobs -l` signals, never the job name ·
`P7` single dashboard instance (BroadcastChannel) · `P8` models kept forever,
raw rotated · `P9` each agent independently deployable · `P10` bsub logged
before execution · `P11` license agent optional, graceful degradation ·
`P12` configurable data path, never assume home.

## Layout

```
jobpilot/
├── jp                  CLI launcher (replaces bsub)
├── cli.py              init / submit / status / dashboard / chat / demo / retrain
├── coordinator.py      orchestrator + state machine + snapshot collectors
├── config.py           path resolution (P12) + config.ini
├── constants.py        TOOL_PATTERNS, LICENSE_MAP, TOOL_DEFAULTS, INTENTS
├── lsf.py              LSF wrapper + simulator
├── agents/             base + 10 agents (monitor, discovery, earlywarning,
│                       database, analysis, prediction, restart, alert,
│                       license, chatbot)
├── ml/                 features, predict, train, lifecycle (nightly retrain)
├── db/                 local SQLite (schema.sql) + central PostgreSQL sync
├── dashboard/          routes (framework-agnostic), server (FastAPI+stdlib),
│                       ws, static/index.html (single file)
└── tests/              pytest suite + stdlib runner (run_standalone.py)
```

## Testing

```bash
pytest jobpilot/tests                      # if pytest is installed
python3 -m jobpilot.tests.run_standalone   # zero-dependency runner
```

29 tests cover tool detection (P6), DB schema, the restart loop guard (P4), the
RCA rule engine, cold-start prediction, all 22 chatbot intents, license levels
(P11), discovery job trees (P5), and the dashboard API.
```
