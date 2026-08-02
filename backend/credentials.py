"""Runtime THUFootball credential persistence for the website."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from thufootball.config import CREDENTIAL_ENV_NAMES


def mask_secret(value: str) -> str | None:
    """Return a useful status hint without exposing a credential."""

    if not value:
        return None
    visible = value[-4:] if len(value) > 4 else value[-1:]
    return f"{'*' * 8}{visible}"


def credential_status() -> dict[str, object]:
    """Describe the credentials currently used by newly created clients."""

    openid, session_key = (
        os.environ.get(name, "") for name in CREDENTIAL_ENV_NAMES
    )
    return {
        "configured": bool(openid and session_key),
        "openid_masked": mask_secret(openid),
        "session_key_masked": mask_secret(session_key),
    }


def persist_credentials(path: Path, openid: str, session_key: str) -> None:
    """Atomically replace both credential entries in a dotenv file."""

    original = path.read_bytes().decode("utf-8-sig") if path.is_file() else ""
    newline = "\r\n" if "\r\n" in original else "\n"
    had_final_newline = original.endswith(("\n", "\r"))
    replacements = dict(zip(CREDENTIAL_ENV_NAMES, (openid, session_key), strict=True))
    found: set[str] = set()
    lines: list[str] = []
    for line in original.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            name = stripped.split("=", 1)[0].strip()
            if name in replacements:
                lines.append(f"{name}={replacements[name]}")
                found.add(name)
                continue
        lines.append(line)
    for name in CREDENTIAL_ENV_NAMES:
        if name not in found:
            lines.append(f"{name}={replacements[name]}")

    content = newline.join(lines)
    if lines and (had_final_newline or not original):
        content += newline

    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def activate_credentials(openid: str, session_key: str) -> None:
    """Make a persisted credential pair visible to future clients."""

    os.environ[CREDENTIAL_ENV_NAMES[0]] = openid
    os.environ[CREDENTIAL_ENV_NAMES[1]] = session_key
