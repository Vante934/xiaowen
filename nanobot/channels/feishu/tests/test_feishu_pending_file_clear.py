# ruff: noqa: E402

"""Tests for pending_file cleanup when new file arrives (Batch-2C)."""
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("lark_oapi")

from nanobot.bus.queue import MessageBus
from nanobot.channels.feishu.runtime import FeishuChannel, FeishuConfig


def _make_channel() -> FeishuChannel:
    config = FeishuConfig(
        enabled=True,
        app_id="cli_test",
        app_secret="secret",
        allow_from=["*"],
    )
    ch = FeishuChannel(config, MessageBus())
    ch._client = MagicMock()
    ch._running = True
    return ch


class TestPendingFileClear:
    """Test pending_file cleanup when new file arrives."""

    @pytest.mark.asyncio
    async def test_new_pdf_replaces_old_pdf(self):
        """New successful PDF should replace old pending_file."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('nanobot.config.loader.load_config') as mock_config:
                mock_config.return_value = MagicMock(workspace_path=Path(tmpdir))
                
                from nanobot.session.manager import SessionManager
                sessions = SessionManager(Path(tmpdir))
                
                # Mock KB store
                mock_kb_store = MagicMock()
                mock_kb_store.get_file.side_effect = lambda fid: {
                    "file-id-1": {"filename": "file1.pdf", "file_size": 100, "file_type": "application/pdf", "status": "indexed"},
                    "file-id-2": {"filename": "file2.pdf", "file_size": 200, "file_type": "application/pdf", "status": "indexed"},
                }.get(fid)
                mock_kb_store.get_parsed_text.return_value = "test content"
                ch._kb_store = mock_kb_store
                
                # Save first file
                saved1 = await ch._save_pending_file_to_session(
                    "ou_user1", "file-id-1", "p2p", "msg_1"
                )
                assert saved1 is True
                
                # Check first file is saved
                session_key = f"{ch.name}:ou_user1"
                meta1 = await sessions.state.read_metadata(session_key)
                assert meta1 is not None
                assert "pending_file" in meta1["metadata"]
                assert meta1["metadata"]["pending_file"]["file_id"] == "file-id-1"
                
                # Clear for new file
                await ch._clear_pending_file_for_new_upload("ou_user1", "msg_2")
                
                # Check old file is cleared
                meta_cleared = await sessions.state.read_metadata(session_key)
                assert meta_cleared is not None
                assert "pending_file" not in meta_cleared["metadata"]
                assert meta_cleared["metadata"]["current_file_message_id"] == "msg_2"
                
                # Save second file
                saved2 = await ch._save_pending_file_to_session(
                    "ou_user1", "file-id-2", "p2p", "msg_2"
                )
                assert saved2 is True
                
                # Check second file is saved
                meta2 = await sessions.state.read_metadata(session_key)
                assert meta2 is not None
                assert "pending_file" in meta2["metadata"]
                assert meta2["metadata"]["pending_file"]["file_id"] == "file-id-2"
                assert meta2["metadata"]["pending_file"]["filename"] == "file2.pdf"

    @pytest.mark.asyncio
    async def test_pptx_clears_old_pdf(self):
        """Unsupported PPTX should clear old pending_file."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('nanobot.config.loader.load_config') as mock_config:
                mock_config.return_value = MagicMock(workspace_path=Path(tmpdir))
                
                from nanobot.session.manager import SessionManager
                sessions = SessionManager(Path(tmpdir))
                
                # Mock KB store
                mock_kb_store = MagicMock()
                mock_kb_store.get_file.return_value = {
                    "filename": "file1.pdf",
                    "file_size": 100,
                    "file_type": "application/pdf",
                    "status": "indexed"
                }
                mock_kb_store.get_parsed_text.return_value = "test content"
                ch._kb_store = mock_kb_store
                
                # Save first PDF
                saved1 = await ch._save_pending_file_to_session(
                    "ou_user1", "file-id-1", "p2p", "msg_1"
                )
                assert saved1 is True
                
                # Clear for PPTX
                await ch._clear_pending_file_for_new_upload("ou_user1", "msg_2")
                
                # Check old file is cleared
                session_key = f"{ch.name}:ou_user1"
                meta = await sessions.state.read_metadata(session_key)
                assert meta is not None
                assert "pending_file" not in meta["metadata"]
                assert meta["metadata"]["current_file_message_id"] == "msg_2"

    @pytest.mark.asyncio
    async def test_text_message_does_not_clear(self):
        """Normal text messages should not clear pending_file."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('nanobot.config.loader.load_config') as mock_config:
                mock_config.return_value = MagicMock(workspace_path=Path(tmpdir))
                
                from nanobot.session.manager import SessionManager
                sessions = SessionManager(Path(tmpdir))
                
                # Mock KB store
                mock_kb_store = MagicMock()
                mock_kb_store.get_file.return_value = {
                    "filename": "file1.pdf",
                    "file_size": 100,
                    "file_type": "application/pdf",
                    "status": "indexed"
                }
                mock_kb_store.get_parsed_text.return_value = "test content"
                ch._kb_store = mock_kb_store
                
                # Save PDF
                saved = await ch._save_pending_file_to_session(
                    "ou_user1", "file-id-1", "p2p", "msg_1"
                )
                assert saved is True
                
                # Text message does NOT call _clear_pending_file_for_new_upload
                # So pending_file should remain
                
                session_key = f"{ch.name}:ou_user1"
                meta = await sessions.state.read_metadata(session_key)
                assert meta is not None
                assert "pending_file" in meta["metadata"]
                assert meta["metadata"]["pending_file"]["file_id"] == "file-id-1"

    @pytest.mark.asyncio
    async def test_concurrent_uploads_b_wins(self):
        """If A and B upload concurrently, B completes first, B should be final pending_file."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('nanobot.config.loader.load_config') as mock_config:
                mock_config.return_value = MagicMock(workspace_path=Path(tmpdir))
                
                from nanobot.session.manager import SessionManager
                sessions = SessionManager(Path(tmpdir))
                
                # Mock KB store
                mock_kb_store = MagicMock()
                mock_kb_store.get_file.side_effect = lambda fid: {
                    "file-id-a": {"filename": "fileA.pdf", "file_size": 100, "file_type": "application/pdf", "status": "indexed"},
                    "file-id-b": {"filename": "fileB.pdf", "file_size": 200, "file_type": "application/pdf", "status": "indexed"},
                }.get(fid)
                mock_kb_store.get_parsed_text.return_value = "test content"
                ch._kb_store = mock_kb_store
                
                # Simulate: A clears, B clears (B's clear is later), B saves, A tries to save
                await ch._clear_pending_file_for_new_upload("ou_user1", "msg_a")
                await ch._clear_pending_file_for_new_upload("ou_user1", "msg_b")  # B's clear wins
                
                # B saves first
                saved_b = await ch._save_pending_file_to_session(
                    "ou_user1", "file-id-b", "p2p", "msg_b"
                )
                assert saved_b is True
                
                # A tries to save (should be rejected)
                saved_a = await ch._save_pending_file_to_session(
                    "ou_user1", "file-id-a", "p2p", "msg_a"
                )
                assert saved_a is False  # Rejected because current_file_message_id=msg_b
                
                # Final state: B wins
                session_key = f"{ch.name}:ou_user1"
                meta = await sessions.state.read_metadata(session_key)
                assert meta is not None
                assert "pending_file" in meta["metadata"]
                assert meta["metadata"]["pending_file"]["file_id"] == "file-id-b"
                assert meta["metadata"]["pending_file"]["filename"] == "fileB.pdf"

    @pytest.mark.asyncio
    async def test_different_users_isolated(self):
        """Different users should have isolated pending_file."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('nanobot.config.loader.load_config') as mock_config:
                mock_config.return_value = MagicMock(workspace_path=Path(tmpdir))
                
                from nanobot.session.manager import SessionManager
                sessions = SessionManager(Path(tmpdir))
                
                # Mock KB store
                mock_kb_store = MagicMock()
                mock_kb_store.get_file.side_effect = lambda fid: {
                    "file-id-1": {"filename": "file1.pdf", "file_size": 100, "file_type": "application/pdf", "status": "indexed"},
                    "file-id-2": {"filename": "file2.pdf", "file_size": 200, "file_type": "application/pdf", "status": "indexed"},
                }.get(fid)
                mock_kb_store.get_parsed_text.return_value = "test content"
                ch._kb_store = mock_kb_store
                
                # User1 uploads file1
                saved1 = await ch._save_pending_file_to_session(
                    "ou_user1", "file-id-1", "p2p", "msg_1"
                )
                assert saved1 is True
                
                # User2 uploads file2
                saved2 = await ch._save_pending_file_to_session(
                    "ou_user2", "file-id-2", "p2p", "msg_2"
                )
                assert saved2 is True
                
                # Check both users have different pending_file
                meta1 = await sessions.state.read_metadata(f"{ch.name}:ou_user1")
                meta2 = await sessions.state.read_metadata(f"{ch.name}:ou_user2")
                
                assert meta1 is not None
                assert meta2 is not None
                assert meta1["metadata"]["pending_file"]["file_id"] == "file-id-1"
                assert meta2["metadata"]["pending_file"]["file_id"] == "file-id-2"
                
                # User1 clears
                await ch._clear_pending_file_for_new_upload("ou_user1", "msg_3")
                
                # User1's pending_file cleared, User2's unchanged
                meta1_after = await sessions.state.read_metadata(f"{ch.name}:ou_user1")
                meta2_after = await sessions.state.read_metadata(f"{ch.name}:ou_user2")
                
                assert meta1_after is not None
                assert meta2_after is not None
                assert "pending_file" not in meta1_after["metadata"]
                assert meta2_after["metadata"]["pending_file"]["file_id"] == "file-id-2"

    @pytest.mark.asyncio
    async def test_none_message_id_rejected(self):
        """message_id=None should be rejected for concurrency safety."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('nanobot.config.loader.load_config') as mock_config:
                mock_config.return_value = MagicMock(workspace_path=Path(tmpdir))
                
                # Mock KB store
                mock_kb_store = MagicMock()
                mock_kb_store.get_file.return_value = {
                    "filename": "test.pdf",
                    "file_size": 100,
                    "file_type": "application/pdf",
                    "status": "indexed"
                }
                mock_kb_store.get_parsed_text.return_value = "test"
                ch._kb_store = mock_kb_store
                
                # Try to save with message_id=None
                result = await ch._save_pending_file_to_session(
                    sender_id="ou_user1",
                    file_id="file-id-1",
                    chat_type="p2p",
                    message_id=None,  # None should be rejected
                )
                
                # Should fail
                assert result is False

    @pytest.mark.asyncio
    async def test_empty_message_id_rejected(self):
        """message_id="" should be rejected for concurrency safety."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('nanobot.config.loader.load_config') as mock_config:
                mock_config.return_value = MagicMock(workspace_path=Path(tmpdir))
                
                # Mock KB store
                mock_kb_store = MagicMock()
                mock_kb_store.get_file.return_value = {
                    "filename": "test.pdf",
                    "file_size": 100,
                    "file_type": "application/pdf",
                    "status": "indexed"
                }
                mock_kb_store.get_parsed_text.return_value = "test"
                ch._kb_store = mock_kb_store
                
                # Try to save with message_id=""
                result = await ch._save_pending_file_to_session(
                    sender_id="ou_user1",
                    file_id="file-id-1",
                    chat_type="p2p",
                    message_id="",  # Empty should be rejected
                )
                
                # Should fail
                assert result is False

    @pytest.mark.asyncio
    async def test_old_task_none_cannot_overwrite_new(self):
        """Old task with message_id=None cannot overwrite new task with valid ID."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('nanobot.config.loader.load_config') as mock_config:
                mock_config.return_value = MagicMock(workspace_path=Path(tmpdir))
                
                from nanobot.session.manager import SessionManager
                sessions = SessionManager(Path(tmpdir))
                
                # Mock KB store
                mock_kb_store = MagicMock()
                mock_kb_store.get_file.side_effect = lambda fid: {
                    "file-id-old": {"filename": "old.pdf", "file_size": 100, "file_type": "application/pdf", "status": "indexed"},
                    "file-id-new": {"filename": "new.pdf", "file_size": 200, "file_type": "application/pdf", "status": "indexed"},
                }.get(fid)
                mock_kb_store.get_parsed_text.return_value = "test"
                ch._kb_store = mock_kb_store
                
                # Simulate: new request arrives and clears old state
                await ch._clear_pending_file_for_new_upload("ou_user1", "msg_new")
                
                # Save new file with valid message_id
                result_new = await ch._save_pending_file_to_session(
                    sender_id="ou_user1",
                    file_id="file-id-new",
                    chat_type="p2p",
                    message_id="msg_new",
                )
                assert result_new is True
                
                # Old task (with message_id=None) tries to save later
                result_old = await ch._save_pending_file_to_session(
                    sender_id="ou_user1",
                    file_id="file-id-old",
                    chat_type="p2p",
                    message_id=None,  # Old task has no message_id
                )
                
                # Old task should be rejected
                assert result_old is False
                
                # Verify new file still saved
                session_key = f"{ch.name}:ou_user1"
                meta = await sessions.state.read_metadata(session_key)
                assert meta is not None
                assert meta["metadata"]["pending_file"]["file_id"] == "file-id-new"
