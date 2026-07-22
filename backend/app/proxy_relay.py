"""Expose a host-loopback HTTP/SOCKS proxy only to local Docker networks."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import socket
import struct


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("buyerreach.proxy_relay")

LISTEN_HOST = os.getenv("PROXY_RELAY_LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("PROXY_RELAY_LISTEN_PORT", "17892"))
UPSTREAM_PORT = int(os.getenv("PROXY_RELAY_UPSTREAM_PORT", "17891"))
ALLOWED_NETWORKS = tuple(
    ipaddress.ip_network(value.strip())
    for value in os.getenv("PROXY_RELAY_ALLOWED_CIDRS", "172.16.0.0/12").split(",")
    if value.strip()
)


def _linux_default_gateway(route_path: str = "/proc/net/route") -> str:
    """Return the IPv4 default gateway exposed to the WSL Linux network."""
    with open(route_path, encoding="ascii") as route_file:
        next(route_file, None)
        for line in route_file:
            fields = line.split()
            if len(fields) < 4 or fields[1] != "00000000":
                continue
            flags = int(fields[3], 16)
            if flags & 0x2:
                return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    raise RuntimeError("No IPv4 default gateway found")


def _resolve_upstream_host(configured_host: str) -> str:
    if configured_host == "wsl-default-gateway":
        return _linux_default_gateway()
    return configured_host


UPSTREAM_HOST = _resolve_upstream_host(
    os.getenv("PROXY_RELAY_UPSTREAM_HOST", "127.0.0.1")
)


def _client_allowed(peer: object) -> bool:
    if not isinstance(peer, tuple) or not peer:
        return False
    try:
        address = ipaddress.ip_address(str(peer[0]))
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address in network for network in ALLOWED_NETWORKS)


async def _copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while chunk := await reader.read(64 * 1024):
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except ConnectionError:
            pass


async def _handle(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    peer = client_writer.get_extra_info("peername")
    if not _client_allowed(peer):
        logger.warning("Rejected proxy relay client %s", peer)
        client_writer.close()
        await client_writer.wait_closed()
        return
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(UPSTREAM_HOST, UPSTREAM_PORT)
    except OSError as exc:
        logger.error("Cannot reach upstream proxy %s:%s: %s", UPSTREAM_HOST, UPSTREAM_PORT, exc)
        client_writer.close()
        await client_writer.wait_closed()
        return
    await asyncio.gather(
        _copy(client_reader, upstream_writer),
        _copy(upstream_reader, client_writer),
    )


async def main() -> None:
    server = await asyncio.start_server(_handle, LISTEN_HOST, LISTEN_PORT)
    logger.info(
        "Proxy relay listening on %s:%s -> %s:%s; allowed=%s",
        LISTEN_HOST,
        LISTEN_PORT,
        UPSTREAM_HOST,
        UPSTREAM_PORT,
        ",".join(str(network) for network in ALLOWED_NETWORKS),
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
