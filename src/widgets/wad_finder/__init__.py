"""WAD finder/browser package.

Public surface is re-exported here; implementation lives in the submodules:

- ``constants``/``models``/``text``/``sources``/``parsing``/``library``:
  Qt-free, unit-testable helpers.
- ``workers``: QRunnable search/download/discovery workers and their signals.
- ``widget``: the ``WadFinder`` QWidget.
"""

from __future__ import annotations

from .models import Source, SourceStatus, WadBrowserResult
from .widget import WadFinder

__all__ = ["WadFinder", "WadBrowserResult", "Source", "SourceStatus"]
