"""Find MPAS logs, summarize each run, and compute study-level scaling numbers.

Works on anything that holds rank 0's log: a single log file, one experiment
directory, a whole ``runs/<study>`` tree, or a copy of those on another machine.
Experiment directories also carry ``provenance.yaml`` (written when the experiment
was staged), which supplies the node count, resolution, mode, and I/O settings.
Without it those are simply unknown and the corresponding numbers are left out.

Standard library plus PyYAML (already a dependency).
"""

import os
import re
import statistics
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import yaml

from . import SetupError
from .timing import LOG_NAME, LOG_RE, RunLog, parse_log

PROVENANCE = "provenance.yaml"
# Directories that can hold huge trees but never a run log.
_SKIP_DIRS = {"builds", ".git", "__pycache__"}
# A step this much slower than the median is a "spike" (radiation, pre-output step, ...).
SPIKE_FACTOR = 1.5
DAY = 86400.0


def _mode(value):
    """provenance `profile` -> 'scalasca' | 'native' | None (older runs stored a bool)."""
    if isinstance(value, bool):
        return "scalasca" if value else "native"
    return value if isinstance(value, str) else None


def duration_seconds(text):
    """'7_00:00:00' or '06:00:00' (MPAS run_duration syntax) -> seconds, else None."""
    m = re.fullmatch(r"(?:(\d+)_)?(\d+):(\d+):(\d+)", str(text).strip())
    if not m:
        return None
    d, h, mi, s = (int(g or 0) for g in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


@dataclass
class Run:
    label: str
    path: Path          # the directory holding the log
    log: RunLog
    meta: dict          # the `experiment:` mapping of provenance.yaml, or {}

    @property
    def nodes(self):
        return self.meta.get("nodes")

    @property
    def ntasks(self):
        """MPI ranks. The log is the authority; provenance is the fallback."""
        return self.log.ntasks or self.meta.get("ntasks")

    @property
    def tasks_per_node(self):
        """Ranks per node, or None (no provenance, or the study gave a total ntasks)."""
        return self.meta.get("tasks_per_node")

    @property
    def resolution(self):
        if self.meta.get("resolution"):
            return str(self.meta["resolution"])
        return f"{self.log.ncells} cells" if self.log.ncells else "?"

    @property
    def mode(self):
        return _mode(self.meta.get("profile"))

    @property
    def output(self):
        v = self.meta.get("output")
        return v if isinstance(v, bool) else None

    @property
    def pio(self):
        """A short description of the PIO settings, or None when unknown."""
        m = self.meta
        if "pio_num_iotasks" not in m and "pio_io_type" not in m:
            return None
        parts = []
        if m.get("pio_iotasks_per_node"):
            parts.append(f"{m['pio_iotasks_per_node']}/node")
        elif m.get("pio_num_iotasks"):
            parts.append(f"{m['pio_num_iotasks']} tasks, stride {m.get('pio_stride', 1)}")
        if m.get("pio_io_type"):
            parts.append(str(m["pio_io_type"]))
        return ", ".join(parts) or "default"

    @cached_property
    def summary(self):
        return summarize(self)


def find_logs(paths):
    """Expand files and directories into a sorted, de-duplicated list of log files."""
    found = {}
    for p in paths:
        p = Path(p).expanduser()
        if p.is_file():
            found[p.resolve()] = p
        elif p.is_dir():
            hits = []
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
                names = sorted(f for f in files if LOG_RE.fullmatch(f))
                if names:
                    hits.append(Path(root) / names[0])
            if not hits:
                raise SetupError(f"No {LOG_NAME} found under {p}.")
            for h in hits:
                found[h.resolve()] = h
        else:
            raise SetupError(f"{p}: no such file or directory.")
    if not found:
        raise SetupError("No logs given.")
    return [found[k] for k in sorted(found)]


def read_meta(directory):
    """The `experiment:` mapping of a directory's provenance.yaml, or {}."""
    try:
        raw = yaml.safe_load((Path(directory) / PROVENANCE).read_text())
    except (OSError, yaml.YAMLError):
        return {}
    exp = raw.get("experiment") if isinstance(raw, dict) else None
    return exp if isinstance(exp, dict) else {}


def load_runs(paths):
    """Parse every log under `paths` into Runs, ordered by series then rank count."""
    runs = []
    for log_path in find_logs(paths):
        meta = read_meta(log_path.parent)
        label = str(meta.get("name") or log_path.parent.resolve().name or log_path.stem)
        runs.append(Run(label=label, path=log_path.parent, log=parse_log(log_path), meta=meta))
    # Two directories can share a name (a bare log copied around); keep labels distinct.
    seen = {}
    for r in runs:
        seen[r.label] = seen.get(r.label, 0) + 1
        if seen[r.label] > 1:
            r.label = f"{r.label}#{seen[r.label]}"
    runs.sort(key=lambda r: (_sortable(group_key(r)), r.ntasks or 0, r.label))
    return runs


def _percentile(sorted_values, q):
    return sorted_values[min(len(sorted_values) - 1, int(q * len(sorted_values)))]


def step_stats(step_times):
    """Distribution of per-step wall times; the first step is reported on its own.

    The headline cost is total/steps, not the median: periodic expensive steps
    (radiation every 30 model minutes) make the median understate real cost.
    """
    if not step_times:
        return {}
    rest = step_times[1:] or step_times
    base = statistics.median(rest)
    spikes = [t for t in rest if t > SPIKE_FACTOR * base]
    ordered = sorted(rest)
    total = sum(step_times)
    return {
        "step_sum_s": total,
        "step_mean_s": total / len(step_times),
        "step_first_s": step_times[0],
        "step_median_s": base,
        "step_p99_s": _percentile(ordered, 0.99),
        "step_max_s": max(step_times),
        "spike_steps": len(spikes),
        # Share of all step time that is spent above the base cost on spike steps.
        "spike_cost_frac": sum(t - base for t in spikes) / total if total else 0.0,
    }


def imbalance_rows(run):
    """The components that load imbalance is measured on: [(timer name, total s, par_eff)].

    These are the children of `time integration`, with `physics driver` replaced by its
    own children so no time is counted twice. Timers without a parallel efficiency are left
    out.
    """
    log = run.log
    rows = [*(c for c in log.children("time integration") if c.name != "physics driver"),
            *log.children("physics driver")]
    return [(c.name, c.total, c.par_eff) for c in rows
            if c.par_eff == c.par_eff and c.total == c.total]


def imbalance_loss(run):
    """Time lost to load imbalance: [(timer name, seconds)].

    A timer with parallel efficiency e and total time T loses (1 - e) x T against a
    perfectly balanced run (see imbalance_rows for which timers).
    """
    return [(name, (1 - eff) * total) for name, total, eff in imbalance_rows(run)]


def summarize(run):
    """One flat row per run. Unknown values are None."""
    log = run.log
    t = log.timer
    val = lambda name: (t(name).total if t(name) else None)   # noqa: E731

    wall = val("total time")
    if wall is None:
        wall = log.log_wall
    sim = log.sim_seconds
    ranks = run.ntasks
    nodes = run.nodes
    integ = val("time integration")
    writes = sum(log.stream_output) if log.stream_output else (
        0.0 if log.completed else None)

    expected = None
    if log.dt and run.meta.get("run_duration"):
        dur = duration_seconds(run.meta["run_duration"])
        expected = round(dur / log.dt) if dur else None

    if log.messages.get("errors") or log.messages.get("criticals"):
        status = "error"
    elif log.completed and (expected is None or log.nsteps >= expected):
        status = "ok"
    else:
        status = "incomplete"

    lost = sum(v for _, v in imbalance_loss(run)) if log.timers else None

    row = {
        "name": run.label, "resolution": run.resolution, "mode": run.mode,
        "output": run.output, "pio": run.pio, "nodes": nodes, "ranks": ranks,
        "cells": log.ncells, "levels": log.nlevels,
        "cells_per_rank": log.ncells / ranks if log.ncells and ranks else None,
        "status": status, "steps": log.nsteps, "steps_expected": expected,
        "dt_s": log.dt, "sim_s": sim,
        "wall_s": wall, "initialize_s": val("initialize"), "read_ics_s": val("read_ICs"),
        "integration_s": integ, "output_s": writes, "diagnostics_s": val("diagnostic_fields"),
        "output_writes": len(log.stream_output),
        "imbalance_lost_s": lost,
        "imbalance_lost_pct": 100 * lost / wall if lost is not None and wall else None,
        "sim_days_per_day": sim / wall if sim and wall else None,
        "node_hours_per_sim_day": (nodes * wall / 3600.0 / (sim / DAY)
                                   if nodes and wall and sim else None),
        "core_s_per_cell_step": (wall * ranks / (log.ncells * log.nsteps)
                                 if wall and ranks and log.ncells and log.nsteps else None),
        "warnings": log.messages.get("warnings"), "errors": log.messages.get("errors"),
        "max_wsp": max(log.max_wsp) if log.max_wsp else None,
        "version": log.describe,
    }
    row.update(step_stats(log.step_times))
    return row


def timer_rows(run):
    """The run's timer tree as flat rows (for the long-form CSV)."""
    return [{"run": run.label, "ranks": run.ntasks, "level": t.level, "path": t.path,
             "total_s": t.total, "calls": t.calls, "min_s": t.min, "max_s": t.max,
             "avg_s": t.avg, "pct_tot": t.pct_tot, "pct_par": t.pct_par, "par_eff": t.par_eff}
            for t in run.log.timers]


def components(run, parent):
    """Direct children of the timer `parent`: [(name, total_s, par_eff, core_seconds)].

    Core-seconds (total x ranks) is what stays flat under perfect scaling.
    """
    ranks = run.ntasks
    return [(c.name, c.total, c.par_eff, c.total * ranks if ranks else None)
            for c in run.log.children(parent)]


# ---- studies ---------------------------------------------------------------------------

def group_key(run):
    """Runs that differ only in size belong to one scaling series."""
    return (run.resolution, run.mode, run.output, run.pio, run.tasks_per_node)


def _sortable(key):
    """A group key as something sortable: None sorts last, numbers sort as numbers."""
    return tuple((v is None, 0 if v is None else v) for v in key)


def group_label(key):
    res, mode, output, pio, tpn = key
    parts = [res, mode or "mode unknown"]
    if tpn:
        parts.append(f"{tpn} ranks/node")
    if output is False:
        parts.append("no output")
    if pio not in (None, "default"):
        parts.append(f"pio {pio}")
    return " · ".join(parts)


def group_tags(keys):
    """Short labels that tell the group keys apart: only the fields that differ.

    One key gives [""]. Used under the bars when ranks are subdivided by group.
    """
    def text(i, v):
        if i == 2:
            return {True: "output", False: "no output"}.get(v, "output unknown")
        if i == 3:
            return "default pio" if v in (None, "default") else f"pio {v}"
        if i == 4:
            return f"{v}/node" if v else "?/node"
        return str(v) if v else "?"
    varying = [i for i in range(len(keys[0])) if len({k[i] for k in keys}) > 1] if keys else []
    return [" · ".join(text(i, k[i]) for i in varying) for k in keys]


def groups(runs):
    """{group key: runs sorted by rank count}, in order of first appearance."""
    out = {}
    for r in runs:
        out.setdefault(group_key(r), []).append(r)
    for rs in out.values():
        rs.sort(key=lambda r: (r.ntasks or 0, r.label))
    return out


def is_scaling_study(runs):
    """True when some series has at least two different rank counts."""
    return any(len({r.ntasks for r in rs if r.ntasks}) >= 2 for rs in groups(runs).values())


def scaling_points(runs, key):
    """Speedup and parallel efficiency of summary field `key` against the smallest run.

    Returns dicts with ranks, nodes, value, speedup, efficiency and ideal (the value a
    perfectly scaling run would have). Runs without the value are skipped.
    """
    pts = [(r.ntasks, r.nodes, r.summary.get(key)) for r in runs]
    pts = [p for p in pts if p[0] and p[2]]
    if not pts:
        return []
    ranks0, _, t0 = pts[0]
    out = []
    for ranks, nodes, v in pts:
        out.append({"ranks": ranks, "nodes": nodes, "value": v,
                    "speedup": t0 / v, "efficiency": (t0 * ranks0) / (v * ranks),
                    "ideal": t0 * ranks0 / ranks})
    return out


def caveats(runs):
    """Reasons to read the numbers with care, as short sentences."""
    notes = []
    for key, rs in groups(runs).items():
        incomplete = [r.label for r in rs if r.summary["status"] != "ok"]
        if incomplete:
            notes.append(f"{group_label(key)}: not finished cleanly: {', '.join(incomplete)}.")
    modes = {r.mode for r in runs if r.mode}
    if "scalasca" in modes and "native" in modes:
        notes.append("Scalasca and native runs are mixed; instrumentation inflates the "
                     "Scalasca timings, so compare like with like.")
    elif modes == {"scalasca"}:
        notes.append("All runs are Scalasca-instrumented; absolute times include "
                     "instrumentation overhead.")
    for key, rs in groups(runs).items():
        if len({r.ntasks for r in rs}) != len(rs):
            notes.append(f"{group_label(key)}: several runs share a rank count "
                         "(repeats, or differing settings that are not recorded).")
    return notes
