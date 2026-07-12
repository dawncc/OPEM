"""Backward-compatible entry point for older Stop/PreCompact configs."""
from capture import main


if __name__ == "__main__":
    raise SystemExit(main())
