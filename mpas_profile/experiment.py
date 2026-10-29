"""Stage an experiment directory, record provenance, and submit it."""

import datetime
import hashlib
import shutil
import subprocess
from pathlib import Path

import yaml

from . import REPO, SetupError, build, partition, render


def data_dirs(res):
    return [REPO / "ic" / res.key, REPO / "fix" / res.key]


def check_data(res):
    for d in data_dirs(res):
        if not d.is_dir():
            raise SetupError(f"Missing data directory for resolution '{res.key}': {d}")
    if not (partition.partition_dir(res) / res.graph_file).exists():
        raise SetupError(f"Missing graph file: "
                         f"{partition.partition_dir(res) / res.graph_file}")


def _link_contents(src_dir, dest):
    for src in sorted(src_dir.iterdir()):
        (dest / src.name).symlink_to(src)


def profile_repo_state():
    head = build.git("rev-parse", "HEAD", check=False).stdout.strip()
    dirty = bool(build.git("status", "--porcelain", check=False).stdout.strip())
    return {"commit": head or None, "dirty": dirty}


def stage(exp, res, edir, bdir, mpas_info, gpmetis_cmd):
    """Create the experiment directory contents (everything except the job id)."""
    edir.mkdir(parents=True)
    mod_text = render.modulefile(exp.profile)

    (edir / "namelist.atmosphere").write_text(render.namelist(exp, res))
    (edir / "streams.atmosphere").write_text(render.streams(res))
    (edir / "job.fcst").write_text(render.job(exp, build.mpas_bin(bdir)))
    (edir / "modulefiles").mkdir()
    (edir / "modulefiles" / "ursa.intel.lua").write_text(mod_text)

    # Small config files are copied so the experiment can't drift if the repo changes.
    for f in sorted((REPO / "config" / "mpas").glob("stream_list.*")):
        shutil.copy(f, edir)
    shutil.copy(REPO / "utils" / "module-setup.sh", edir)
    if exp.profile:
        shutil.copy(REPO / "config" / "scalasca" / "scorep.filt", edir)

    # Large data is linked.
    for d in data_dirs(res):
        _link_contents(d, edir)
    if exp.ntasks > 1:
        pf = partition.partition_file(res, exp.ntasks)
        (edir / pf.name).symlink_to(pf)

    prov = {
        "experiment": {k: v for k, v in vars(exp).items()},
        "mpas": mpas_info,
        "profile_mpas": profile_repo_state(),
        "modulefile_sha256": hashlib.sha256(mod_text.encode()).hexdigest(),
        "gpmetis": gpmetis_cmd,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    write_provenance(edir, prov)
    return prov


def write_provenance(edir, prov):
    (edir / "provenance.yaml").write_text(yaml.safe_dump(prov, sort_keys=False))


def submit(edir):
    r = subprocess.run(["sbatch", "--parsable", "job.fcst"], cwd=str(edir),
                       check=True, stdout=subprocess.PIPE, universal_newlines=True)
    jobid = r.stdout.strip().split(";")[0]
    (edir / "jobid").write_text(jobid + "\n")
    return jobid
