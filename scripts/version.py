from __future__ import annotations


VERSION_NAME = "Character Update"
VERSION_MAJOR = 3
VERSION_MINOR = 0


def get_version_label() -> str:
    return f"{VERSION_NAME} v{VERSION_MAJOR}.{VERSION_MINOR}"
