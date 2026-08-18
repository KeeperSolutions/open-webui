"""Regression tests for Chats.get_messages_map_by_chat_id().

The chat_message table deliberately never stores content/output/files/
sources/embeds (encryption-sensitive — see ChatMessage's column comment in
models/chat_messages.py). get_messages_map_by_chat_id() must always overlay
those fields from the legacy JSON blob onto the chat_message "fast path",
regardless of whether the parent-link graph has gaps. Without this overlay,
every message read via the fast path silently has empty-string content —
which breaks any caller that needs real content: the outlet-filter sync
that emits chat:outlet to the frontend (content flashes and then reverts to
empty), and load_messages_from_db (which sends blank history to the LLM).
"""
from unittest.mock import AsyncMock, patch

import pytest

from open_webui.models.chats import ChatModel, Chats


def _make_chat(chat_id, user_id, messages):
    return ChatModel(
        id=chat_id,
        user_id=user_id,
        title="Test Chat",
        chat={"history": {"messages": messages, "currentId": None}},
        created_at=0,
        updated_at=0,
    )


class TestGetMessagesMapByChatId:
    @pytest.mark.asyncio
    async def test_fast_path_overlays_content_from_legacy_blob(self):
        """chat_message fast path has no content — must be merged in from
        the JSON blob even when the parent-link graph is fully connected."""
        chat_id = "chat-1"
        user_id = "user-1"

        # chat_message "fast path": real graph shape, content defaulted to "".
        fast_path_map = {
            "user-msg": {
                "id": "user-msg",
                "role": "user",
                "parentId": None,
                "childrenIds": ["assistant-msg"],
                "content": "",
            },
            "assistant-msg": {
                "id": "assistant-msg",
                "role": "assistant",
                "parentId": "user-msg",
                "childrenIds": [],
                "content": "",
            },
        }

        legacy_messages = {
            "user-msg": {
                "id": "user-msg",
                "role": "user",
                "parentId": None,
                "childrenIds": ["assistant-msg"],
                "content": "What are 5 creative things I could do with my kids' art?",
            },
            "assistant-msg": {
                "id": "assistant-msg",
                "role": "assistant",
                "parentId": "user-msg",
                "childrenIds": [],
                "content": "Here are 5 ideas...",
                "done": True,
            },
        }

        chat = _make_chat(chat_id, user_id, legacy_messages)

        with patch(
            "open_webui.models.chats.ChatMessages.get_messages_map_by_chat_id",
            AsyncMock(return_value=fast_path_map),
        ), patch.object(Chats, "get_chat_by_id", AsyncMock(return_value=chat)):
            result = await Chats.get_messages_map_by_chat_id(chat_id)

        assert result["user-msg"]["content"] == legacy_messages["user-msg"]["content"]
        assert result["assistant-msg"]["content"] == legacy_messages["assistant-msg"]["content"]
        # Graph shape from the fast path is preserved.
        assert result["assistant-msg"]["parentId"] == "user-msg"

    @pytest.mark.asyncio
    async def test_fast_path_without_legacy_chat_leaves_content_untouched(self):
        """If the chat row itself can't be loaded, don't crash — just return
        the fast-path map as-is (best effort, matches prior behavior)."""
        chat_id = "chat-2"
        fast_path_map = {
            "only-msg": {"id": "only-msg", "role": "user", "parentId": None, "content": ""},
        }

        with patch(
            "open_webui.models.chats.ChatMessages.get_messages_map_by_chat_id",
            AsyncMock(return_value=fast_path_map),
        ), patch.object(Chats, "get_chat_by_id", AsyncMock(return_value=None)):
            result = await Chats.get_messages_map_by_chat_id(chat_id)

        assert result["only-msg"]["content"] == ""

    @pytest.mark.asyncio
    async def test_unresolved_parent_ids_still_merged_in_alongside_content_overlay(self):
        """Existing gap-healing behavior (missing messages backfilled from
        the legacy blob) must keep working alongside the content overlay."""
        chat_id = "chat-3"
        user_id = "user-1"

        # "child-msg" references a parent not present in the fast-path map.
        fast_path_map = {
            "child-msg": {
                "id": "child-msg",
                "role": "assistant",
                "parentId": "missing-parent",
                "childrenIds": [],
                "content": "",
            },
        }

        legacy_messages = {
            "missing-parent": {
                "id": "missing-parent",
                "role": "user",
                "parentId": None,
                "childrenIds": ["child-msg"],
                "content": "original prompt",
            },
            "child-msg": {
                "id": "child-msg",
                "role": "assistant",
                "parentId": "missing-parent",
                "childrenIds": [],
                "content": "the real response",
                "done": True,
            },
        }

        chat = _make_chat(chat_id, user_id, legacy_messages)

        with patch(
            "open_webui.models.chats.ChatMessages.get_messages_map_by_chat_id",
            AsyncMock(return_value=fast_path_map),
        ), patch.object(Chats, "get_chat_by_id", AsyncMock(return_value=chat)), patch.object(
            Chats, "backfill_messages_by_chat_id", AsyncMock()
        ):
            result = await Chats.get_messages_map_by_chat_id(chat_id)

        assert "missing-parent" in result
        assert result["missing-parent"]["content"] == "original prompt"
        assert result["child-msg"]["content"] == "the real response"
