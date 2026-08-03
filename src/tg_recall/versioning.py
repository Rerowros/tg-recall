"""Runtime package-version lookup without access to archive state."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as distribution_version


DISTRIBUTION_NAME = "tg-recall"


def runtime_package_version() -> str:
    """Return the installed distribution version, with a source-tree fallback.

    Importing package metadata neither loads profile configuration nor touches
    archive data.  The fallback keeps editable/source-tree use usable when
    distribution metadata is temporarily unavailable.
    """

    try:
        return distribution_version(DISTRIBUTION_NAME)
    except PackageNotFoundError:
        # Import lazily to avoid a package-import cycle during startup.
        from . import __version__

        return __version__
