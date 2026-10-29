"""Build, partition, and submit MPAS profiling studies from a setup YAML."""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class SetupError(Exception):
    """A problem with the study setup that the user can fix."""
