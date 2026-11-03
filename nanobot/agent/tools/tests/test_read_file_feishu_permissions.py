# ruff: noqa: E402

"""Tests for read_file Feishu media file permissions (Batch-2D)."""
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

import pytest


@dataclass(frozen=True)
class MockRequestContext:
    """Mock RequestContext for testing."""
    channel: str | None
    sender_id: str | None
    session_key: str | None
    chat_id: str | None = None
    message_id: str | None = None
    log_content: bool = True


def _make_read_tool(workspace: Path):
    """Create ReadFileTool for testing."""
    from nanobot.agent.tools.filesystem import ReadFileTool
    from nanobot.agent.tools.file_state import FileStates
    
    return ReadFileTool(
        workspace=workspace,
        allowed_dir=workspace,
        extra_read_allowed_dirs=[],
        extra_read_allowed_files=[],
        file_states=FileStates(),
        restrict_to_workspace=False,
        sandbox_restricts_workspace=False,
    )


class TestFeishuMediaFilePermissions:
    """Test Feishu media file access permissions."""

    @pytest.mark.asyncio
    async def test_feishu_read_pending_file_success(self):
        """Feishu user can read their own pending_file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Create media directory and test file
            media_dir = tmpdir / "media" / "feishu"
            media_dir.mkdir(parents=True)
            test_file = media_dir / "test.txt"
            test_file.write_text("test content")
            
            # Mock config and session
            with patch('nanobot.config.paths.get_media_dir') as mock_media_dir, \
                 patch('nanobot.config.loader.load_config') as mock_config, \
                 patch('nanobot.agent.tools.context.current_request_context') as mock_ctx:
                
                mock_media_dir.return_value = media_dir
                mock_config.return_value = MagicMock(workspace_path=tmpdir)
                
                # Setup RequestContext
                mock_ctx.return_value = MockRequestContext(
                    channel="feishu",
                    sender_id="ou_user1",
                    session_key="feishu:ou_user1",
                )
                
                # Setup session with pending_file
                from nanobot.session.manager import SessionManager
                from nanobot.kb.store import KnowledgeBaseStore
                
                sessions = SessionManager(tmpdir)
                kb_store = KnowledgeBaseStore(sessions.get_sqlite_store())
                
                # Create file record
                file_id = kb_store.create_file_record(
                    filename="test.txt",
                    file_type="txt",
                    file_size=12,
                    file_path=str(test_file),
                    file_hash="testhash",
                    status="indexed",
                    sender_id="ou_user1",
                )
                
                # Save pending_file
                session_key = "feishu:ou_user1"
                
                def save_pending(meta):
                    meta["pending_file"] = {
                        "file_id": file_id,
                        "sender_id": "ou_user1",
                    }
                    return None
                
                await sessions.state.mutate_metadata(
                    session_key,
                    save_pending,
                    create_if_missing=True,
                )
                
                # Test read_file
                tool = _make_read_tool(tmpdir)
                result = await tool.execute(path=str(test_file))
                
                # Should succeed
                assert "test content" in str(result)

    @pytest.mark.asyncio
    async def test_feishu_read_other_user_file_denied(self):
        """Feishu user cannot read another user's file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            media_dir = tmpdir / "media" / "feishu"
            media_dir.mkdir(parents=True)
            test_file = media_dir / "test.txt"
            test_file.write_text("test content")
            
            with patch('nanobot.config.paths.get_media_dir') as mock_media_dir, \
                 patch('nanobot.config.loader.load_config') as mock_config, \
                 patch('nanobot.agent.tools.context.current_request_context') as mock_ctx:
                
                mock_media_dir.return_value = media_dir
                mock_config.return_value = MagicMock(workspace_path=tmpdir)
                
                # UserB trying to read
                mock_ctx.return_value = MockRequestContext(
                    channel="feishu",
                    sender_id="ou_user2",
                    session_key="feishu:ou_user2",
                )
                
                from nanobot.session.manager import SessionManager
                from nanobot.kb.store import KnowledgeBaseStore
                
                sessions = SessionManager(tmpdir)
                kb_store = KnowledgeBaseStore(sessions.get_sqlite_store())
                
                # Create file record for UserA
                file_id = kb_store.create_file_record(
                    filename="test.txt",
                    file_type="txt",
                    file_size=12,
                    file_path=str(test_file),
                    file_hash="testhash",
                    status="indexed",
                    sender_id="ou_user1",  # UserA's file
                )
                
                # UserB has different pending_file
                session_key = "feishu:ou_user2"
                
                def save_pending(meta):
                    meta["pending_file"] = {
                        "file_id": "different_file_id",
                        "sender_id": "ou_user2",
                    }
                    return None
                
                await sessions.state.mutate_metadata(
                    session_key,
                    save_pending,
                    create_if_missing=True,
                )
                
                # Test read_file
                tool = _make_read_tool(tmpdir)
                result = await tool.execute(path=str(test_file))
                
                # Should be denied
                assert "Permission denied" in str(result) or "not found" in str(result).lower()

    @pytest.mark.asyncio
    async def test_feishu_no_pending_file_denied(self):
        """Feishu user cannot read without pending_file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            media_dir = tmpdir / "media" / "feishu"
            media_dir.mkdir(parents=True)
            test_file = media_dir / "test.txt"
            test_file.write_text("test content")
            
            with patch('nanobot.config.paths.get_media_dir') as mock_media_dir, \
                 patch('nanobot.config.loader.load_config') as mock_config, \
                 patch('nanobot.agent.tools.context.current_request_context') as mock_ctx:
                
                mock_media_dir.return_value = media_dir
                mock_config.return_value = MagicMock(workspace_path=tmpdir)
                
                mock_ctx.return_value = MockRequestContext(
                    channel="feishu",
                    sender_id="ou_user1",
                    session_key="feishu:ou_user1",
                )
                
                # No pending_file setup
                
                tool = _make_read_tool(tmpdir)
                result = await tool.execute(path=str(test_file))
                
                # Should be denied
                assert "No file available" in str(result) or "Upload a file" in str(result)

    @pytest.mark.asyncio
    async def test_feishu_request_context_missing_denied(self):
        """Feishu media file denied when RequestContext missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            media_dir = tmpdir / "media" / "feishu"
            media_dir.mkdir(parents=True)
            test_file = media_dir / "test.txt"
            test_file.write_text("test content")
            
            with patch('nanobot.config.paths.get_media_dir') as mock_media_dir, \
                 patch('nanobot.agent.tools.context.current_request_context') as mock_ctx:
                
                mock_media_dir.return_value = media_dir
                mock_ctx.return_value = None  # No context
                
                tool = _make_read_tool(tmpdir)
                result = await tool.execute(path=str(test_file))
                
                # Should be denied
                assert "Permission denied" in str(result) or "verify" in str(result).lower()

    @pytest.mark.asyncio
    async def test_feishu_no_sender_id_denied(self):
        """Feishu request without sender_id denied."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            media_dir = tmpdir / "media" / "feishu"
            media_dir.mkdir(parents=True)
            test_file = media_dir / "test.txt"
            test_file.write_text("test content")
            
            with patch('nanobot.config.paths.get_media_dir') as mock_media_dir, \
                 patch('nanobot.agent.tools.context.current_request_context') as mock_ctx:
                
                mock_media_dir.return_value = media_dir
                
                # RequestContext exists but no sender_id
                mock_ctx.return_value = MockRequestContext(
                    channel="feishu",
                    sender_id=None,  # Missing
                    session_key="feishu:ou_user1",
                )
                
                tool = _make_read_tool(tmpdir)
                result = await tool.execute(path=str(test_file))
                
                # Should be denied
                assert "Permission denied" in str(result) or "sender identity" in str(result).lower()

    @pytest.mark.asyncio
    async def test_non_feishu_channel_allowed(self):
        """Non-Feishu channel can read media files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            media_dir = tmpdir / "media" / "feishu"
            media_dir.mkdir(parents=True)
            test_file = media_dir / "test.txt"
            test_file.write_text("test content")
            
            with patch('nanobot.config.paths.get_media_dir') as mock_media_dir, \
                 patch('nanobot.agent.tools.context.current_request_context') as mock_ctx:
                
                mock_media_dir.return_value = media_dir
                
                # Different channel
                mock_ctx.return_value = MockRequestContext(
                    channel="slack",  # Not feishu
                    sender_id="U12345",
                    session_key="slack:U12345",
                )
                
                tool = _make_read_tool(tmpdir)
                result = await tool.execute(path=str(test_file))
                
                # Should succeed (different channel has own rules)
                assert "test content" in str(result)

    @pytest.mark.asyncio
    async def test_read_non_media_file_unaffected(self):
        """Reading non-media files unaffected by Feishu permissions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            # Non-media file
            test_file = tmpdir / "workspace_file.txt"
            test_file.write_text("workspace content")
            
            media_dir = tmpdir / "media" / "feishu"
            media_dir.mkdir(parents=True)
            
            with patch('nanobot.config.paths.get_media_dir') as mock_media_dir, \
                 patch('nanobot.agent.tools.context.current_request_context') as mock_ctx:
                
                mock_media_dir.return_value = media_dir
                
                # Feishu context but reading non-media file
                mock_ctx.return_value = MockRequestContext(
                    channel="feishu",
                    sender_id="ou_user1",
                    session_key="feishu:ou_user1",
                )
                
                tool = _make_read_tool(tmpdir)
                result = await tool.execute(path=str(test_file))
                
                # Should succeed (not a media file)
                assert "workspace content" in str(result)

    @pytest.mark.asyncio
    async def test_path_mismatch_denied(self):
        """File path mismatch denied."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            
            media_dir = tmpdir / "media" / "feishu"
            media_dir.mkdir(parents=True)
            file_a = media_dir / "fileA.txt"
            file_b = media_dir / "fileB.txt"
            file_a.write_text("content A")
            file_b.write_text("content B")
            
            with patch('nanobot.config.paths.get_media_dir') as mock_media_dir, \
                 patch('nanobot.config.loader.load_config') as mock_config, \
                 patch('nanobot.agent.tools.context.current_request_context') as mock_ctx:
                
                mock_media_dir.return_value = media_dir
                mock_config.return_value = MagicMock(workspace_path=tmpdir)
                
                mock_ctx.return_value = MockRequestContext(
                    channel="feishu",
                    sender_id="ou_user1",
                    session_key="feishu:ou_user1",
                )
                
                from nanobot.session.manager import SessionManager
                from nanobot.kb.store import KnowledgeBaseStore
                
                sessions = SessionManager(tmpdir)
                kb_store = KnowledgeBaseStore(sessions.get_sqlite_store())
                
                # Create record for fileA
                file_id = kb_store.create_file_record(
                    filename="fileA.txt",
                    file_type="txt",
                    file_size=9,
                    file_path=str(file_a),  # Points to fileA
                    file_hash="hashA",
                    status="indexed",
                    sender_id="ou_user1",
                )
                
                # pending_file points to fileA
                session_key = "feishu:ou_user1"
                
                def save_pending(meta):
                    meta["pending_file"] = {
                        "file_id": file_id,
                        "sender_id": "ou_user1",
                    }
                    return None
                
                await sessions.state.mutate_metadata(
                    session_key,
                    save_pending,
                    create_if_missing=True,
                )
                
                # Try to read fileB (mismatch)
                tool = _make_read_tool(tmpdir)
                result = await tool.execute(path=str(file_b))
                
                # Should be denied
                assert "Permission denied" in str(result) or "path mismatch" in str(result).lower()

