import sys
import time
import unittest
from pathlib import Path


CHATMOCK_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(CHATMOCK_ROOT))

from chatmock.http import wrap_sse_stream_with_heartbeat


class SseHeartbeatTests(unittest.TestCase):
    def test_wrap_sse_stream_with_heartbeat_emits_keepalive_when_idle(self):
        def slow_stream():
            yield b"data: first\n\n"
            time.sleep(1.2)
            yield b"data: second\n\n"

        chunks = list(
            wrap_sse_stream_with_heartbeat(
                slow_stream(),
                interval_seconds=1.0,
            )
        )

        self.assertGreaterEqual(len(chunks), 3)
        self.assertEqual(chunks[0], b"data: first\n\n")
        self.assertIn(b": keep-alive\n\n", chunks)
        self.assertEqual(chunks[-1], b"data: second\n\n")


if __name__ == "__main__":
    unittest.main()
