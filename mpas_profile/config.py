"""Load a study YAML, validate it, and expand it into experiments."""

import copy
import itertools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from . import REPO, SetupError

# Everything an experiment (or `defaults`) may set. Adding a key to the setup
# YAML means adding it here and using it in a template.
DEFAULTS = {
    "profile": "scalasca",
    "cpus_per_task": 1,
    "walltime": "00:40:00",
    "account": "fv3-cpu",
    "qos": "batch",
    "start_time": "2026-1-26_00:00:00",
    "run_duration": "7_00:00:00",
    "output": True,
}
EXPERIMENT_KEYS = set(DEFAULTS) | {
    "name", "resolution", "nodes", "tasks_per_node", "ntasks", "mpas", "pio",
}
# `scalasca` builds with the Score-P wrappers and runs under scalasca. `native` is a plain
# build that relies on MPAS's own timers (which write to the log in every mode). The old
# boolean form is still accepted: true -> scalasca, false -> native.
PROFILE_MODES = ("scalasca", "native")
_PROFILE_ALIASES = {True: "scalasca", False: "native"}
MPAS_KEYS = {"ref", "repo"}
PIO_KEYS = {"num_iotasks", "stride", "iotasks_per_node", "io_type"}
# Values MPAS accepts for the io_type stream attribute.
IO_TYPES = ("pnetcdf", "pnetcdf,cdf5", "netcdf", "netcdf4")
TOP_KEYS = {"study", "defaults", "experiments"}


@dataclass
class Experiment:
    name: str
    resolution: str
    nodes: int
    ntasks: int                       # total MPI ranks
    tasks_per_node: Optional[int]     # None when the study gave a total ntasks
    cpus_per_task: int
    walltime: str
    account: str
    qos: str
    profile: str                      # one of PROFILE_MODES
    start_time: str
    run_duration: str
    mpas_ref: Optional[str]           # None means the commit pinned by the submodule
    mpas_repo: Optional[str]          # None means the MPAS-Model submodule
    output: bool = True               # False turns off restart, history and diagnostics
    pio_num_iotasks: int = 0          # config_pio_num_iotasks; 0 = every rank does I/O
    pio_stride: int = 1               # config_pio_stride
    pio_iotasks_per_node: Optional[int] = None  # set when num_iotasks/stride were derived
    pio_io_type: Optional[str] = None  # io_type on the output streams; None = MPAS default

    @property
    def scorep(self):
        """True when MPAS is built with the Score-P wrappers and run under scalasca."""
        return self.profile == "scalasca"

    @property
    def tag(self):
        return "prof" if self.scorep else "native"


@dataclass
class Study:
    name: str
    experiments: list
    raw: dict


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def set_path(d, dotted, value):
    """d['a.b'] = v  ->  d['a']['b'] = v"""
    *parents, leaf = dotted.split(".")
    for p in parents:
        d = d.setdefault(p, {})
    d[leaf] = value


def _axes(value, label):
    """Validate a `sweep`/`zip` mapping of key -> non-empty list of values."""
    if not isinstance(value, dict) or not value:
        raise SetupError(f"'{label}' must be a mapping of key -> list of values.")
    for k, v in value.items():
        if not isinstance(v, list) or not v:
            raise SetupError(f"{label} '{k}' must be a non-empty list.")
    return value


def expand_entry(entry):
    """Expand one `experiments:` entry.

    A `sweep` is a cartesian product of its lists. A `zip` pairs its lists by
    position (all must be the same length). With both, each zipped tuple is
    crossed with the sweep product.
    """
    entry = copy.deepcopy(entry)
    sweep = entry.pop("sweep", None)
    zipped = entry.pop("zip", None)
    if sweep is None and zipped is None:
        return [entry]
    sweep = _axes(sweep, "sweep") if sweep is not None else {}
    zipped = _axes(zipped, "zip") if zipped is not None else {}

    lengths = {k: len(v) for k, v in zipped.items()}
    if len(set(lengths.values())) > 1:
        detail = ", ".join(f"'{k}' has {n}" for k, n in lengths.items())
        raise SetupError(f"zip lists must all be the same length: {detail}.")
    both = sorted(set(sweep) & set(zipped))
    if both:
        raise SetupError(f"key(s) in both 'sweep' and 'zip': {', '.join(both)}.")

    sweep_keys, zip_keys = list(sweep), list(zipped)
    n_zip = next(iter(lengths.values()), 1)  # one pass with no keys when there is no zip
    out = []
    for combo in itertools.product(*(sweep[k] for k in sweep_keys)):
        for i in range(n_zip):
            e = copy.deepcopy(entry)
            for k, v in zip(sweep_keys, combo):
                set_path(e, k, v)
            for k in zip_keys:
                set_path(e, k, zipped[k][i])
            out.append(e)
    return out


def slug(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-")


def _positive_int(spec, key, where):
    v = spec.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise SetupError(f"{where}: '{key}' must be a positive integer (got {v!r}).")
    return v


def resolve_pio(pio, nodes, ntasks, tpn, where):
    """Validate the `pio` mapping; return (num_iotasks, stride, iotasks_per_node, io_type)."""
    if not isinstance(pio, dict) or set(pio) - PIO_KEYS:
        raise SetupError(f"{where}: 'pio' may only contain: {', '.join(sorted(PIO_KEYS))}")

    io_type = pio.get("io_type")
    if io_type is not None and io_type not in IO_TYPES:
        raise SetupError(f"{where}: pio.io_type must be one of "
                         f"{', '.join(IO_TYPES)} (got {io_type!r}).")

    per_node = pio.get("iotasks_per_node")
    if per_node is not None:
        if "num_iotasks" in pio or "stride" in pio:
            raise SetupError(f"{where}: pio.iotasks_per_node cannot be combined with "
                             "pio.num_iotasks or pio.stride.")
        if isinstance(per_node, bool) or not isinstance(per_node, int) or per_node < 0:
            raise SetupError(f"{where}: pio.iotasks_per_node must be a non-negative integer "
                             f"(got {per_node!r}).")
        if per_node == 0:   # every rank does I/O: the MPAS default, same as no pio settings
            return 0, 1, None, io_type
        k = per_node
        if tpn is None:
            if ntasks % nodes:
                raise SetupError(f"{where}: pio.iotasks_per_node needs an even rank count "
                                 f"per node, but ntasks ({ntasks}) is not divisible by "
                                 f"nodes ({nodes}).")
            tpn = ntasks // nodes
        if k > tpn:
            raise SetupError(f"{where}: pio.iotasks_per_node ({k}) exceeds the "
                             f"{tpn} ranks per node.")
        # k I/O ranks on every node: ranks 0, stride, 2*stride, ... with block placement.
        return nodes * k, tpn // k, k, io_type

    num = pio.get("num_iotasks", 0)
    if isinstance(num, bool) or not isinstance(num, int) or num < 0:
        raise SetupError(f"{where}: pio.num_iotasks must be a non-negative integer "
                         f"(got {num!r}).")
    stride = _positive_int({"stride": pio.get("stride", 1)}, "stride", where)
    if num > ntasks:
        raise SetupError(f"{where}: pio.num_iotasks ({num}) exceeds the rank count ({ntasks}).")
    if num > 0 and num * stride > ntasks:
        raise SetupError(f"{where}: pio.num_iotasks * pio.stride ({num} * {stride}) "
                         f"exceeds the rank count ({ntasks}).")
    return num, stride, None, io_type


def build_experiment(spec, where):
    unknown = set(spec) - EXPERIMENT_KEYS
    if unknown:
        raise SetupError(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")
    mpas = spec.get("mpas") or {}
    if not isinstance(mpas, dict) or set(mpas) - MPAS_KEYS:
        raise SetupError(f"{where}: 'mpas' may only contain: {', '.join(sorted(MPAS_KEYS))}")

    spec = {**DEFAULTS, **spec}
    if not isinstance(spec["output"], bool):
        raise SetupError(f"{where}: 'output' must be true or false.")
    profile = spec["profile"]
    if isinstance(profile, bool):
        profile = _PROFILE_ALIASES[profile]
    if profile not in PROFILE_MODES:
        raise SetupError(f"{where}: 'profile' must be one of "
                         f"{', '.join(PROFILE_MODES)} (got {profile!r}).")
    spec["profile"] = profile
    if not spec.get("resolution"):
        raise SetupError(f"{where}: 'resolution' is required.")

    nodes = _positive_int(spec, "nodes", where)
    cpt = _positive_int(spec, "cpus_per_task", where)
    has_tpn, has_nt = "tasks_per_node" in spec, "ntasks" in spec
    if has_tpn == has_nt:
        raise SetupError(f"{where}: give exactly one of 'tasks_per_node' or 'ntasks'.")
    if has_tpn:
        tpn = _positive_int(spec, "tasks_per_node", where)
        ntasks = nodes * tpn
    else:
        tpn = None
        ntasks = _positive_int(spec, "ntasks", where)
        if ntasks < nodes:
            raise SetupError(f"{where}: ntasks ({ntasks}) is less than nodes ({nodes}).")

    pio_n, pio_stride, pio_pn, io_type = resolve_pio(
        spec.get("pio") or {}, nodes, ntasks, tpn, where)

    ref = mpas.get("ref")
    name = spec.get("name")
    if not name:
        shape = f"n{nodes}x{tpn}" if tpn else f"n{nodes}t{ntasks}"
        parts = [spec["resolution"], slug(ref or "pinned"),
                 "prof" if profile == "scalasca" else profile, shape]
        # Only non-default I/O settings appear, so existing names are unchanged.
        if not spec["output"]:
            parts.append("noout")
        if pio_pn:
            parts.append(f"io{pio_pn}pn")
        elif pio_n or pio_stride != 1:
            parts.append(f"io{pio_n}s{pio_stride}")
        if io_type:
            parts.append(slug(io_type))
        name = "_".join(parts)
    if slug(name) != name:
        raise SetupError(f"{where}: name '{name}' has characters unsafe for a directory.")

    return Experiment(
        name=name, resolution=str(spec["resolution"]), nodes=nodes, ntasks=ntasks,
        tasks_per_node=tpn, cpus_per_task=cpt, walltime=str(spec["walltime"]),
        account=spec["account"], qos=spec["qos"], profile=spec["profile"],
        start_time=spec["start_time"], run_duration=spec["run_duration"],
        mpas_ref=None if ref is None else str(ref), mpas_repo=mpas.get("repo"),
        output=spec["output"], pio_num_iotasks=pio_n, pio_stride=pio_stride,
        pio_iotasks_per_node=pio_pn, pio_io_type=io_type,
    )


def load_study(path):
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as e:
        raise SetupError(f"Cannot read '{path}': {e}")
    if not isinstance(raw, dict):
        raise SetupError(f"'{path}' must contain a YAML mapping.")
    unknown = set(raw) - TOP_KEYS
    if unknown:
        raise SetupError(f"Unknown top-level key(s): {', '.join(sorted(unknown))}")

    name = raw.get("study")
    if not name or slug(name) != str(name):
        raise SetupError("'study' is required and must be a directory-safe name.")
    defaults = raw.get("defaults") or {}
    entries = raw.get("experiments")
    if not isinstance(defaults, dict) or not isinstance(entries, list) or not entries:
        raise SetupError("Need a 'defaults' mapping and a non-empty 'experiments' list.")

    experiments = []
    for i, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            raise SetupError(f"experiments[{i}] must be a mapping.")
        for j, spec in enumerate(expand_entry(entry), 1):
            experiments.append(build_experiment(
                deep_merge(defaults, spec), f"experiments[{i}] (#{j})"))

    names = [e.name for e in experiments]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise SetupError(f"Duplicate experiment name(s): {', '.join(dupes)}. "
                         "Set 'name' to tell them apart.")
    return Study(name=str(name), experiments=experiments, raw=raw)


@dataclass
class Resolution:
    key: str
    stem: str
    mesh: str
    dt: float

    @property
    def graph_file(self):
        return f"{self.stem}.graph.info"

    @property
    def partition_prefix(self):
        return f"{self.graph_file}.part."


def load_resolutions(path=None):
    path = Path(path) if path else REPO / "config" / "resolutions.yaml"
    raw = yaml.safe_load(path.read_text()) or {}
    out = {}
    for key, v in raw.items():
        try:
            out[str(key)] = Resolution(str(key), v["stem"], v["mesh"], float(v["dt"]))
        except (KeyError, TypeError, ValueError) as e:
            raise SetupError(f"{path}: bad entry for '{key}': {e!r}")
    return out
