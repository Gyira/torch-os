"""网页版分享链接取回器的守卫：只认 chat.deepseek.com，只收问答，不碰思考过程。"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import share_link  # noqa: E402


def payload_with(messages: list[dict]) -> dict:
    return {"code": 0, "msg": "", "data": {"biz_code": 0, "biz_data": {"title": "Shared Conversation", "messages": messages}}}


def message(role: str, *fragments: tuple[str, str], message_id: int = 1, at: float = 1792637351.0) -> dict:
    return {
        "message_id": message_id,
        "role": role,
        "inserted_at": at,
        "fragments": [
            {"id": index, "type": kind, "content": content}
            for index, (kind, content) in enumerate(fragments, 1)
        ],
    }


def test_parse_share_id_accepts_links_api_urls_and_bare_ids() -> None:
    assert share_link.parse_share_id("https://chat.deepseek.com/share/2ohjq7tc2q1mtgb6pq") == "2ohjq7tc2q1mtgb6pq"
    assert share_link.parse_share_id("  https://chat.deepseek.com/share/2ohjq7tc2q1mtgb6pq。\n") == "2ohjq7tc2q1mtgb6pq"
    assert share_link.parse_share_id(
        "https://chat.deepseek.com/api/v0/share/content?share_id=c1uhg26rqlvtzdbw7n") == "c1uhg26rqlvtzdbw7n"
    assert share_link.parse_share_id("2ohjq7tc2q1mtgb6pq") == "2ohjq7tc2q1mtgb6pq"
    assert share_link.parse_share_id("<https://chat.deepseek.com/share/abcd1234>") == "abcd1234"

    with pytest.raises(share_link.ShareLinkError):
        share_link.parse_share_id("")
    with pytest.raises(share_link.ShareLinkError):
        share_link.parse_share_id("https://evil.example/share/abcd1234")
    with pytest.raises(share_link.ShareLinkError):
        share_link.parse_share_id("javascript:alert(1)")
    with pytest.raises(share_link.ShareLinkError):
        share_link.parse_share_id("这不是链接")


def test_extract_messages_drops_thinking_tools_and_tips() -> None:
    payload = payload_with([
        message("USER", ("REQUEST", "第一个问题"), ("THINK", "不该出现的思考")),
        message("ASSISTANT",
                ("THINK", "内部推理"),
                ("TOOL_SEARCH", "搜到一堆网页"),
                ("RESPONSE", "第一个回答"),
                ("TIP", "AI 生成，仅供参考")),
        message("USER", ("REQUEST", "   ") , ("REQUEST", "第二个问题")),
        message("ASSISTANT", ("RESPONSE", "")),
    ])

    messages = share_link.extract_messages(payload)

    assert [item["role"] for item in messages] == ["user", "assistant", "user"]
    assert [item["text"] for item in messages] == ["第一个问题", "第一个回答", "第二个问题"]
    assert messages[0]["at"] is not None


def test_rounds_filter_uses_original_numbers_and_reports_range_errors() -> None:
    payload = payload_with([
        message("USER", ("REQUEST", "第一问")),
        message("ASSISTANT", ("RESPONSE", "第一答")),
        message("USER", ("REQUEST", "第二问")),
        message("ASSISTANT", ("RESPONSE", "第二答")),
    ])
    messages = share_link.extract_messages(payload)

    selected, picked, total = share_link.select_rounds(messages, "2")
    assert picked == [2] and total == 2
    assert [item["text"] for item in selected] == ["第二问", "第二答"]

    markdown = share_link.render_markdown(selected, "abc123", rounds_note="（只取第 2 轮，原文共 2 轮）")
    assert "https://chat.deepseek.com/share/abc123" in markdown
    assert "## 第 2 轮 · 我问" in markdown
    assert "## 第 1 轮" not in markdown
    assert "只取第 2 轮" in markdown

    assert share_link.parse_round_spec("1,2", 2) == [1, 2]
    assert share_link.parse_round_spec("1-2", 2) == [1, 2]
    with pytest.raises(share_link.ShareLinkError):
        share_link.parse_round_spec("3", 2)
    with pytest.raises(share_link.ShareLinkError):
        share_link.parse_round_spec("0", 2)
    with pytest.raises(share_link.ShareLinkError):
        share_link.parse_round_spec("第一轮", 2)


def test_title_is_a_short_single_line_from_the_first_question() -> None:
    messages = [
        {"role": "user", "text": "**怎样**把\n深搜的答案\n整理成笔记？" + "很长的补充" * 30, "at": None},
        {"role": "assistant", "text": "回答", "at": None},
    ]
    title = share_link.conversation_title(messages)
    assert "\n" not in title
    assert "*" not in title
    assert title.endswith("…")
    assert len(title) <= share_link.TITLE_LIMIT + 1
    assert share_link.conversation_title([{"role": "assistant", "text": "只有回答"}]) == "DeepSeek 网页版对话"


def test_fetch_payload_maps_api_errors_and_html_to_friendly_failures() -> None:
    seen: dict[str, str] = {}

    def ok(request: httpx.Request) -> httpx.Response:
        seen["share_id"] = request.url.params.get("share_id", "")
        return httpx.Response(200, json=payload_with([message("USER", ("REQUEST", "问题"))]))

    with httpx.Client(transport=httpx.MockTransport(ok)) as client:
        payload = share_link.fetch_payload("abc12345", client=client)
    assert seen["share_id"] == "abc12345"
    assert payload["code"] == 0

    def denied(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 10001, "msg": "分享不存在", "data": None})

    with httpx.Client(transport=httpx.MockTransport(denied)) as client:
        with pytest.raises(share_link.ShareLinkError) as error:
            share_link.fetch_payload("abc12345", client=client)
    assert "分享不存在" in str(error.value)

    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as client:
        with pytest.raises(share_link.ShareLinkError):
            share_link.fetch_payload("abc12345", client=client)

    with httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, headers={"content-type": "text/html"}, text="<html></html>"))) as client:
        with pytest.raises(share_link.ShareLinkError):
            share_link.fetch_payload("abc12345", client=client)


def test_summary_reports_turns_and_sizes_without_dumping_text() -> None:
    payload = payload_with([
        message("USER", ("REQUEST", "第一问")),
        message("ASSISTANT", ("RESPONSE", "答" * 500)),
        message("USER", ("REQUEST", "第二问")),
        message("ASSISTANT", ("RESPONSE", "答" * 200)),
    ])
    summary = share_link.summarize(share_link.extract_messages(payload), "abc123")
    assert summary["share_url"] == "https://chat.deepseek.com/share/abc123"
    assert summary["turns"] == 2
    assert summary["chars"] == 3 + 500 + 3 + 200
    assert all(len(item["preview"]) <= 80 for item in summary["messages"])
