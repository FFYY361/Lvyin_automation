import asyncio
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from thufootball.qr_cache import GameQRCodeCache


def test_qr_cache_reuses_and_repairs(tmp_path):
    output = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(output, format="JPEG")
    payload = output.getvalue()
    calls = []

    async def fetch(game_id):
        calls.append(game_id)
        return payload

    cache = GameQRCodeCache(tmp_path / "var" / "qrcodes")
    client = SimpleNamespace(get_game_page_code=fetch)
    assert asyncio.run(cache.get(client, 4377)) == payload
    assert asyncio.run(GameQRCodeCache(cache.directory).get(client, 4377)) == payload
    assert calls == [4377]
    (cache.directory / "4377.img").write_bytes(b"broken")
    assert asyncio.run(cache.get(client, 4377)) == payload
    assert calls == [4377, 4377]

    async def invalid(_game_id):
        return b"not an image"

    with pytest.raises(OSError):
        asyncio.run(cache.get(SimpleNamespace(get_game_page_code=invalid), 4380))
    assert not (cache.directory / "4380.img").exists()
