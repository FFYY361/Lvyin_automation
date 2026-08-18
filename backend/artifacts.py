"""Content-addressed website artifact storage."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from io import BytesIO
from pathlib import Path, PurePosixPath

from PIL import Image, UnidentifiedImageError

MAX_COVER_BYTES = 10 * 1024 * 1024
_FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "GIF": ("gif", "image/gif"),
}
_REPORT_KINDS = ("image", "text")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def validate_cover(content: bytes) -> tuple[str, str, str]:
    if not content:
        raise ValueError("cover image is empty")
    if len(content) > MAX_COVER_BYTES:
        raise ValueError("cover image exceeds 10 MiB")
    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
            resolved = _FORMATS.get(image.format or "")
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("cover must be a valid JPEG, PNG or GIF image") from exc
    if resolved is None:
        raise ValueError("cover must be a JPEG, PNG or GIF image")
    extension, content_type = resolved
    return extension, content_type, sha256_bytes(content)


def save_cover(root: Path, content: bytes) -> tuple[str, str]:
    extension, content_type, fingerprint = validate_cover(content)
    relative = PurePosixPath("covers") / f"{fingerprint}.{extension}"
    destination = resolve_storage_key(root, relative.as_posix())
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(content)
            temporary = Path(stream.name)
        try:
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return relative.as_posix(), content_type


def save_report(root: Path, content: bytes, *, extension: str) -> tuple[str, str]:
    if extension not in {"png", "txt"}:
        raise ValueError("report extension must be png or txt")
    if not content:
        raise ValueError("report content is empty")
    if extension == "png":
        try:
            with Image.open(BytesIO(content)) as image:
                image.verify()
                if image.format != "PNG":
                    raise ValueError("report image must be PNG")
        except (OSError, UnidentifiedImageError) as exc:
            raise ValueError("report image must be a valid PNG") from exc
    else:
        try:
            content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("report text must be UTF-8") from exc
    fingerprint = sha256_bytes(content)
    relative = PurePosixPath("reports") / f"{fingerprint}.{extension}"
    destination = resolve_report_storage_key(root, relative.as_posix())
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        with tempfile.NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(content)
            temporary = Path(stream.name)
        try:
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    return relative.as_posix(), fingerprint


def report_storage_descriptor(storage_keys: Mapping[str, str]) -> tuple[str, str]:
    if not storage_keys or any(key not in _REPORT_KINDS for key in storage_keys):
        raise ValueError("report artifacts must contain image and/or text")
    ordered = {key: storage_keys[key] for key in _REPORT_KINDS if key in storage_keys}
    for kind, storage_key in ordered.items():
        expected_suffix = ".png" if kind == "image" else ".txt"
        if not isinstance(storage_key, str) or not storage_key.endswith(expected_suffix):
            raise ValueError(f"report {kind} storage key has the wrong extension")
    descriptor = json.dumps(ordered, ensure_ascii=False, separators=(",", ":"))
    return descriptor, sha256_bytes(descriptor.encode("utf-8"))


def parse_report_storage_descriptor(value: str) -> dict[str, str]:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid report storage descriptor") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid report storage descriptor")
    try:
        descriptor, _fingerprint = report_storage_descriptor(payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid report storage descriptor") from exc
    if descriptor != value:
        raise ValueError("report storage descriptor is not canonical")
    return payload


def resolve_storage_key(root: Path, storage_key: str) -> Path:
    relative = PurePosixPath(storage_key)
    if (
        relative.is_absolute()
        or not relative.parts
        or relative.parts[0] != "covers"
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("invalid cover storage key")
    resolved_root = root.resolve()
    resolved = resolved_root.joinpath(*relative.parts).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("cover storage key leaves artifact root") from exc
    return resolved


def resolve_report_storage_key(root: Path, storage_key: str) -> Path:
    relative = PurePosixPath(storage_key)
    if (
        relative.is_absolute()
        or len(relative.parts) != 2
        or relative.parts[0] != "reports"
        or relative.suffix not in {".png", ".txt"}
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise ValueError("invalid report storage key")
    resolved_root = root.resolve()
    resolved = resolved_root.joinpath(*relative.parts).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("report storage key leaves artifact root") from exc
    return resolved
