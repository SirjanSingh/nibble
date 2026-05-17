"""PyInstaller entrypoint — equivalent to `python -m nibble`."""
from nibble.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
