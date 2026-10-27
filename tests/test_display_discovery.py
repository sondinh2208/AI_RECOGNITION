import asyncio
import hashlib
import hmac
import json
import unittest
from unittest.mock import Mock

from display_discovery import DiscoveryProtocol, REQUEST_PREFIX, discovery_reply


class DiscoveryTests(unittest.TestCase):
    def test_signature_requires_same_token_and_fresh_nonce(self):
        nonce = "0123456789abcdef" * 2
        reply = discovery_reply(nonce, "display-secret")
        self.assertEqual(reply["display_name"], "FaceTouch Pro")
        self.assertEqual(reply["service"], "FaceCheck TFT display")  # Existing TFT compatibility.
        expected = hmac.new(b"display-secret",
                            f"facecheck-display-v1|{nonce}|8767".encode(),
                            hashlib.sha256).hexdigest()
        self.assertEqual(reply["signature"], expected)
        self.assertNotEqual(reply["signature"], discovery_reply(nonce, "wrong")["signature"])
        self.assertNotEqual(reply["signature"], discovery_reply("f" * 32, "display-secret")["signature"])
        self.assertNotIn("display-secret", json.dumps(reply))

    def test_malformed_requests_and_unconfigured_token_are_silent(self):
        for nonce in (None, "", "f" * 31, "F" * 32, "g" * 32, "f" * 1000):
            self.assertIsNone(discovery_reply(nonce, "token"))
        self.assertIsNone(discovery_reply("f" * 32, ""))
        protocol = DiscoveryProtocol("token")
        transport = Mock()
        protocol.connection_made(transport)
        for packet in (b"hello", REQUEST_PREFIX, REQUEST_PREFIX + b"g" * 32):
            protocol.datagram_received(packet, ("127.0.0.1", 1234))
        transport.sendto.assert_not_called()


class DiscoveryNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_udp_request_round_trip(self):
        loop = asyncio.get_running_loop()
        server, _ = await loop.create_datagram_endpoint(
            lambda: DiscoveryProtocol("token"), local_addr=("127.0.0.1", 0))
        received = loop.create_future()

        class Client(asyncio.DatagramProtocol):
            def datagram_received(self, data, _address):
                if not received.done():
                    received.set_result(json.loads(data))

        client, _ = await loop.create_datagram_endpoint(
            Client, local_addr=("127.0.0.1", 0))
        try:
            client.sendto(REQUEST_PREFIX + b"a" * 32, server.get_extra_info("sockname"))
            reply = await asyncio.wait_for(received, 2)
            self.assertEqual(reply, discovery_reply("a" * 32, "token"))
        finally:
            client.close()
            server.close()


if __name__ == "__main__":
    unittest.main()
