"""Find or generate the METIS graph partition for a resolution and rank count."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import REPO, SetupError

METIS = REPO / "utils" / "METIS" / "install"
GKLIB = REPO / "utils" / "GKlib" / "install"
GPMETIS_ARGS = ["-minconn", "-contig", "-niter=200"]


def partition_dir(res):
    return REPO / "partition" / res.key


def partition_file(res, nranks):
    return partition_dir(res) / f"{res.partition_prefix}{nranks}"


def find_gpmetis():
    local = METIS / "bin" / "gpmetis"
    if local.exists():
        return str(local)
    found = shutil.which("gpmetis")
    if found:
        return found
    raise SetupError("gpmetis not found. Build it with utils/build-metis.sh "
                     "(it needs the Intel compilers loaded).")


def _gpmetis_env():
    libs = [str(p) for base in (METIS, GKLIB) for p in (base / "lib", base / "lib64")
            if p.is_dir()]
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = ":".join(libs + [env["LD_LIBRARY_PATH"]]
                                      if env.get("LD_LIBRARY_PATH") else libs)
    return env


def needs_partition(res, nranks):
    # MPAS runs a single rank without reading a decomposition file.
    return nranks > 1 and not partition_file(res, nranks).exists()


def ensure_partition(res, nranks, dry_run=False):
    """Return the gpmetis command that was run (or would be), or None if the
    partition already exists or isn't needed."""
    if not needs_partition(res, nranks):
        return None
    graph = partition_dir(res) / res.graph_file
    if not graph.exists():
        raise SetupError(f"Cannot generate a partition: {graph} does not exist.")
    cmd = ["gpmetis", *GPMETIS_ARGS, res.graph_file, str(nranks)]
    shown = " ".join(cmd)
    if dry_run:
        return shown

    exe = find_gpmetis()
    print(f"  Generating partition for {nranks} ranks: {shown}")
    # Work in a scratch directory next to the graph so a failed or interrupted
    # run never leaves a half-written partition file in place.
    with tempfile.TemporaryDirectory(dir=str(graph.parent), prefix=".gpmetis-") as tmp:
        (Path(tmp) / res.graph_file).symlink_to(graph)
        subprocess.run([exe, *cmd[1:]], cwd=tmp, env=_gpmetis_env(), check=True)
        produced = Path(tmp) / f"{res.partition_prefix}{nranks}"
        if not produced.exists():
            raise SetupError(f"gpmetis did not produce {produced.name}.")
        produced.replace(partition_file(res, nranks))
    return shown
