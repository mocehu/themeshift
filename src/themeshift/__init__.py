"""themeshift - cross-platform icon theme migration toolkit.

The project is intentionally split into two layers:

* behaviour lives in this package (readers, writers, imaging, mapping);
* knowledge lives in ``themeshift/data`` as plain JSON (platform profiles and
  package mappings).

That separation is what makes new platforms additive: drop in a profile, a
mapping file and an implementation class, and the CLI can target it.
"""

from __future__ import annotations

from importlib import metadata

try:
    __version__ = metadata.version("themeshift")
except metadata.PackageNotFoundError:  # running from a source checkout
    __version__ = "1.2.0"

__all__ = ["__version__"]
