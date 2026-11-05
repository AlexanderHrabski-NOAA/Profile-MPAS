"""Parse an MPAS-Atmosphere log (log.atmosphere.0000.out) into a RunLog.

MPAS writes its own timing information to the log of every run, whether or not it
is profiled:

* ``Timing for integration step: X s`` after every time step (this rank's wall time),
* ``Timing for stream output`` / ``diagnostic computation`` / ``read of`` for I/O,
* a timer tree at shutdown (``Timer information``), reduced over all ranks.

Only rank 0's log is needed: the timer tree is global, and the per-step lines of
the other ranks differ by little because every step ends in a synchronization.
Standard library only, so this module works anywhere.

The timer tree is fixed-width Fortran output (``mpas_timer.F``), and timer names
contain spaces, so rows are cut by column, not split on whitespace. The values are
the global *maximum* over ranks of each timer, so a parent's children can add up to
slightly more than the parent. Treat shares computed from the tree as approximate.
"""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

LOG_NAME = "log.atmosphere.0000.out"

# One row of the timer table:
#   i2, 1x, a45 name (indented one space per level), f15.5 total, i10 calls,
#   3 x f15.5 (min, max, avg), 1x, f8.2 pct_tot, 3x, f8.2 pct_par, 3x, f8.2 par_eff
# MPAS puts one leading space on every message.
_ROW = re.compile(
    r"^\s*(?P<level>\d+) (?P<name>.{45})(?P<total>.{15})(?P<calls>.{10})"
    r"(?P<min>.{15})(?P<max>.{15})(?P<avg>.{15}) (?P<pct_tot>.{8})   "
    r"(?P<pct_par>.{8})   (?P<par_eff>.{8})\s*$")

_STEP = re.compile(r"Timing for integration step:\s*([-+0-9.eE]+) s")
_BEGIN = re.compile(r"Begin timestep (\d+)-(\d+)-(\d+)_(\d+):(\d+):(\d+)")
_WSP = re.compile(r"global max wsp:\s*([-+0-9.eE]+)")
_STREAM_OUT = re.compile(r"Timing for stream output:\s*([-+0-9.eE]+) s")
_DIAG = re.compile(r"Timing for diagnostic computation:\s*([-+0-9.eE]+) s")
_STREAM_IN = re.compile(r"Timing for stream input:\s*([-+0-9.eE]+) s")
_READ = re.compile(r"Timing for read of '([^']*)' stream:\s*([-+0-9.eE]+) s")
_TASKS = re.compile(r"Output Log File for task\s+(\d+) of\s+(\d+)")
_MPI_COUNT = re.compile(r"MPI task count:\s*(\d+)")
_STAMP = re.compile(r"(\d{4})/(\d\d)/(\d\d) (\d\d):(\d\d):(\d\d)")
_DIM = re.compile(r"^\s*(nCells|nVertLevels)\s*=\s*(\d+)\s*$")
_FOOTER = {
    "output": re.compile(r"^\s*Output messages =\s*(\d+)"),
    "warnings": re.compile(r"^\s*Warning messages =\s*(\d+)"),
    "errors": re.compile(r"^\s*Error messages =\s*(\d+)"),
    "criticals": re.compile(r"^\s*Critical error messages =\s*(\d+)"),
}


@dataclass
class TimerRow:
    level: int            # 1 = the root ("total time")
    name: str
    path: str             # names below the root joined by "/", e.g. "time integration/physics driver"
    total: float          # seconds: max over ranks (and threads) of the accumulated time
    calls: int
    min: float            # shortest single start/stop over all ranks
    max: float            # longest single start/stop over all ranks
    avg: float
    pct_tot: float
    pct_par: float
    par_eff: float        # mean over ranks of total / max over ranks of total (1 = balanced)


@dataclass
class RunLog:
    path: str
    version: Optional[str] = None         # e.g. "8.4.2-noaa"
    describe: Optional[str] = None        # e.g. "MPAS-v8.4.2-1.19"
    build_target: Optional[str] = None
    precision: Optional[str] = None
    openmp: Optional[bool] = None
    io_layer: Optional[str] = None
    ntasks: Optional[int] = None
    opened: Optional[datetime] = None
    closed: Optional[datetime] = None
    ncells: Optional[int] = None
    nlevels: Optional[int] = None
    step_stamps: list = field(default_factory=list)   # model time at the start of each step
    step_times: list = field(default_factory=list)    # wall seconds of each completed step
    max_wsp: list = field(default_factory=list)       # max wind speed (m/s) after each step
    stream_output: list = field(default_factory=list)  # wall seconds of each output write
    diagnostics: list = field(default_factory=list)   # wall seconds of each diagnostics pass
    stream_input: list = field(default_factory=list)
    reads: dict = field(default_factory=dict)         # stream name -> seconds
    timers: list = field(default_factory=list)        # TimerRow, depth-first as printed
    messages: dict = field(default_factory=dict)      # footer counts, if the run closed its log
    finished: bool = False                            # "Finished running the atmosphere core"
    logging_complete: bool = False

    @property
    def nsteps(self):
        return len(self.step_times)

    @property
    def completed(self):
        """The run reached the end of the model and wrote its timer table."""
        return self.finished and self.logging_complete and bool(self.timers)

    @property
    def dt(self):
        """Model time step in seconds, from the step time stamps (None for < 2 steps)."""
        s = self.step_stamps
        if len(s) < 2:
            return None
        gaps = sorted((b - a).total_seconds() for a, b in zip(s, s[1:]))
        return gaps[len(gaps) // 2]

    @property
    def sim_seconds(self):
        dt = self.dt
        return None if dt is None else dt * self.nsteps

    @property
    def log_wall(self):
        """Seconds between the log being opened and closed, if both are known."""
        if self.opened and self.closed:
            return (self.closed - self.opened).total_seconds()
        return None

    def timer(self, name):
        """The first timer whose path or leaf name equals `name` (None if absent)."""
        for t in self.timers:
            if t.path == name:
                return t
        for t in self.timers:
            if t.name == name:
                return t
        return None

    def children(self, name):
        """Direct children of the timer `name`, in printed order."""
        parent = self.timer(name)
        if parent is None:
            return []
        out = []
        # The table is depth first: a parent's subtree is the run of deeper rows after it.
        for t in self.timers[self.timers.index(parent) + 1:]:
            if t.level <= parent.level:
                break
            if t.level == parent.level + 1:
                out.append(t)
        return out


def _num(text):
    """Fortran fixed-width field -> float; overflow stars or blanks give NaN."""
    try:
        return float(text)
    except ValueError:
        return math.nan


def _stamp(m):
    try:
        return datetime(*(int(g) for g in m.groups()))
    except ValueError:
        return None


def _row(m, stack):
    level = int(m.group("level"))
    name = m.group("name").strip()
    del stack[level - 1:]
    # A level can skip if a log is damaged; pad so the path stays well formed.
    while len(stack) < level - 1:
        stack.append("?")
    stack.append(name)
    calls = _num(m.group("calls"))
    # Paths leave out the root ("total time"), which is every timer's ancestor.
    path = "/".join(stack[1:]) if level > 1 else name
    return TimerRow(
        level=level, name=name, path=path,
        total=_num(m.group("total")), calls=0 if math.isnan(calls) else int(calls),
        min=_num(m.group("min")), max=_num(m.group("max")), avg=_num(m.group("avg")),
        pct_tot=_num(m.group("pct_tot")), pct_par=_num(m.group("pct_par")),
        par_eff=_num(m.group("par_eff")))


def parse_log(path):
    """Parse one log file. A truncated log (timeout, crash) parses as far as it goes."""
    run = RunLog(path=str(path))
    stack = []
    # 0: before the table; 1: in the column legend, waiting for the header; 2: reading rows
    table = 0
    with open(path, errors="replace") as f:
        for line in f:
            if table == 1:
                if line.strip().startswith("timer_name"):
                    table = 2
                continue
            if table == 2:
                m = _ROW.match(line)
                if m:
                    run.timers.append(_row(m, stack))
                    continue
                if not line.strip():
                    continue
                table = 0                  # the dashed footer: parse it as usual below

            if "Timer information" in line:
                table = 1
                continue
            if "Begin timestep" in line:
                m = _BEGIN.search(line)
                if m:
                    try:
                        run.step_stamps.append(datetime(*(int(g) for g in m.groups())))
                    except ValueError:
                        pass
                continue
            if "Timing for integration step" in line:
                m = _STEP.search(line)
                if m:
                    run.step_times.append(float(m.group(1)))
                continue
            if "global max wsp" in line:
                m = _WSP.search(line)
                if m:
                    run.max_wsp.append(float(m.group(1)))
                continue
            if "Timing for" in line:
                for rx, dest in ((_STREAM_OUT, run.stream_output), (_DIAG, run.diagnostics),
                                 (_STREAM_IN, run.stream_input)):
                    m = rx.search(line)
                    if m:
                        dest.append(float(m.group(1)))
                        break
                else:
                    m = _READ.search(line)
                    if m:
                        run.reads[m.group(1)] = float(m.group(2))
                continue
            _header_and_footer(run, line)
    return run


def _header_and_footer(run, line):
    """Everything that is not a per-step or table line."""
    if "Opened at" in line:
        m = _STAMP.search(line)
        if m and run.opened is None:
            run.opened = _stamp(m)
    elif "Closing file at" in line:
        m = _STAMP.search(line)
        if m:
            run.closed = _stamp(m)
        if "Logging complete" in line:
            run.logging_complete = True
    elif "Finished running the atmosphere core" in line:
        run.finished = True
    elif "Output Log File for task" in line:
        m = _TASKS.search(line)
        if m and run.ntasks is None:
            run.ntasks = int(m.group(2))
    elif "MPI task count" in line:
        m = _MPI_COUNT.search(line)
        if m:
            run.ntasks = int(m.group(1))
    elif "MPAS-Atmosphere Version" in line:
        run.version = line.split("Version", 1)[1].strip()
    elif "Output from 'git describe" in line:
        run.describe = line.split("':", 1)[-1].strip()
    elif "Build target:" in line:
        run.build_target = line.split(":", 1)[1].strip()
    elif "Default real precision:" in line:
        run.precision = line.split(":", 1)[1].strip()
    elif "OpenMP support:" in line:
        run.openmp = line.split(":", 1)[1].strip().lower() == "yes"
    elif "I/O layer:" in line:
        run.io_layer = line.split(":", 1)[1].strip()
    elif "= " in line:
        m = _DIM.match(line)
        if m:
            if m.group(1) == "nCells" and run.ncells is None:
                run.ncells = int(m.group(2))
            elif m.group(1) == "nVertLevels" and run.nlevels is None:
                run.nlevels = int(m.group(2))
            return
        for key, rx in _FOOTER.items():
            m = rx.match(line)
            if m:
                run.messages[key] = int(m.group(1))
                return
