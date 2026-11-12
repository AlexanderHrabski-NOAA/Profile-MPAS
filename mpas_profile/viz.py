"""Plots and CSV tables for MPAS timing: one run, or a whole study.

    ./plot-timing.py runs/strong-scaling-60km            # interactive windows
    ./plot-timing.py runs/strong-scaling-60km --save out # PNGs + results.csv + timers.csv

One run gets an overview and a component breakdown. Several runs get a study view:
scaling (when rank counts differ), phase and component breakdowns, load imbalance (in
total and component by component), quality (throughput, cost), and the distribution of
step times.

matplotlib is imported only when figures are drawn, so loading logs and writing the
CSV tables work without it. Chart conventions follow the dataviz skill: thin marks,
one axis per plot, a fixed categorical color order, a legend whenever a plot has two
or more series, and ink-colored text.
"""

import argparse
import csv
import math
import statistics
import sys
from pathlib import Path

from . import SetupError, analysis

# Categorical slots in their fixed order (the dataviz reference palette, light mode).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
OTHER = "#a8a7a1"
SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#8d8c86", "#e6e5e1"

# Wall-time phases of a run. Each keeps its slot color in every figure.
PHASES = [("integration_s", "time integration", 0), ("output_s", "stream output", 1),
          ("initialize_s", "initialize", 2), ("diagnostics_s", "diagnostic fields", 3)]

NOTE = ("Timer values are each timer's maximum over ranks, so shares from the timer "
        "table are approximate (children can add up to slightly more than their parent).")
NON_INTERACTIVE = {"agg", "pdf", "svg", "ps", "cairo", "template", "pgf"}


# ---- CSV and console -------------------------------------------------------------------

def write_csv(runs, outdir):
    """results.csv (one row per run) and timers.csv (the timer trees, long form)."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    rows = [r.summary for r in runs]
    cols = list(dict.fromkeys(k for row in rows for k in row))
    _write(outdir / "results.csv", cols, rows)
    trows = [t for r in runs for t in analysis.timer_rows(r)]
    tcols = list(dict.fromkeys(k for row in trows for k in row)) or ["run"]
    _write(outdir / "timers.csv", tcols, trows)
    return [outdir / "results.csv", outdir / "timers.csv"]


def _write(path, cols, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for row in rows:
            w.writerow({k: "" if v is None or (isinstance(v, float) and math.isnan(v))
                        else v for k, v in row.items()})


def print_summary(runs, out=None):
    out = out or sys.stdout
    head = ["run", "ranks", "status", "steps", "wall s", "sim d/d", "step mean s", "integ %", "output %"]
    table = []
    for r in runs:
        s = r.summary
        wall = s["wall_s"]
        pct = lambda v: f"{100 * v / wall:.1f}" if v is not None and wall else "-"   # noqa: E731
        table.append([r.label, s["ranks"] or "-", s["status"], s["steps"],
                      f"{wall:.1f}" if wall else "-",
                      f"{s['sim_days_per_day']:.2f}" if s["sim_days_per_day"] else "-",
                      f"{s['step_mean_s']:.3f}" if s.get("step_mean_s") else "-",
                      pct(s["integration_s"]), pct(s["output_s"])])
    widths = [max(len(str(x)) for x in col) for col in zip(head, *table)]
    for row in [head] + table:
        print("  " + "  ".join(str(c).ljust(w) for c, w in zip(row, widths)).rstrip(), file=out)
    for note in analysis.caveats(runs):
        print(f"  note: {note}", file=out)


# ---- matplotlib plumbing -----------------------------------------------------------------

def _load_pyplot(save):
    try:
        import matplotlib
    except ImportError:
        raise SetupError("matplotlib is needed to draw figures (pip install matplotlib). "
                         "The summary table and, with --save, the CSV files do not need it.")
    if save:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    if not save and matplotlib.get_backend().lower() in NON_INTERACTIVE:
        raise SetupError("No display is available for interactive windows. "
                         "Use --save DIR to write PNG files and CSV tables instead.")
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": GRID, "axes.labelcolor": INK2, "axes.titlecolor": INK,
        "axes.titlesize": 10, "axes.titleweight": "regular", "axes.titlelocation": "left",
        "axes.labelsize": 9, "xtick.color": INK2, "ytick.color": INK2, "xtick.labelsize": 8.5,
        "ytick.labelsize": 8.5, "text.color": INK, "axes.grid": True, "grid.color": GRID,
        "grid.linewidth": 0.8, "axes.axisbelow": True, "axes.spines.top": False,
        "axes.spines.right": False, "legend.frameon": False, "legend.fontsize": 8.5,
        "font.size": 9, "lines.linewidth": 2.0, "lines.markersize": 7,
        "figure.dpi": 100, "savefig.dpi": 150,
    })
    return plt


def _color(i):
    return SERIES[i % len(SERIES)]


def _seconds(v):
    return f"{v:,.0f} s" if abs(v) >= 100 else f"{v:.1f} s"


def _finish(fig, title, notes=(), timer_note=True):
    fig.suptitle(title, x=0.01, ha="left", fontsize=12, color=INK)
    text = "\n".join([*notes, *([NOTE] if timer_note else [])])
    fig.text(0.01, 0.006, text, fontsize=7.5, color=MUTED, va="bottom")
    try:
        fig.canvas.manager.set_window_title(title)
    except Exception:           # headless backends have no window
        pass
    return fig


def _hbars(ax, labels, values, color, texts=None, room=1.3):
    """Ranked horizontal bars, largest first, with the value at each bar end.

    `room` widens the x range so the end-of-bar text fits inside the axes."""
    y = list(range(len(labels)))
    ax.barh(y, values, color=color, height=0.62)
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.grid(axis="y", visible=False)
    top = max(values) if values else 1
    for yi, v, t in zip(y, values, texts or [_seconds(v) for v in values]):
        ax.text(v + top * 0.01, yi, t, va="center", fontsize=8, color=INK2)
    ax.set_xlim(0, top * room)


def _size(xkey):
    """How a run's size is read for the x axis: node count, or MPI rank count."""
    return (lambda r: r.nodes) if xkey == "nodes" else (lambda r: r.ntasks)


def _series_by_size(runs, xkey):
    """{group key: runs} when every series has distinct sizes and some series has several
    (so a line through its runs means something), else None."""
    size = _size(xkey)
    by_group = analysis.groups(runs)
    distinct = all(size(r) for r in runs) and all(
        len({size(r) for r in rs}) == len(rs) for rs in by_group.values())
    return by_group if distinct and any(len(rs) > 1 for rs in by_group.values()) else None


class _Layout:
    """Where the per-run bars go.

    One series: one bar per run, labeled with its size (rank or node count, per `xkey`).
    Several series with distinct sizes inside each: one cluster per size, one bar per
    series in it (so `||  ||  ||` is three sizes of two series), each bar tagged with what
    sets its series apart. Otherwise (unknown or repeated sizes) one bar per run, labeled
    by run name. `gidx` is each run's series number, for coloring.
    """
    SPAN = 0.84       # width of a cluster, as a fraction of the gap between sizes

    def __init__(self, runs, xkey="ranks"):
        by_group = analysis.groups(runs)
        self.keys = list(by_group)
        self.gidx = [self.keys.index(analysis.group_key(r)) for r in runs]
        self.tags = analysis.group_tags(self.keys)
        self.xkey = xkey
        size = _size(xkey)
        sizes = [size(r) for r in runs]
        distinct = all(sizes) and all(len({size(r) for r in rs}) == len(rs)
                                      for rs in by_group.values())
        self.grouped = len(self.keys) > 1 and bool(distinct)
        n = len(self.keys)
        if self.grouped:
            self.slots = sorted(set(sizes))
            self.unit = self.SPAN / n
            self.xs = [self.slots.index(size(r)) + (g - (n - 1) / 2) * self.unit
                       for r, g in zip(runs, self.gidx)]
            self.labels = [f"{size(r)} · {self.tags[g]}" for r, g in zip(runs, self.gidx)]
        else:
            self.slots = []
            self.unit = 1.0
            self.xs = list(range(len(runs)))
            one = n == 1
            self.labels = [str(size(r)) if one and size(r) else r.label for r in runs]
        self.runs = runs

    @property
    def xlabel(self):
        return "nodes" if self.xkey == "nodes" else "MPI ranks"

    def width(self, frac):
        """Bar width: `frac` of a slot when there is one bar per slot, else the cluster share."""
        return self.unit * (0.92 if self.grouped else frac)

    def colors(self):
        return [_color(g) for g in self.gidx]

    def legend(self, ax, **kw):
        """A series legend for bars colored by series (none when there is only one)."""
        if len(self.keys) < 2:
            return
        from matplotlib.patches import Patch
        ax.legend(handles=[Patch(color=_color(i), label=t) for i, t in enumerate(self.tags)],
                  ncol=len(self.keys), **kw)


def _set_run_axis(ax, lay, tags=False):
    """Rank- or node-count ticks, per the layout's `xkey`. `tags` also labels every bar with its series, under the bar, for
    charts whose colors are taken (stacked bars); colored charts use a legend instead."""
    ax.grid(axis="x", visible=False)
    if lay.grouped:
        n = len(lay.slots)
        ax.set_xticks(range(n), [f"{s:g}" for s in lay.slots])
        if tags:
            longest = max(len(t) for t in lay.tags)
            ax.set_xticks(lay.xs, [lay.tags[g] for g in lay.gidx], minor=True, rotation=90,
                          va="top", ha="center", fontsize=7.5)
            ax.tick_params(axis="x", which="minor", length=0, pad=2, colors=INK2)
            ax.tick_params(axis="x", which="major", pad=6 + 4.6 * longest)
        ax.set_xlim(-0.55, n - 0.45)
        ax.set_xlabel(lay.xlabel)
        return
    labels = lay.labels
    short = max(map(len, labels)) <= 6
    ax.set_xticks(lay.xs, labels, rotation=0 if short else 35, ha="center" if short else "right")
    ax.set_xlabel(lay.xlabel if len(lay.keys) == 1 else "run")


def _rank_axis(ax, ticks, xlabel):
    from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator
    ax.set_xscale("log", base=2)
    ax.xaxis.set_major_locator(FixedLocator(sorted(set(ticks))))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax.set_xlabel(xlabel)


# ---- one run ---------------------------------------------------------------------------

def fig_overview(plt, run):
    log, s = run.log, run.summary
    fig = plt.figure(figsize=(11.5, 7.6))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.2, 1], hspace=0.42, wspace=0.34,
                          left=0.14, right=0.97, top=0.88, bottom=0.14)

    ax = fig.add_subplot(gs[0, :])
    times = log.step_times
    if log.step_stamps and len(log.step_stamps) >= len(times) > 0:
        t0 = log.step_stamps[0]
        x = [(t - t0).total_seconds() / 3600 for t in log.step_stamps[:len(times)]]
        ax.set_xlabel("model time (hours)")
    else:
        x = list(range(len(times)))
        ax.set_xlabel("step")
    if times:
        base = s["step_median_s"]
        ax.plot(x, times, color=SERIES[0], linewidth=1.0, label="step wall time")
        idx = [i for i, v in enumerate(times) if i > 0 and v > analysis.SPIKE_FACTOR * base]
        ax.scatter([x[i] for i in idx], [times[i] for i in idx], s=14, color=SERIES[1],
                   zorder=3, label=f"spike (> {analysis.SPIKE_FACTOR:g}× median)")
        ax.axhline(base, color=MUTED, linewidth=1.0, zorder=1, label=f"median {base:.2f} s")
        ax.scatter([x[0]], [times[0]], s=30, facecolor="none", edgecolor=SERIES[1],
                   zorder=3, label=f"first step {times[0]:.1f} s")
        ax.set_ylim(0, max(times) * 1.12)
        ax.legend(loc="upper right", ncol=4)
        ax.set_title(f"Wall time per step: mean {s['step_mean_s']:.2f} s (total / steps), "
                     f"{s['spike_steps']} spike steps = {100 * s['spike_cost_frac']:.0f}% of step time")
    else:
        ax.text(0.5, 0.5, "no completed steps in the log", ha="center", transform=ax.transAxes,
                color=INK2)
        ax.set_title("Wall time per step")
    ax.set_ylabel("seconds")

    ax = fig.add_subplot(gs[1, 0])
    phases = [(lab, s[key], slot) for key, lab, slot in PHASES if s.get(key)]
    wall = s["wall_s"]
    if phases:
        texts = [f"{_seconds(v)}  ({100 * v / wall:.1f}%)" if wall else _seconds(v)
                 for _, v, _ in phases]
        _hbars(ax, [p[0] for p in phases], [p[1] for p in phases], SERIES[0], texts, room=1.45)
    ax.set_title("Where the wall time goes" + (f" (total {_seconds(wall)})" if wall else ""))
    ax.set_xlabel("seconds")

    ax = fig.add_subplot(gs[1, 1])
    writes = log.stream_output
    if writes:
        ax.bar(range(len(writes)), writes, color=SERIES[1], width=0.6)
        for i, v in enumerate(writes):
            if len(writes) <= 12 or v == max(writes):
                ax.text(i, v + max(writes) * 0.015, f"{v:.0f}", ha="center", fontsize=8, color=INK2)
        ax.set_xticks(range(len(writes)), [str(i + 1) for i in range(len(writes))])
        ax.set_ylim(0, max(writes) * 1.15)
        ax.grid(axis="x", visible=False)
    else:
        ax.text(0.5, 0.5, "no output writes", ha="center", transform=ax.transAxes, color=INK2)
    ax.set_title(f"Output writes (rank 0): {len(writes)}, total {_seconds(sum(writes))}")
    ax.set_xlabel("write")
    ax.set_ylabel("seconds")

    ranks = f"{run.ntasks} ranks" if run.ntasks else "ranks unknown"
    cells = f", {log.ncells:,} cells" if log.ncells else ""
    sim = f"{s['sim_days_per_day']:.1f} simulated days per day" if s["sim_days_per_day"] else ""
    title = f"{run.label}: {ranks}{cells}" + (f", {sim}" if sim else "")
    return _finish(fig, title, analysis.caveats([run]))


def _component_rows(log, parent, limit):
    rows = [c for c in log.children(parent) if c.total == c.total]
    return sorted(rows, key=lambda c: -c.total)[:limit]


def fig_components(plt, run):
    log = run.log
    fig, axes = plt.subplots(1, 3, figsize=(16.5, 6.4), gridspec_kw={"wspace": 1.05})
    fig.subplots_adjust(left=0.155, right=0.985, top=0.86, bottom=0.14)
    if not log.timers:
        for ax in axes:
            ax.axis("off")
        axes[1].text(0.5, 0.5, "no timer table in this log\n(the run did not finish)",
                     ha="center", va="center", color=INK2)
        return _finish(fig, f"{run.label}: components")

    integ = _component_rows(log, "time integration", 12)
    ax = axes[0]
    _hbars(ax, [c.name for c in integ], [c.total for c in integ], SERIES[0],
           [f"{_seconds(c.total)}  {c.pct_par:.0f}%" for c in integ], room=1.4)
    ax.set_title("time integration: components\n(seconds, % of time integration)")

    phys = _component_rows(log, "physics driver", 12)
    ax = axes[1]
    if phys:
        _hbars(ax, [c.name for c in phys], [c.total for c in phys], SERIES[0],
               [f"{_seconds(c.total)}  {c.pct_par:.0f}%" for c in phys], room=1.4)
    else:
        ax.axis("off")
    ax.set_title("physics driver: schemes\n(seconds, % of physics driver)")

    lost = [(c.name, (1 - c.par_eff) * c.total, c.par_eff)
            for c in [*[c for c in integ if c.name != "physics driver"], *phys]
            if c.par_eff == c.par_eff]
    lost = sorted(lost, key=lambda t: -t[1])[:12]
    ax = axes[2]
    if lost:
        _hbars(ax, [n for n, _, _ in lost], [v for _, v, _ in lost], SERIES[1],
               [f"{_seconds(v)}  eff {e:.2f}" for _, v, e in lost], room=1.5)
    ax.set_title("Time lost to load imbalance\n((1 − par_eff) × total, with par_eff)")
    for ax in axes:
        ax.set_xlabel("seconds")
    return _finish(fig, f"{run.label}: where the time goes, and what is imbalanced",
                   analysis.caveats([run]))


# ---- a study ---------------------------------------------------------------------------

def _x_choice(runs, xkey):
    """('ranks' | 'nodes', note) honoring --x, falling back when nodes are unknown."""
    if xkey == "nodes" and not all(r.nodes for r in runs):
        return "ranks", "--x nodes needs provenance.yaml for every run; using ranks."
    return ("nodes" if xkey == "nodes" else "ranks"), None


def fig_scaling(plt, runs, xkey):
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 8.2))
    fig.subplots_adjust(left=0.08, right=0.97, top=0.9, bottom=0.14, hspace=0.38, wspace=0.26)
    (a_tot, a_int), (a_spd, a_eff) = axes
    xlabel = "nodes" if xkey == "nodes" else "MPI ranks"
    ticks = []
    handles = []
    for gi, (key, rs) in enumerate(analysis.groups(runs).items()):
        color = _color(gi)
        label = analysis.group_label(key)
        for ax, metric in ((a_tot, "wall_s"), (a_int, "integration_s")):
            pts = analysis.scaling_points(rs, metric)
            if not pts:
                continue
            xs = [p[xkey] for p in pts]
            ticks += xs
            line, = ax.plot(xs, [p["value"] for p in pts], marker="o", color=color,
                            label=label)
            ax.plot(xs, [p["ideal"] for p in pts], color=color, linewidth=1.0,
                    linestyle="--", alpha=0.55)
            ax.set_xscale("log", base=2)
            ax.set_yscale("log")
            if ax is a_tot:
                handles.append(line)
        pts = analysis.scaling_points(rs, "integration_s")
        if pts:
            xs = [p[xkey] for p in pts]
            a_spd.plot(xs, [p["speedup"] for p in pts], marker="o", color=color)
            x0 = xs[0]
            a_eff.plot(xs, [p["efficiency"] for p in pts], marker="o", color=color)
            if gi == 0:
                a_spd.plot(xs, [x / x0 for x in xs], color=MUTED, linewidth=1.0, linestyle="--")
    a_eff.axhline(1.0, color=MUTED, linewidth=1.0)
    a_eff.set_ylim(0, 1.12)
    a_tot.set_title("Total run time (MPAS 'total time')\ndashed: perfect 1/N scaling")
    a_int.set_title("Time integration\ndashed: perfect 1/N scaling")
    a_spd.set_title("Speedup of time integration\ndashed: ideal")
    a_eff.set_title("Parallel efficiency of time integration")
    a_tot.set_ylabel("seconds")
    a_int.set_ylabel("seconds")
    a_spd.set_ylabel("speedup vs smallest run")
    a_eff.set_ylabel("efficiency")
    for ax in axes.flat:
        _rank_axis(ax, ticks, xlabel)
    if len(handles) > 1:
        a_tot.legend(handles=handles, loc="upper right")
    return _finish(fig, f"Strong scaling: {len(runs)} runs", analysis.caveats(runs))


def _stack(ax, lay, series, ylabel):
    """Stacked bars, one per run. `series` = [(label, color, [value per run], hatch?)]."""
    bottoms = [0.0] * len(lay.runs)
    for label, color, vals, *hatch in series:
        vals = [v or 0.0 for v in vals]
        ax.bar(lay.xs, vals, bottom=bottoms, color=color, width=lay.width(0.62), label=label,
               edgecolor=SURFACE, linewidth=1.2, hatch=hatch[0] if hatch else None)
        bottoms = [b + v for b, v in zip(bottoms, vals)]
    _set_run_axis(ax, lay, tags=True)
    ax.set_ylabel(ylabel)
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0))


REST = "\0rest"          # key for the part of a run that the named parts do not cover
INNER = "\0inner"        # key for time integration's own time, outside its child timers
REST_COLOR = "#d2d1cc"


def _shares(wall, parts):
    """Each part's percent of a run's wall time, with the uncovered remainder under REST.

    `parts` is {name: seconds}. The shares sum to exactly 100: timers are maxima over
    ranks, so parts can add up to slightly more than the whole, in which case everything is
    scaled down together. Returns (shares, scale) where scale is parts / wall before
    scaling (1.0 when they fit), or ({}, 1.0) when the wall time is unknown.
    """
    if not wall:
        return {}, 1.0
    parts = {n: v for n, v in parts.items() if v and v == v and v > 0}
    rest = max(wall - sum(parts.values()), 0.0)
    total = sum(parts.values()) + rest
    shares = {n: 100 * v / total for n, v in parts.items()}
    shares[REST] = 100 * rest / total
    return shares, total / wall


HATCHES = [None, "//", "xx", "\\\\"]     # after the palette runs out, reuse colors with a pattern


def _series_style(i):
    """(color, hatch) of the i-th named component: palette first, then patterned repeats."""
    return SERIES[i % len(SERIES)], HATCHES[(i // len(SERIES)) % len(HATCHES)]


def _parent_shares(runs, parent, scales, of_parent=False):
    """Per run, the shares for each direct child timer of `parent`.

    Shares are of the run's wall time, or of the parent's own time when `of_parent`. The
    parent's own time (what none of its children account for) goes under INNER, and, for
    shares of wall time, the rest of the run under REST. Appends each run's scale (see
    _shares) to `scales`.
    """
    out = []
    for r in runs:
        total = r.log.timer(parent)
        children = {n: t for n, t, _, _ in analysis.components(r, parent)}
        children[INNER] = (total.total if total else 0.0) - sum(
            v for v in children.values() if v == v)
        shares, scale = _shares(total.total if total and of_parent else
                                (None if of_parent else r.summary["wall_s"]), children)
        out.append(shares)
        scales.append(scale)
    return out


def _component_series(shares, parent, outside=True):
    """Stack series for `_stack`: every child of `parent`, largest first, then the parent's
    own remaining time, then (unless `outside` is False) the rest of the run."""
    totals = {}
    for c in shares:
        for n, v in c.items():
            if n not in (REST, INNER):
                totals[n] = totals.get(n, 0.0) + v
    names = sorted(totals, key=lambda n: -totals[n])
    series = []
    for i, n in enumerate(names):
        color, hatch = _series_style(i)
        series.append((n, color, [c.get(n) for c in shares], hatch))
    series.append((f"{parent} (own time)", OTHER, [c.get(INNER) for c in shares]))
    if outside:
        series.append((f"outside {parent}", REST_COLOR, [c.get(REST) for c in shares]))
    return series, bool(names)


def fig_breakdown(plt, runs, xkey="ranks"):
    fig, (a, b, c) = plt.subplots(1, 3, figsize=(25.0, 6.6), gridspec_kw={"wspace": 0.85})
    fig.subplots_adjust(left=0.04, right=0.91, top=0.84, bottom=0.2)
    lay = _Layout(runs, xkey)
    scales = []

    phases = []
    for r in runs:
        shares, scale = _shares(r.summary["wall_s"],
                                {key: r.summary.get(key) for key, _, _ in PHASES})
        phases.append(shares)
        scales.append(scale)
    series = [(lab, SERIES[slot], [p.get(key) for p in phases]) for key, lab, slot in PHASES]
    series.append(("other / untimed", REST_COLOR, [p.get(REST) for p in phases]))
    _stack(a, lay, series, "% of total run time")
    a.set_ylim(0, 100)
    a.set_title("Wall time by phase\n(% of total run time)")

    # Time integration is shown against the whole run; the physics driver against itself.
    for ax, parent, of_parent in ((b, "time integration", False), (c, "physics driver", True)):
        whole = f"{parent} time" if of_parent else "total run time"
        series, found = _component_series(_parent_shares(runs, parent, scales, of_parent),
                                          parent, outside=not of_parent)
        _stack(ax, lay, series, f"% of {whole}")
        ax.set_ylim(0, 100)
        ax.set_title(f"{parent}: components\n(% of {whole})")
        if not found:
            ax.text(0.5, 0.5, f"no {parent} timers in these logs", ha="center", va="center",
                    transform=ax.transAxes, color=INK2)

    notes = list(analysis.caveats(runs))
    if max(scales) > 1.005:
        notes.append("Some bars were scaled down to 100%: timer maxima over ranks add up to "
                     f"as much as {100 * (max(scales) - 1):.1f}% more than the whole they "
                     "are a share of.")
    return _finish(fig, "Where the time goes, run by run", notes)


def _series_panel(ax, lay, runs, xkey, values, legend=False):
    """One value per run: a line per series against size, which makes series easy to tell
    apart, or one bar per run when sizes do not tell runs apart. True when lines."""
    by_group = _series_by_size(runs, xkey)
    if by_group:
        size = _size(xkey)
        at = {id(r): v for r, v in zip(runs, values)}
        ticks = []
        for gi, rs in enumerate(by_group.values()):
            pts = [(size(r), at[id(r)]) for r in rs if at[id(r)] is not None]
            if pts:
                ticks += [x for x, _ in pts]
                ax.plot(*zip(*pts), marker="o", color=_color(gi), label=lay.tags[gi])
        if ticks:
            _rank_axis(ax, ticks, lay.xlabel)
        if legend and len(by_group) > 1:
            ax.legend(loc="best")
        return True
    ax.bar(lay.xs, [v or 0 for v in values], color=lay.colors(), width=lay.width(0.6))
    for x, v in zip(lay.xs, values):
        if v and len(runs) <= 10:
            ax.text(x, v * 1.01, f"{v:.1f}", ha="center", va="bottom", fontsize=8, color=INK2)
    _set_run_axis(ax, lay)
    lay.legend(ax, loc="upper left")
    return False


def _imbalance_table(runs):
    """Per run, {component: (% of total run time lost to imbalance, imbalance in %)}.

    Imbalance is 1 - par_eff, the share of the component's time that a perfectly balanced
    run would not spend. The first number weighs it by how long the component runs.
    """
    table = []
    for r in runs:
        wall = r.summary["wall_s"]
        table.append({name: (100 * (1 - eff) * total / wall if wall else None, 100 * (1 - eff))
                      for name, total, eff in analysis.imbalance_rows(r)})
    return table


def _top_components(table, n):
    """The n components that lose the most time to imbalance, summed over runs."""
    totals = {}
    for d in table:
        for name, (lost, _) in d.items():
            totals[name] = totals.get(name, 0.0) + (lost or 0.0)
    return sorted(totals, key=lambda name: -totals[name])[:n]


def fig_imbalance(plt, runs, xkey="ranks"):
    fig, (a, b) = plt.subplots(1, 2, figsize=(16.5, 6.2), gridspec_kw={"wspace": 0.55})
    fig.subplots_adjust(left=0.06, right=0.84, top=0.84, bottom=0.2)
    lay = _Layout(runs, xkey)
    table = _imbalance_table(runs)

    total = [r.summary["imbalance_lost_pct"] for r in runs]
    _series_panel(a, lay, runs, xkey, total, legend=True)
    a.set_ylim(0, max([v or 0 for v in total] + [0]) * 1.2 or 1)
    a.set_ylabel("% of total run time")
    a.set_title("Time lost to load imbalance\n(sum of (1 − par_eff) × time over components)")

    top = _top_components(table, 10)
    series = []
    for i, name in enumerate(top):
        color, hatch = _series_style(i)
        series.append((name, color, [d[name][0] if name in d else None for d in table], hatch))
    series.append(("all other components", OTHER,
                   [sum(v[0] or 0.0 for n, v in d.items() if n not in top) for d in table]))
    _stack(b, lay, series, "% of total run time")
    b.set_title("The same loss, by component\n(the ten largest; bars add up to the left panel)")
    return _finish(fig, "Load imbalance: how much of the run it costs, and where from",
                   analysis.caveats(runs))


def fig_imbalance_components(plt, runs, xkey="ranks", limit=8):
    """One small panel per component: its own imbalance against size, a line per series."""
    table = _imbalance_table(runs)
    names = _top_components(table, limit)
    n = max(len(names), 1)
    ncols = min(n, 4)
    nrows = math.ceil(n / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.1 * ncols + 0.8, 3.4 * nrows + 1.7),
                             sharey=True, squeeze=False)
    fig.subplots_adjust(left=0.07 if ncols > 2 else 0.12, right=0.985,
                        top=0.8 if nrows == 1 else 0.88, bottom=0.2 if nrows == 1 else 0.11,
                        wspace=0.12, hspace=0.6)
    for ax in axes.flat[n:]:
        ax.axis("off")
    title = "Load imbalance of the components that lose the most time"
    if not names:
        axes[0, 0].text(0.5, 0.5, "no timer tables in these logs", ha="center",
                        transform=axes[0, 0].transAxes, color=INK2)
        return _finish(fig, title)
    lay = _Layout(runs, xkey)
    top = 0.0
    for i, (ax, name) in enumerate(zip(axes.flat, names)):
        imb = [d[name][1] if name in d else None for d in table]
        lost = [d[name][0] for d in table if name in d and d[name][0] is not None]
        _series_panel(ax, lay, runs, xkey, imb, legend=i == 0)
        ax.set_title(f"{name}\n(avg {sum(lost) / len(lost):.1f}% of run time lost)"
                     if lost else name, fontsize=9)
        top = max([top] + [v for v in imb if v is not None])
    for ax in axes.flat[:n]:
        ax.set_ylim(0, top * 1.2 or 1)
    for ax in axes[:, 0]:
        ax.set_ylabel("imbalance, 1 − par_eff (%)")
    notes = ["Imbalance is 1 − par_eff: the share of a component's time lost to waiting for "
             "its slowest rank. Panels are ordered by time lost.", *analysis.caveats(runs)]
    return _finish(fig, title, notes)


def fig_quality(plt, runs, xkey="ranks"):
    fig, (a_thr, a_cost) = plt.subplots(1, 2, figsize=(12.0, 5.6), gridspec_kw={"wspace": 0.28})
    fig.subplots_adjust(left=0.07, right=0.985, top=0.84, bottom=0.22)
    lay = _Layout(runs, xkey)
    xs = lay.xs
    cols = lay.colors()

    sdd = [r.summary["sim_days_per_day"] for r in runs]
    a_thr.bar(xs, [v or 0 for v in sdd], color=cols, width=lay.width(0.6))
    for x, v in zip(xs, sdd):
        if v and len(runs) <= 10:
            a_thr.text(x, v * 1.01, f"{v:.1f}", ha="center", va="bottom", fontsize=8, color=INK2)
    _set_run_axis(a_thr, lay)
    lay.legend(a_thr, loc="upper left")
    if lay.grouped:
        a_thr.set_ylim(0, max([v or 0 for v in sdd] + [0]) * 1.22)
    a_thr.set_ylabel("simulated days per day")
    a_thr.set_title("Throughput")

    nh = [r.summary["node_hours_per_sim_day"] for r in runs]
    if all(nh):
        vals, ylabel, title = nh, "node-hours per simulated day", "Cost"
    else:
        vals = [r.summary["core_s_per_cell_step"] for r in runs]
        ylabel, title = "core-seconds per cell per step", "Cost (nodes unknown, so per cell-step)"
    a_cost.bar(xs, [v or 0 for v in vals], color=cols, width=lay.width(0.6))
    for x, v in zip(xs, vals):
        if v and len(runs) <= 10:
            a_cost.text(x, v * 1.01, f"{v:.3g}", ha="center", va="bottom", fontsize=8, color=INK2)
    _set_run_axis(a_cost, lay)
    a_cost.set_ylabel(ylabel)
    a_cost.set_title(title)
    return _finish(fig, "Quality of the runs: throughput and cost", analysis.caveats(runs))


def _step_panels(runs, xkey, lay):
    """[(title, [(run, series number)])]: one panel per size with the series overlaid, or,
    when sizes do not tell runs apart, one panel per run."""
    if _series_by_size(runs, xkey):
        size = _size(xkey)
        unit = "nodes" if xkey == "nodes" else "ranks"
        return [(f"{s:g} {unit}", [(r, g) for r, g in zip(runs, lay.gidx) if size(r) == s])
                for s in sorted({size(r) for r in runs})]
    return [(r.label, [(r, g)]) for r, g in zip(runs, lay.gidx)]


def fig_steps(plt, runs, xkey="ranks"):
    """Histograms of step wall time over the run's median step time, one panel per size."""
    import numpy as np
    from matplotlib.colors import to_rgba
    from matplotlib.patches import Patch
    from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator
    lay = _Layout(runs, xkey)
    panels = _step_panels(runs, xkey, lay)
    ratios = {}
    for r in runs:
        steps = [t for t in r.log.step_times[1:] if t > 0]      # the first step is not typical
        if steps:
            ratios[r.label] = np.array(steps) / statistics.median(steps)
    n = len(panels)
    ncols = min(n, 5)
    nrows = math.ceil(n / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(3.7 * ncols + 0.8, 3.4 * nrows + 1.6),
                             sharex=True, sharey=True, squeeze=False)
    fig.subplots_adjust(left=0.07 if ncols > 2 else 0.12, right=0.985, top=0.8 if nrows == 1
                        else 0.88, bottom=0.2 if nrows == 1 else 0.13, wspace=0.12, hspace=0.35)
    for ax in axes.flat[n:]:
        ax.axis("off")
    if not ratios:
        axes[0, 0].text(0.5, 0.5, "no completed steps in the logs", ha="center",
                        transform=axes[0, 0].transAxes, color=INK2)
        return _finish(fig, "Step wall time, relative to each run's median", timer_note=False)

    allr = np.concatenate(list(ratios.values()))
    # Always room for the median (1) and the spike threshold, whatever the data does.
    lo = min(allr.min(), 0.87)
    hi = max(allr.max(), analysis.SPIKE_FACTOR * 1.15)
    edges = np.geomspace(lo, hi, 51)
    floor = 0.5 / max(len(v) for v in ratios.values())          # half a step: the log-axis bottom
    k0, k1 = math.floor(math.log2(lo)), math.ceil(math.log2(hi))
    for ax, (title, members) in zip(axes.flat, panels):
        for r, g in members:
            if r.label not in ratios:
                continue
            frac = np.histogram(ratios[r.label], edges)[0] / len(ratios[r.label])
            frac = np.maximum(frac, floor)
            c = _color(g)
            ax.stairs(frac, edges, baseline=floor, fill=True, color=to_rgba(c, 0.28))
            ax.stairs(frac, edges, color=c, linewidth=1.3)
        ax.axvline(1.0, color=MUTED, linewidth=1.0, zorder=1)
        ax.axvline(analysis.SPIKE_FACTOR, color=MUTED, linewidth=1.0, linestyle="--", zorder=1)
        ax.set_xscale("log", base=2)
        ax.set_yscale("log")
        ax.set_xlim(edges[0], edges[-1])
        ax.set_ylim(floor, 1.0)
        ax.xaxis.set_major_locator(FixedLocator([2.0 ** k for k in range(k0, k1 + 1)]))
        ax.xaxis.set_minor_locator(NullLocator())
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}×"))
        ax.set_title(title)
    for ax in axes[-1]:
        ax.set_xlabel("step time / run median")
    for ax in axes[:, 0]:
        ax.set_ylabel("fraction of steps")
    if len(lay.keys) > 1:
        axes[0, 0].legend(handles=[Patch(color=_color(i), label=t)
                                   for i, t in enumerate(lay.tags)], loc="upper right")
    notes = ["Step times are rank 0's, first step excluded. Solid line: the run's median; "
             f"dashed: spike threshold ({analysis.SPIKE_FACTOR:g}× median).",
             *analysis.caveats(runs)]
    return _finish(fig, "Step wall time, relative to each run's median", notes,
                   timer_note=False)


def build_figures(plt, runs, xkey="ranks"):
    """{name: Figure}. One run: overview + components. Several: study views."""
    if len(runs) == 1:
        return {"overview": fig_overview(plt, runs[0]),
                "components": fig_components(plt, runs[0])}
    figs = {}
    if analysis.is_scaling_study(runs):
        figs["scaling"] = fig_scaling(plt, runs, xkey)
    figs["breakdown"] = fig_breakdown(plt, runs, xkey)
    figs["imbalance"] = fig_imbalance(plt, runs, xkey)
    figs["imbalance_components"] = fig_imbalance_components(plt, runs, xkey)
    figs["quality"] = fig_quality(plt, runs, xkey)
    figs["steps"] = fig_steps(plt, runs, xkey)
    return figs


# ---- command line ----------------------------------------------------------------------

def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="plot-timing.py",
        description="Summarize and plot MPAS timing from log.atmosphere.0000.out. Takes a "
                    "log file, an experiment directory, or a study directory (any tree "
                    "holding logs). Shows interactive windows unless --save is given.")
    p.add_argument("paths", nargs="+", metavar="PATH",
                   help="A log file or a directory searched for log.atmosphere.0000.out.")
    p.add_argument("--save", metavar="DIR",
                   help="Write PNG figures plus results.csv and timers.csv to DIR instead of "
                        "opening windows (works without a display).")
    p.add_argument("--format", choices=("png", "pdf", "svg"), default="png",
                   help="Figure file format for --save (default png).")
    p.add_argument("--no-csv", action="store_true", help="With --save, skip the CSV files.")
    p.add_argument("--x", choices=("ranks", "nodes"), default="ranks",
                   help="Study-plot x axis (default ranks). 'nodes' needs provenance.yaml.")
    return p.parse_args(argv)


def run(argv):
    args = parse_args(argv)
    runs = analysis.load_runs(args.paths)
    print(f"{len(runs)} run(s):")
    print_summary(runs)
    xkey, note = _x_choice(runs, args.x)
    if note:
        print(f"  note: {note}")

    if args.save and not args.no_csv:
        for path in write_csv(runs, args.save):
            print(f"wrote {path}")

    plt = _load_pyplot(bool(args.save))
    figs = build_figures(plt, runs, xkey)
    if args.save:
        outdir = Path(args.save)
        outdir.mkdir(parents=True, exist_ok=True)
        for name, fig in figs.items():
            path = outdir / f"{name}.{args.format}"
            fig.savefig(path)
            print(f"wrote {path}")
        plt.close("all")
    else:
        plt.show()


def main(argv=None):
    try:
        run(sys.argv[1:] if argv is None else argv)
    except SetupError as e:
        sys.exit(f"Error: {e}")
    except OSError as e:
        sys.exit(f"Error: {e}")
