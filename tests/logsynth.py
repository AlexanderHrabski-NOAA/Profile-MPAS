"""Write synthetic MPAS logs and experiment directories for tests.

The log text follows the real format (see data/log.atmosphere.sample.out), including
the fixed-width timer table, so it exercises the same parser paths as a real run.
"""

from datetime import datetime, timedelta

import yaml

BASE_RANKS = 96


def _row(level, name, total, calls, mn, mx, avg, pct_tot, pct_par, eff):
    indent = " " * (level - 1)
    return (f" {level:2d} {indent + name:<45}{total:15.5f}{calls:10d}{mn:15.5f}{mx:15.5f}"
            f"{avg:15.5f} {pct_tot:8.2f}   {pct_par:8.2f}   {eff:8.2f}")


def log_text(ntasks, nsteps=12, dt=50, step_s=3.0, radiation_every=6, radiation_extra=6.0,
             scale=1.0, complete=True, ncells=40962, warnings=0, outputs=2):
    """A log for a run of `ntasks` ranks. `scale` multiplies every time (a slower run)."""
    eff = 1.0 / (1.0 + 0.0004 * (ntasks - BASE_RANKS))      # parallel efficiency model
    sp = BASE_RANKS / ntasks / eff                           # relative time per step
    start = datetime(2026, 1, 26)
    steps = [(step_s + (radiation_extra if i % radiation_every == 0 else 0.0)) * sp * scale
             for i in range(nsteps)]
    integ = sum(steps)
    writes = [4.0 * scale + i for i in range(outputs)]
    init, read = 5.0 * scale, 4.0 * scale
    diag = 0.5 * scale * outputs
    total = init + integ + sum(writes) + diag

    out = [
        "-" * 70,
        f"Beginning MPAS-atmosphere Output Log File for task       0 of {ntasks:7d}",
        "    Opened at 2026/11/04 16:00:00",
        "-" * 70, "", " MPAS-Atmosphere Version 8.4.2-noaa", "",
        " Output from 'git describe --dirty': MPAS-v8.4.2-1.19", "",
        " Compile-time options:", "   Build target: IntelLLVM", "   OpenMP support: no",
        "   Default real precision: single", "   I/O layer: SMIOL", "",
        " Run-time settings:", f"   MPI task count: {ntasks}", "",
        f"        nCells = {ncells}", "        nVertLevels = 55", "",
        f"  Timing for read of 'input' stream: {read:g} s", "",
    ]
    for i, t in enumerate(steps):
        stamp = (start + timedelta(seconds=dt * i)).strftime("%Y-%m-%d_%H:%M:%S")
        out += ["", f" Begin timestep {stamp}", "  global max wsp: 150.5 k=100, 1.0 lat, 2.0 lon"]
        if i == nsteps - 1 and not complete:
            break
        out.append(f"  Timing for integration step: {t:g} s")
    if not complete:
        return "\n".join(out) + "\n"
    for w in writes:
        out += [f" Timing for diagnostic computation: {0.5 * scale:g} s",
                f" Timing for stream output: {w:g} s"]
    out += ["", " ********************************************************",
            "    Finished running the atmosphere core",
            " ********************************************************", "", "",
            "  Timer information: ", "", "    timer_name  total",
            _row(1, "total time", total, 1, total - 0.2, total, total - 0.1, 100, 0, 1.0),
            _row(2, "initialize", init, 1, init, init, init, 100 * init / total,
                 100 * init / total, 1.0),
            _row(3, "read_ICs", read, 1, read, read, read, 100 * read / total,
                 100 * read / init, 1.0),
            _row(2, "diagnostic_fields", diag, 2 * outputs, 0.01, 1.0, 0.1,
                 100 * diag / total, 100 * diag / total, eff)]
    out.append(_row(2, "stream_output", sum(writes), nsteps + 1, 0.0, max(writes),
                    0.5, 100 * sum(writes) / total, 100 * sum(writes) / total, 1.0))
    out.append(_row(2, "time integration", integ, nsteps, min(steps), max(steps),
                    integ / nsteps, 100 * integ / total, 100 * integ / total, 1.0))
    parts = [("physics driver", 0.30, eff * 0.8), ("atm_compute_dyn_tend", 0.40, eff),
             ("atm_advance_acoustic_step", 0.30, eff)]
    for name, share, e in parts:
        t = integ * share
        out.append(_row(3, name, t, nsteps, t / nsteps / 2, t / nsteps * 2, t / nsteps,
                        100 * t / total, 100 * share, e))
        if name == "physics driver":
            for sub, frac in (("rrtmg_swrad", 0.6), ("cu_ntiedtke", 0.4)):
                s = t * frac
                out.append(_row(4, sub, s, nsteps, s / nsteps / 2, s / nsteps * 2, s / nsteps,
                                100 * s / total, 100 * frac, e * 0.7))
    out += [" ", " -----------------------------------------", " Total log messages printed:",
            "    Output messages =                  500",
            f"    Warning messages =                 {warnings:3d}",
            "    Error messages =                     0",
            "    Critical error messages =            0",
            " -----------------------------------------",
            " Logging complete.  Closing file at 2026/11/04 17:00:00"]
    return "\n".join(out) + "\n"


def write_experiment(root, name, nodes, tasks_per_node=BASE_RANKS, profile="native",
                     provenance=True, resolution="60km", run_duration="0_00:10:00", **kw):
    """Create <root>/<name>/ with a log and (optionally) a provenance.yaml."""
    d = root / name
    d.mkdir(parents=True)
    ntasks = nodes * tasks_per_node
    (d / "log.atmosphere.0000.out").write_text(log_text(ntasks, **kw))
    if provenance:
        exp = {"name": name, "resolution": resolution, "nodes": nodes, "ntasks": ntasks,
               "tasks_per_node": tasks_per_node, "profile": profile, "output": True,
               "run_duration": run_duration, "pio_num_iotasks": 0, "pio_stride": 1,
               "pio_iotasks_per_node": None, "pio_io_type": None}
        (d / "provenance.yaml").write_text(yaml.safe_dump({"experiment": exp}))
    return d


def write_study(root, node_counts=(1, 2, 4, 8), **kw):
    """A strong-scaling study laid out like runs/<study>/experiments/<name>/."""
    exps = root / "experiments"
    exps.mkdir(parents=True)
    for n in node_counts:
        write_experiment(exps, f"60km_native_n{n}x96", n, **kw)
    return root
