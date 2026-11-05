"""ACP 图片通道客户端：用假 ACP 服务验证协议、图片块、超时与错误。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import acp_client
from backend.app.dsh_web import DshCommand

FAKE_SERVER = r'''
import json
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    message = json.loads(line)
    request_id = message.get("id")
    method = message.get("method")
    if method == "initialize":
        capabilities = {"promptCapabilities": {"image": mode != "noimage"}}
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": 1, "agentCapabilities": capabilities}}), flush=True)
    elif method == "session/new":
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {
            "sessionId": "sess_fake"}}), flush=True)
    elif method == "session/prompt":
        blocks = message.get("params", {}).get("prompt", [])
        images = [item for item in blocks if item.get("type") == "image"]
        if mode == "hang":
            time.sleep(30)
        elif mode != "empty":
            print(json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {
                "sessionId": "sess_fake",
                "update": {"sessionUpdate": "agent_message_chunk",
                           "content": {"type": "text", "text": "FAKE ACP " + str(len(images))}},
            }}), flush=True)
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {
            "stopReason": "end_turn"}}), flush=True)
    elif method == "session/close":
        print(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {}}), flush=True)
'''


class AcpClientTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="torch-acp-test-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.script = self.root / "fake_acp.py"
        self.script.write_text(FAKE_SERVER, encoding="utf-8")
        self.workspace = self.root / "workspace"
        self.image = {"image_bytes": b"\x89PNG\r\n\x1a\nfake", "media_type": "image/png"}

    def run_prompt(self, mode: str = "ok", timeout: float = 10):
        return acp_client.run_prompt(
            "请读图", [self.image], workspace=self.workspace,
            environ=dict(os.environ), timeout=timeout,
            command=DshCommand((sys.executable, str(self.script), mode), "custom"),
        )

    def test_prompt_sends_image_and_collects_update(self):
        result = self.run_prompt()
        self.assertEqual(result.text, "FAKE ACP 1")
        self.assertEqual(result.stop_reason, "end_turn")
        self.assertGreaterEqual(result.seconds, 0)

    def test_timeout_kills_round(self):
        with self.assertRaises(acp_client.AcpTimeout):
            self.run_prompt("hang", timeout=1)

    def test_empty_answer_is_an_error(self):
        with self.assertRaises(acp_client.AcpProtocolError):
            self.run_prompt("empty")

    def test_missing_image_capability_is_rejected(self):
        with self.assertRaises(acp_client.AcpUnavailable):
            self.run_prompt("noimage")


if __name__ == "__main__":
    unittest.main(verbosity=2)
