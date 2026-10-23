"""Owner 09: stable result order, bounded concurrency, per-item failures."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


async def process_items(
    items: list[T], operation: Callable[[T], Awaitable[R]], mode: str, concurrency: int
) -> list[R | Exception]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(item: T) -> R | Exception:
        async with semaphore:
            try:
                return await operation(item)
            except Exception as error:
                return error

    if mode == "serial":
        return [await one(item) for item in items]
    if mode == "concurrent":
        return list(await asyncio.gather(*(one(item) for item in items)))
    raise ValueError("Processing mode must be serial or concurrent")
