"""Public API; importing the package does not initialize the research runtime."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autohedge.main import AutoHedge

__all__ = ["AutoHedge"]


def __getattr__(name: str):
    if name == "AutoHedge":
        from autohedge.main import AutoHedge

        return AutoHedge
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
