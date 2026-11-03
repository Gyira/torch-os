"""WeFlow 媒体翻页：短页不能当作“历史到底”，否则全量同步会卡在几千条。"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app import weflow

BASE = datetime(2026, 10, 1, 12, 0, 0)


def make_messages(count: int) -> list[weflow.WeFlowMessage]:
    return [
        weflow.WeFlowMessage(
            platform_message_id=str(index),
            sender_id="wxid",
            sender="胡丽洁",
            timestamp=BASE + timedelta(minutes=index),
            message_type=1,
            content=f"消息 {index}",
            is_send=False,
            is_system=False,
        )
        for index in range(count)
    ]


def ts(message: weflow.WeFlowMessage) -> int:
    return int(message.timestamp.replace(tzinfo=weflow.TZ).timestamp())


class FakeClient:
    """模拟 WeFlow 媒体端点：end/offset 时间窗翻页；可让指定调用返回短页。"""

    def __init__(self, messages, *, short_calls=None):
        self.messages = sorted(messages, key=ts, reverse=True)
        self.short_calls = dict(short_calls or {})
        self.calls = 0

    def pull_with_media(self, session_id, *, since=0, offset=0, limit=1000, end=0):
        self.calls += 1
        candidates = [
            message for message in self.messages
            if ts(message) >= since and (not end or ts(message) <= end)
        ]
        page = candidates[offset:offset + limit]
        short = self.short_calls.get(self.calls)
        if short is not None:
            page = page[:short]
        return weflow.WeFlowPull(
            owner_id="", members={}, messages=page, raw_count=len(page))


class DescendingPagerTests(unittest.TestCase):
    def setUp(self):
        self._empty_attempts = weflow.EMPTY_PAGE_ATTEMPTS
        self._retry_delay = weflow.RETRY_DELAY
        weflow.EMPTY_PAGE_ATTEMPTS = 1
        weflow.RETRY_DELAY = 0
        self.addCleanup(self._restore)

    def _restore(self):
        weflow.EMPTY_PAGE_ATTEMPTS = self._empty_attempts
        weflow.RETRY_DELAY = self._retry_delay

    def collect(self, client, *, since=0):
        pages = list(weflow.iter_pages(
            client, "wxid", since=since, limit=10, with_media=True))
        messages = [message for page in pages for message in page.messages]
        return pages, messages

    def test_short_page_in_the_middle_does_not_stop_history(self):
        client = FakeClient(make_messages(25), short_calls={2: 2})
        pages, messages = self.collect(client)
        self.assertEqual(len({ts(message) for message in messages}), 25)
        self.assertEqual(sorted(ts(message) for message in messages),
                         sorted(ts(message) for message in client.messages))
        self.assertGreaterEqual(client.calls, 4)

    def test_true_end_still_stops_after_an_empty_page(self):
        client = FakeClient(make_messages(7))
        pages, messages = self.collect(client)
        self.assertEqual(len(messages), 7)
        # 第一页短于 limit 后仍会再确认一次是否真的没有更早消息。
        self.assertEqual(client.calls, 2)

    def test_cursor_that_never_moves_older_does_not_loop(self):
        messages = make_messages(12)

        class StuckClient(FakeClient):
            def pull_with_media(self, session_id, *, since=0, offset=0, limit=1000, end=0):
                self.calls += 1
                # 忽略 end，始终返回最新 10 条：游标无法向更旧推进。
                page = self.messages[:limit]
                return weflow.WeFlowPull(
                    owner_id="", members={}, messages=page, raw_count=len(page))

        client = StuckClient(messages)
        _pages, collected = self.collect(client)
        self.assertLessEqual(client.calls, 2)
        self.assertTrue(collected)

    def test_incremental_stops_at_since_boundary(self):
        messages = make_messages(25)
        since = ts(messages[10])
        client = FakeClient(messages)
        _pages, collected = self.collect(client, since=since)
        self.assertTrue(collected)
        self.assertTrue(all(ts(message) >= since for message in collected))
        self.assertLessEqual(client.calls, 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
