"""Allow ``python -m themeshift`` to behave like the ``themeshift`` command."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
