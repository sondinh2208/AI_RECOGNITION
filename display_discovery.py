"""Token-signed LAN discovery for FaceCheck TFT; never broadcasts the token."""

import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import logging
import os
import re
from branding import APP_NAME

DISCOVERY_PORT = 8768
DISPLAY_PORT = 8767
REQUEST_PREFIX = b"FACECHECK_DISCOVER_V1 "


def discovery_reply(nonce, token):
    if not token or not isinstance(nonce, str) or not re.fullmatch("[0-9a-f]{32}", nonce):
        return None
    message = f"facecheck-display-v1|{nonce}|{DISPLAY_PORT}"
    return {
        "service": "FaceCheck TFT display",
        "display_name": APP_NAME,
        "port": DISPLAY_PORT,
        "nonce": nonce,
        "signature": hmac.new(token.encode(), message.encode(), hashlib.sha256).hexdigest(),
    }


class DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, token):
        self.token = token

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, address):
        if not data.startswith(REQUEST_PREFIX) or len(data) != len(REQUEST_PREFIX) + 32:
            return
        reply = discovery_reply(data[len(REQUEST_PREFIX):].decode("ascii", errors="replace"), self.token)
        if reply:
            self.transport.sendto(json.dumps(reply).encode(), address)


@asynccontextmanager
async def discovery_lifespan(_app):
    transport = None
    try:
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: DiscoveryProtocol(os.getenv("FACECHECK_DISPLAY_TOKEN", "")),
            local_addr=("0.0.0.0", DISCOVERY_PORT),
            allow_broadcast=True,
        )
    except OSError as exc:
        # TCP /discover remains available when UDP is blocked or occupied.
        logging.getLogger(__name__).warning("TFT UDP discovery unavailable: %s", exc)
    try:
        yield
    finally:
        if transport:
            transport.close()
