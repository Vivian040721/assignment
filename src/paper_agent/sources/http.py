"""Small GET retry helper; no authentication values in exception messages."""

import asyncio

import httpx


async def get_with_retry(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    for attempt in range(3):
        try:
            response = await client.get(url, **kwargs)
        except httpx.TransportError as error:
            if attempt == 2:
                raise RuntimeError(
                    f"Source transport failed after 3 attempts ({type(error).__name__})"
                ) from None
            await asyncio.sleep(3.1 * (attempt + 1))
            continue
        if response.status_code == 429 or response.status_code >= 500:
            if attempt < 2:
                await asyncio.sleep(3.1 * (attempt + 1))
                continue
        if response.is_error:
            raise RuntimeError(f"Source returned HTTP {response.status_code}")
        return response
    raise RuntimeError("Source request failed")
