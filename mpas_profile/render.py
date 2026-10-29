"""Render the Jinja2 templates in templates/."""

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from . import REPO

_env = Environment(
    loader=FileSystemLoader(str(REPO / "templates")),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def modulefile(profile):
    return _env.get_template("ursa.intel.lua.j2").render(profile=profile)


def job(exp, mpas_bin):
    return _env.get_template("job.fcst.j2").render(
        name=exp.name, tag=exp.tag, profile=exp.profile, account=exp.account,
        qos=exp.qos, nodes=exp.nodes, tasks_per_node=exp.tasks_per_node,
        ntasks=exp.ntasks, cpus_per_task=exp.cpus_per_task,
        walltime=exp.walltime, mpas_bin=mpas_bin)


def namelist(exp, res):
    return _env.get_template("namelist.atmosphere.j2").render(
        dt=res.dt, start_time=exp.start_time, run_duration=exp.run_duration,
        partition_prefix=res.partition_prefix)


def streams(res):
    return _env.get_template("streams.atmosphere.j2").render(
        stem=res.stem, mesh=res.mesh)
