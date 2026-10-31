# ruff: noqa: E402

"""Tests for unsupported file format handling in Feishu channel."""
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestUnsupportedFileFormat:
    """Test handling of unsupported file formats."""

    @pytest.mark.asyncio
    async def test_supported_format_returns_success(self):
        """Supported formats should proceed normally."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test PDF file
            test_file = Path(tmpdir) / "test.pdf"
            test_file.write_bytes(b"%PDF-1.4\ntest content")
            
            # Mock KB store and tool
            from nanobot.kb.store import KnowledgeBaseStore
            from nanobot.session.sqlite_store import SqliteSessionStore
            
            store = SqliteSessionStore(Path(tmpdir))
            store.initialize(Path(tmpdir))
            ch._kb_store = KnowledgeBaseStore(store)
            
            # Mock KB upload tool to return success
            with patch('nanobot.agent.tools.kb_upload.KBUploadTool') as mock_tool_class:
                mock_tool = MagicMock()
                mock_tool.execute = AsyncMock(return_value="✅ File uploaded (ID: test-id-123)")
                mock_tool_class.return_value = mock_tool
                
                result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")
            
            # Should proceed to upload
            assert result is not None
            assert "uploaded" in result.lower() or "successfully" in result.lower()

    @pytest.mark.asyncio
    async def test_pptx_rejected_with_message(self):
        """PPTX files should be rejected with user-friendly message."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "presentation.pptx"
            test_file.write_bytes(b"PK\x03\x04")  # ZIP signature
            
            result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")
            
            # Should return error message
            assert result is not None
            assert "⚠️" in result
            assert "presentation.pptx" in result
            assert "PPTX" in result or "PowerPoint" in result
            assert "PDF" in result
            assert "DOCX" in result

    @pytest.mark.asyncio
    async def test_doc_rejected_with_message(self):
        """DOC files should be rejected with user-friendly message."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "old_document.doc"
            test_file.write_bytes(b"\xD0\xCF\x11\xE0")  # OLE signature
            
            result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")
            
            # Should return error message
            assert result is not None
            assert "⚠️" in result
            assert "old_document.doc" in result
            assert "DOC" in result

    @pytest.mark.asyncio
    async def test_image_rejected_with_message(self):
        """Image files should be rejected with user-friendly message."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "photo.jpg"
            test_file.write_bytes(b"\xFF\xD8\xFF")  # JPEG signature
            
            result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")
            
            # Should return error message
            assert result is not None
            assert "⚠️" in result
            assert "photo.jpg" in result
            assert "图片" in result

    @pytest.mark.asyncio
    async def test_case_insensitive_extension(self):
        """File extensions should be case-insensitive."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Test uppercase PPTX
            test_file = Path(tmpdir) / "TEST.PPTX"
            test_file.write_bytes(b"content")
            
            result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")
            
            # Should be rejected
            assert result is not None
            assert "⚠️" in result

    @pytest.mark.asyncio
    async def test_no_extension_rejected(self):
        """Files without extension should be rejected."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "noextension"
            test_file.write_bytes(b"content")
            
            result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")
            
            # Should be rejected
            assert result is not None
            assert "⚠️" in result

    @pytest.mark.asyncio
    async def test_unsupported_format_not_uploaded_to_kb(self):
        """Unsupported formats should not trigger KB upload tool."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.pptx"
            test_file.write_bytes(b"content")
            
            # Mock KB tool - should not be called
            with patch('nanobot.agent.tools.kb_upload.KBUploadTool') as mock_tool_class:
                mock_tool = MagicMock()
                mock_tool.execute = AsyncMock()
                mock_tool_class.return_value = mock_tool
                
                result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")
                
                # KB tool should NOT be called
                mock_tool.execute.assert_not_called()
                
                # Should return error message
                assert result is not None
                assert "⚠️" in result

    @pytest.mark.asyncio
    async def test_supported_formats_complete(self):
        """Verify all supported formats are accepted."""
        ch = _make_channel()
        
        supported_formats = [".pdf", ".docx", ".txt", ".md", ".xlsx"]
        
        with tempfile.TemporaryDirectory() as tmpdir:
            from nanobot.kb.store import KnowledgeBaseStore
            from nanobot.session.sqlite_store import SqliteSessionStore
            
            store = SqliteSessionStore(Path(tmpdir))
            store.initialize(Path(tmpdir))
            ch._kb_store = KnowledgeBaseStore(store)
            
            with patch('nanobot.agent.tools.kb_upload.KBUploadTool') as mock_tool_class:
                mock_tool = MagicMock()
                mock_tool.execute = AsyncMock(return_value="✅ Uploaded")
                mock_tool_class.return_value = mock_tool
                
                for ext in supported_formats:
                    test_file = Path(tmpdir) / f"test{ext}"
                    test_file.write_bytes(b"content")
                    
                    result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")
                    
                    # Should proceed (not return error message)
                    assert result is not None
                    assert "⚠️" not in result

    @pytest.mark.asyncio
    async def test_error_message_format(self):
        """Error message should follow consistent format."""
        ch = _make_channel()
        
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.pptx"
            test_file.write_bytes(b"content")
            
            result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")
            
            # Check message structure
            assert result is not None
            assert result.startswith("⚠️")
            assert "暂时无法处理" in result or "无法处理" in result
            assert "test.pptx" in result
            assert "PDF" in result and "DOCX" in result  # List supported formats
            assert "PPTX" in result or "DOC" in result  # Mention unsupported
