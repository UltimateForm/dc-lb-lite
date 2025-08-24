import asyncio
from common import logger
from rcon.rcon import RconClient


class RconConnectionPool:
    _pool: asyncio.Queue[RconClient]
    _in_use: set[RconClient]

    def __init__(self, max_size: int = 10) -> None:
        self._max_size = max_size
        self._pool: asyncio.Queue[RconClient] = asyncio.Queue(maxsize=max_size)
        self._lock = asyncio.Lock()
        self._in_use: set[RconClient] = set()

    async def get_client(self) -> RconClient:
        async with self._lock:
            total_clients = len(self._in_use) + self._pool.qsize()
            logger.debug(f"Total clients: {total_clients}")
            if total_clients < self._max_size:
                client = RconClient()
                logger.debug(f"Created client {client.id}, authenticating...")
                await client.authenticate()
                self._in_use.add(client)
                return client
        if self._pool.empty():
            logger.info("All clients busy, waiting...")
        client = await self._pool.get()
        logger.debug(f"Polled client {client.id} from pool")
        while client is not None and client.age_since_used > 60:
            logger.debug(f"Client {client.id} stale, dropping...")
            client._writer.close()
            client = None
            if not self._pool.empty():
                client = await self._pool.get()
                logger.debug(f"Polled freshier client {client.id} from pool")
        if client is None:
            logger.info("No fresh clients available, recursing...")
            return await self.get_client()
        async with self._lock:
            self._in_use.add(client)
        return client

    async def release_client(self, client: RconClient) -> None:
        async with self._lock:
            total_clients = len(self._in_use) + self._pool.qsize()
            logger.debug(
                f"releasing client {client.id}, total clients: {total_clients}"
            )
            if client in self._in_use:
                self._in_use.remove(client)
                logger.debug("Releasing RCON client")
                await self._pool.put(client)
            else:
                logger.error(
                    f"Attempted to release a client ({client.id}) not part of the pool"
                )

    async def close_client(self, client: RconClient):
        async with self._lock:
            try:
                if client in self._in_use:
                    self._in_use.remove(client)
                client._writer.close()
                await client._writer.wait_closed()
            except Exception as e:
                logger.error(
                    f"Attempted to close client {client.id}, failed with error {e}"
                )
                pass

    async def close_all(self) -> None:
        async with self._lock:
            while not self._pool.empty():
                client = await self._pool.get()
                client._writer.close()
                await client._writer.wait_closed()
            for client in self._in_use:
                client._writer.close()
                await client._writer.wait_closed()
            self._in_use.clear()
            self._pool = asyncio.Queue(maxsize=self._max_size)
