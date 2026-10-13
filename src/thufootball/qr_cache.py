"""Persistent, per-game mini-program image cache."""

from __future__ import annotations

import asyncio
import io
import os
import tempfile
from pathlib import Path

from PIL import Image

from .client import THUFootballClient


class GameQRCodeCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @staticmethod
    def _validate(payload: bytes) -> None:
        with Image.open(io.BytesIO(payload)) as image:
            image.load()

    def _read(self, game_id: int) -> bytes | None:
        path = self.directory / f"{game_id}.img"
        try:
            payload = path.read_bytes()
            self._validate(payload)
        except (OSError, ValueError):
            return None
        return payload

    def _write(self, game_id: int, payload: bytes) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.directory, delete=False
            ) as output:
                temporary_path = Path(output.name)
                output.write(payload)
            os.replace(temporary_path, self.directory / f"{game_id}.img")
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    async def get(self, client: THUFootballClient, game_id: int) -> bytes:
        payload = await asyncio.to_thread(self._read, game_id)
        if payload is None:
            payload = await client.get_game_page_code(game_id)
            self._validate(payload)
            await asyncio.to_thread(self._write, game_id, payload)
        return payload
