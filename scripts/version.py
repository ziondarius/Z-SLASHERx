from __future__ import annotations


VERSION_NAME = "Character Edition"
VERSION_MAJOR = 1
VERSION_MINOR = 0


def get_version_label() -> str:
    return f"{VERSION_NAME} v{VERSION_MAJOR}.{VERSION_MINOR}"
