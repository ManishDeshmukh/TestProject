"""``jp`` — the JobPilot CLI that replaces ``bsub`` (P14).

Subcommands::

  jp init        first-run wizard: pick a fast data disk, write config.ini
  jp submit      pre-flight + submit + start the autonomous closed loop
  jp status      coordinator + job summary
  jp dashboard   launch the dashboard (and coordinator) in Firefox
  jp chat        terminal chatbot REPL
  jp demo        seed simulated jobs and watch the full loop run
  jp retrain     run the nightly aggregate + model retrain once

Uses ``click``/``rich`` when present and degrades to stdlib ``argparse``/``print``
so it runs on a bare interpreter.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import webbrowser
from pathlib import Path

from .config import (Config, CONFIG_PATH, SESSION_PATH, HOME_DIR, DEFAULTS,
                     detect_scratch_candidates)

try:
    from rich.console import Console  # type: ignore
    _C = Console()
    def out(msg, style=None): _C.print(msg, style=style)
except ImportError:
    def out(msg, style=None): print(msg)


# ─────────────────────────────────────────────────────────────────────────────
def cmd_init(args):
    out("JobPilot first-run setup", "bold")
    candidates = detect_scratch_candidates()
    out("\n  Detected available locations:")
    for i, (path, free, rec) in enumerate(candidates, 1):
        mark = "  ✅ recommended" if rec else ""
        out(f"    {i}. {path:28s} ({free}GB free){mark}")
    out(f"    {len(candidates)+1}. Enter custom path")

    choice = args.path
    if not choice:
        try:
            sel = input(f"\n  Choose [1]: ").strip() or "1"
        except EOFError:
            sel = "1"
        if sel.isdigit() and 1 <= int(sel) <= len(candidates):
            choice = candidates[int(sel) - 1][0]
        elif sel.isdigit() and int(sel) == len(candidates) + 1:
            choice = input("  Custom path: ").strip()
        else:
            choice = sel
    data_path = str(Path(choice) / ".jobpilot")

    cfg = Config()
    for section, kv in DEFAULTS.items():
        for k, v in kv.items():
            cfg.set(section, k, v)
    cfg.set("paths", "data_path", data_path)
    cfg.save()
    # Force-create the tree.
    cfg2 = Config()
    cfg2.log_path; cfg2.models_path; cfg2.snapshots_path
    out("\n  ✅ JobPilot configured.", "green")
    out(f"     Config:  {CONFIG_PATH}")
    out(f"     Data:    {cfg2.data_path}")


def _start_system(open_browser=False):
    """Construct the coordinator, start background daemons + dashboard."""
    from .coordinator import Coordinator
    from .dashboard.server import DashboardServer
    cfg = Config()
    co = Coordinator(cfg)
    co.start_background()
    server = DashboardServer(co)
    server.serve_background()
    SESSION_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_PATH.write_text(json.dumps({"pid": co.pid, "port": server.port}))
    out(co.status_banner(), "bold")
    out(f"  Dashboard backend: {server.backend}")
    if open_browser and cfg.getbool("coordinator", "auto_open_browser", True):
        try:
            webbrowser.get(cfg.get("coordinator", "browser_cmd", "firefox")).open(
                f"http://localhost:{server.port}")
        except Exception:
            webbrowser.open(f"http://localhost:{server.port}")
    return co, server


def _print_preflight(pf):
    p = pf["prediction"]
    out("\n── Pre-flight ─────────────────────────────", "bold")
    out(f"  Tool: {pf['tool'] or 'unknown'} / {pf['flow_stage'] or '-'}")
    out(f"  Predicted memory:  ~{p['predicted_mem_mb']}MB  ({p['confidence']} confidence, {p['sample_count']} samples)")
    if p["includes_license_wait"]:
        out(f"  Compute:       ~{p['predicted_compute_min']}min")
        out(f"  License wait:  ~{p['predicted_license_wait_p50']}min (P50) / {p['predicted_license_wait_p90']}min (P90)")
        out(f"  Total P50:     ~{p['predicted_total_p50_min']}min")
    else:
        out(f"  Predicted compute: ~{p['predicted_compute_min']}min")
    for l in pf.get("license", []):
        icon = "🔴" if l["risk"] in ("BLOCKED", "HIGH") else "✅"
        out(f"  {icon} {l['feature']}: {l['available']}/{l['total']} free [{l['risk']}]")
    if p["note"]:
        out(f"  ℹ️  {p['note']}", "yellow")


def cmd_submit(args):
    co, server = _start_system(open_browser=True)
    command = " ".join(args.command)
    runtime_sec = args.W * 60 if args.W else None
    job_id, pf = co.submit_job(args.J, args.q, command, mem_mb=args.M,
                               runtime_sec=runtime_sec)
    _print_preflight(pf)
    if not job_id:
        out("\n  ✗ Submission failed.", "red"); return
    out(f"\n  ✓ Submitted job {job_id} (queue {args.q}).", "green")
    out("  Coordinator is now monitoring autonomously. Ctrl-C to stop.\n")
    _block(co, server)


def cmd_dashboard(args):
    co, server = _start_system(open_browser=True)
    out("  Dashboard running. Ctrl-C to stop.\n")
    _block(co, server)


def cmd_demo(args):
    co, server = _start_system(open_browser=False)
    out(f"\n  Seeding {args.n} simulated jobs across tools/queues…", "bold")
    samples = [
        ("syn_top", "normal", "dc_shell -f run.tcl"),
        ("pnr_core", "long", "icc2_shell -f place.tcl corner1 corner2"),
        ("drc_full", "verify", "calibre -drc rules.svrf design.spef"),
        ("sta_signoff", "normal", "pt_shell -f sta.tcl corner1 corner2 corner3"),
        ("sim_regress", "sim", "vcs -full64 tb_top.sv"),
        ("synth_blk", "normal", "genus -f syn.tcl"),
    ]
    for i in range(args.n):
        name, queue, cmd = samples[i % len(samples)]
        jid, _ = co.submit_job(f"{name}_{i}", queue, cmd)
        out(f"    → job {jid:>6}  {name}_{i} ({queue})")
        time.sleep(0.05)
    out(f"\n  {co.status_banner()}", "green")
    out("  Watch the closed loop run (submit→run→done/exit→RCA→restart).")
    out("  Open the dashboard URL above. Ctrl-C to stop.\n")
    _block(co, server)


def cmd_status(args):
    if not SESSION_PATH.exists():
        out("  No active JobPilot session (.session not found). Start with 'jp dashboard'.", "yellow")
    else:
        sess = json.loads(SESSION_PATH.read_text())
        alive = _pid_alive(sess.get("pid"))
        out(f"  Coordinator PID {sess.get('pid')}: {'RUNNING' if alive else 'not running'}")
        out(f"  Dashboard port:  {sess.get('port')}")
    # Job summary from DB.
    from .db.local import LocalDB
    cfg = Config()
    db = LocalDB(cfg.db_path)
    jobs = db.get_all_jobs(2000)
    counts = {}
    for jb in jobs:
        counts[jb["status"]] = counts.get(jb["status"], 0) + 1
    out("  Jobs: " + (", ".join(f"{k}={v}" for k, v in counts.items()) or "none"))
    out(f"  Data path: {cfg.data_path}")
    db.close()


def cmd_chat(args):
    from .coordinator import Coordinator
    co = Coordinator(Config())
    out("JobPilot chatbot. Type 'exit' to quit.\n", "bold")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in ("exit", "quit"):
            break
        if not text:
            continue
        res = co.chatbot.respond(text, session="cli")
        out("jp > " + res["text"], "cyan")
    co.stop()


def cmd_retrain(args):
    from .coordinator import Coordinator
    from .ml.lifecycle import nightly_retrain
    co = Coordinator(Config())
    result = nightly_retrain(co.db, co.cfg)
    out(json.dumps(result, indent=2))
    co.stop()


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def _block(co, server):
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        out("\n  Stopping JobPilot…", "yellow")
        co.stop()
        server.stop()


# ─────────────────────────────────────────────────────────────────────────────
def build_parser():
    p = argparse.ArgumentParser(prog="jp", description="JobPilot — autonomous LSF job management")
    sub = p.add_subparsers(dest="cmd")

    pi = sub.add_parser("init", help="first-run setup wizard")
    pi.add_argument("--path", help="data path parent (non-interactive)")
    pi.set_defaults(func=cmd_init)

    ps = sub.add_parser("submit", help="submit a job (replaces bsub)")
    ps.add_argument("-J", required=True, help="job name")
    ps.add_argument("-q", default="normal", help="queue")
    ps.add_argument("-M", type=int, help="memory MB")
    ps.add_argument("-W", type=int, help="runtime minutes")
    ps.add_argument("command", nargs=argparse.REMAINDER, help="command to run")
    ps.set_defaults(func=cmd_submit)

    sub.add_parser("status", help="show coordinator + job status").set_defaults(func=cmd_status)
    sub.add_parser("dashboard", help="launch dashboard").set_defaults(func=cmd_dashboard)
    sub.add_parser("chat", help="terminal chatbot").set_defaults(func=cmd_chat)
    sub.add_parser("retrain", help="run nightly retrain once").set_defaults(func=cmd_retrain)

    pd = sub.add_parser("demo", help="seed simulated jobs and watch the loop")
    pd.add_argument("-n", type=int, default=6, help="number of jobs to seed")
    pd.set_defaults(func=cmd_demo)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 0
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
