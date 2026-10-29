"""Clone MPAS at a ref and build it. One build per (commit, profile)."""

import shutil
import subprocess
from pathlib import Path

from . import REPO, SetupError, render

DEFAULT_SOURCE = REPO / "MPAS-Model"
STAMP = ".build-ok"

# `module` is a shell function, so loading modules and building has to happen
# in a single bash process. $1 is the clone. MPAS's build.sh reads the
# modulefile in the clone's own modulefiles/ directory.
BUILD_SCRIPT = """
type module >/dev/null 2>&1 || source /apps/lmod/lmod/init/bash
set -e
module use "$1/modulefiles"
module load mpas/ursa.intel
cd "$1"
./build.sh
"""


def git(*args, cwd=REPO, check=True):
    return subprocess.run(["git", *args], cwd=str(cwd), check=check,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


def pinned_commit():
    """The MPAS commit this repo's submodule is pinned to."""
    r = git("rev-parse", "HEAD:MPAS-Model", check=False)
    if r.returncode:
        raise SetupError("No 'mpas.ref' given and the MPAS-Model submodule pin "
                         f"could not be read: {r.stderr.strip()}")
    return r.stdout.strip()


def resolve_ref(source, ref):
    """Return the full SHA for ref (branch, tag, or hash) in the source repo."""
    source = Path(source)
    if not (source / ".git").exists() and not (source / "objects").exists():
        raise SetupError(
            f"MPAS source '{source}' is not a git repository. Run "
            "`git submodule update --init`, or set 'mpas.repo' in the setup YAML.")
    # Prefer the remote-tracking branch; submodules rarely have local branches.
    candidates = [f"origin/{ref}", ref]

    def lookup():
        for c in candidates:
            r = git("rev-parse", "--verify", "--quiet", f"{c}^{{commit}}",
                    cwd=source, check=False)
            if r.returncode == 0:
                return r.stdout.strip()

    sha = lookup()
    if sha is None:
        print(f"  '{ref}' not found locally in {source}; fetching...")
        git("fetch", "--tags", "origin", cwd=source, check=False)
        sha = lookup()
    if sha is None:
        raise SetupError(f"Cannot resolve MPAS ref '{ref}' in '{source}'.")
    return sha


def resolve(exp):
    """Return (source repo, full SHA) for an experiment's MPAS spec."""
    source = Path(exp.mpas_repo).expanduser() if exp.mpas_repo else DEFAULT_SOURCE
    sha = resolve_ref(source, exp.mpas_ref) if exp.mpas_ref else pinned_commit()
    return source, sha


def build_dir(study_dir, sha, profile):
    return Path(study_dir) / "builds" / f"{sha[:12]}-{'prof' if profile else 'norm'}"


def mpas_bin(bdir):
    return Path(bdir) / "build" / "bin" / "mpas_atmosphere"


def ensure_build(bdir, source, sha, profile, rebuild=False):
    """Clone and build unless a finished build already exists."""
    bdir = Path(bdir)
    if (bdir / STAMP).exists() and not rebuild:
        print(f"  Reusing build {bdir.name}")
        return
    if bdir.exists():
        shutil.rmtree(bdir)
    print(f"  Cloning {source} @ {sha[:12]} -> {bdir}")
    bdir.parent.mkdir(parents=True, exist_ok=True)
    git("clone", "--quiet", "--no-checkout", str(source), str(bdir))
    git("checkout", "--quiet", sha, cwd=bdir)

    # build.sh reads the modulefile in the clone, so the rendered one goes there.
    mod = bdir / "modulefiles" / "mpas" / "ursa.intel.lua"
    mod.parent.mkdir(parents=True, exist_ok=True)
    mod.write_text(render.modulefile(profile))

    print(f"  Building {bdir.name} ({'with' if profile else 'without'} Scorep wrappers)")
    subprocess.run(["bash", "-c", BUILD_SCRIPT, "build", str(bdir)], check=True)
    if not mpas_bin(bdir).exists():
        raise SetupError(f"Build finished but {mpas_bin(bdir)} does not exist.")
    (bdir / STAMP).write_text(sha + "\n")
