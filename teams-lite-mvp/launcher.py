"""PyInstaller entry point.

PyInstaller runs its entry script as a top-level module, outside the teams_lite package,
so this file must use absolute imports (src/teams_lite/__main__.py cannot be used here).
"""
from teams_lite.app import main

if __name__ == "__main__":
    raise SystemExit(main())
