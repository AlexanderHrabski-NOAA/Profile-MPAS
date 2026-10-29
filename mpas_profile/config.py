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
    "profile": True,
    "cpus_per_task": 1,
    "walltime": "00:40:00",
    "account": "fv3-cpu",
    "qos": "batch",
    "start_time": "2026-1-26_00:00:00",
    "run_duration": "7_00:00:00",
}
EXPERIMENT_KEYS = set(DEFAULTS) | {
    "name", "resolution", "nodes", "tasks_per_node", "ntasks", "mpas",
}
MPAS_KEYS = {"ref", "repo"}
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
    profile: bool
    start_time: str
    run_duration: str
    mpas_ref: Optional[str]           # None means the commit pinned by the submodule
    mpas_repo: Optional[str]          # None means the MPAS-Model submodule

    @property
    def tag(self):
        return "prof" if self.profile else "norm"


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


def expand_entry(entry):
    """Expand one `experiments:` entry. A `sweep` is a cartesian product."""
    entry = copy.deepcopy(entry)
    sweep = entry.pop("sweep", None)
    if sweep is None:
        return [entry]
    if not isinstance(sweep, dict) or not sweep:
        raise SetupError("'sweep' must be a mapping of key -> list of values.")
    for k, v in sweep.items():
        if not isinstance(v, list) or not v:
            raise SetupError(f"sweep '{k}' must be a non-empty list.")
    keys = list(sweep)
    out = []
    for combo in itertools.product(*(sweep[k] for k in keys)):
        e = copy.deepcopy(entry)
        for k, v in zip(keys, combo):
            set_path(e, k, v)
        out.append(e)
    return out


def slug(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-")


def _positive_int(spec, key, where):
    v = spec.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or v < 1:
        raise SetupError(f"{where}: '{key}' must be a positive integer (got {v!r}).")
    return v


def build_experiment(spec, where):
    unknown = set(spec) - EXPERIMENT_KEYS
    if unknown:
        raise SetupError(f"{where}: unknown key(s): {', '.join(sorted(unknown))}")
    mpas = spec.get("mpas") or {}
    if not isinstance(mpas, dict) or set(mpas) - MPAS_KEYS:
        raise SetupError(f"{where}: 'mpas' may only contain: {', '.join(sorted(MPAS_KEYS))}")

    spec = {**DEFAULTS, **spec}
    if not isinstance(spec["profile"], bool):
        raise SetupError(f"{where}: 'profile' must be true or false.")
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

    ref = mpas.get("ref")
    name = spec.get("name")
    if not name:
        shape = f"n{nodes}x{tpn}" if tpn else f"n{nodes}t{ntasks}"
        name = "_".join([spec["resolution"], slug(ref or "pinned"),
                         "prof" if spec["profile"] else "norm", shape])
    if slug(name) != name:
        raise SetupError(f"{where}: name '{name}' has characters unsafe for a directory.")

    return Experiment(
        name=name, resolution=str(spec["resolution"]), nodes=nodes, ntasks=ntasks,
        tasks_per_node=tpn, cpus_per_task=cpt, walltime=str(spec["walltime"]),
        account=spec["account"], qos=spec["qos"], profile=spec["profile"],
        start_time=spec["start_time"], run_duration=spec["run_duration"],
        mpas_ref=None if ref is None else str(ref), mpas_repo=mpas.get("repo"),
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
