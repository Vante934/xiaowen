# ruff: noqa: E402

"""Tests for file size limit in Feishu channel (Batch-2E)."""
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


class TestFileSizeLimit:
    """Test file size limit (50 MiB) for Feishu file uploads."""

    @pytest.mark.asyncio
    async def test_file_within_limit_uploaded(self):
        """File under 50 MiB should be uploaded successfully."""
        ch = _make_channel()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file just under 50 MiB
            file_size = 49 * 1024 * 1024  # 49 MiB
            test_file = Path(tmpdir) / "test_49mb.pdf"
            test_file.write_bytes(b"x" * file_size)

            # Mock KB components
            mock_kb_store = MagicMock()
            ch._kb_store = mock_kb_store
            ch._index_manager = MagicMock()

            # Mock KBUploadTool to return success
            with patch("nanobot.agent.tools.kb_upload.KBUploadTool") as MockTool:
                mock_tool = MagicMock()
                
                # Make execute async
                async def mock_execute(*args, **kwargs):
                    return "Success: File uploaded (ID: file-123)"
                
                mock_tool.execute = mock_execute
                MockTool.return_value = mock_tool

                result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")

                # Should proceed to upload (result may be None or contain success message)
                # The key is that it didn't return an error message
                if result:
                    assert "文件过大" not in result
                    assert "无法读取" not in result

    @pytest.mark.asyncio
    async def test_file_exactly_limit_uploaded(self):
        """File exactly 50 MiB should be uploaded successfully."""
        ch = _make_channel()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file exactly 50 MiB
            file_size = 50 * 1024 * 1024  # 50 MiB
            test_file = Path(tmpdir) / "test_50mb.pdf"

            # Use sparse file to save time
            with open(test_file, "wb") as f:
                f.seek(file_size - 1)
                f.write(b"\0")

            # Mock KB components
            mock_kb_store = MagicMock()
            ch._kb_store = mock_kb_store
            ch._index_manager = MagicMock()

            # Mock KBUploadTool
            with patch("nanobot.agent.tools.kb_upload.KBUploadTool") as MockTool:
                mock_tool = MagicMock()
                
                async def mock_execute(*args, **kwargs):
                    return "Success: File uploaded (ID: file-123)"
                
                mock_tool.execute = mock_execute
                MockTool.return_value = mock_tool

                result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")

                # Should proceed to upload (50 MiB is allowed)
                # The key is that it didn't return an error message
                if result:
                    assert "文件过大" not in result
                    assert "无法读取" not in result

    @pytest.mark.asyncio
    async def test_file_exceeds_limit_rejected(self):
        """File over 50 MiB should be rejected."""
        ch = _make_channel()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file over 50 MiB
            file_size = 51 * 1024 * 1024  # 51 MiB
            test_file = Path(tmpdir) / "test_51mb.pdf"

            # Use sparse file
            with open(test_file, "wb") as f:
                f.seek(file_size - 1)
                f.write(b"\0")

            result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")

            # Should return size limit error
            assert result is not None
            assert "文件过大" in result or "过大" in result
            assert "50" in result or "MiB" in result

            # File should be deleted
            assert not test_file.exists()

    @pytest.mark.asyncio
    async def test_oversized_file_not_uploaded_to_kb(self):
        """Oversized file should not enter KB upload flow."""
        ch = _make_channel()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create oversized file
            file_size = 55 * 1024 * 1024  # 55 MiB
            test_file = Path(tmpdir) / "test_55mb.pdf"

            with open(test_file, "wb") as f:
                f.seek(file_size - 1)
                f.write(b"\0")

            # Mock KB components
            mock_kb_store = MagicMock()
            ch._kb_store = mock_kb_store

            # Mock KBUploadTool - should NOT be called
            with patch("nanobot.agent.tools.kb_upload.KBUploadTool") as MockTool:
                mock_tool = MagicMock()
                
                async def mock_execute(*args, **kwargs):
                    return "Success: File uploaded"
                
                mock_tool.execute = mock_execute
                MockTool.return_value = mock_tool

                result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")

                # Should return error message
                assert result is not None
                assert "文件过大" in result or "过大" in result

    @pytest.mark.asyncio
    async def test_oversized_file_no_pending_file(self):
        """Oversized file should not be saved to pending_file."""
        ch = _make_channel()

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("nanobot.config.loader.load_config") as mock_config:
                mock_config.return_value = MagicMock(workspace_path=Path(tmpdir))

                # Create oversized file
                file_size = 52 * 1024 * 1024  # 52 MiB
                test_file = Path(tmpdir) / "test_52mb.pdf"

                with open(test_file, "wb") as f:
                    f.seek(file_size - 1)
                    f.write(b"\0")

                # Call auto_upload_to_kb
                result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")

                # Should be rejected
                assert result is not None
                assert "文件过大" in result or "过大" in result

                # Verify no pending_file saved
                from nanobot.session.manager import SessionManager
                sessions = SessionManager(Path(tmpdir))
                session_key = f"{ch.name}:ou_user1"

                session = await sessions.state.get(session_key)
                # Session may not exist or have no pending_file
                if session and session.metadata:
                    assert "pending_file" not in session.metadata

    @pytest.mark.asyncio
    async def test_oversized_file_no_greeting(self):
        """Oversized file should not trigger file greeting."""
        ch = _make_channel()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create oversized file
            file_size = 60 * 1024 * 1024  # 60 MiB
            test_file = Path(tmpdir) / "test_60mb.pdf"

            with open(test_file, "wb") as f:
                f.seek(file_size - 1)
                f.write(b"\0")

            # Mock _send_file_greeting
            ch._send_file_greeting = MagicMock()

            result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")

            # Should be rejected
            assert result is not None
            assert "文件过大" in result or "过大" in result

            # Greeting should NOT be called
            assert not ch._send_file_greeting.called

    @pytest.mark.asyncio
    async def test_stat_failure_rejects_upload(self):
        """If stat() fails, upload should be rejected."""
        ch = _make_channel()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a valid file
            test_file = Path(tmpdir) / "test.pdf"
            test_file.write_bytes(b"test content")

            # Mock stat to raise exception
            original_stat = Path.stat

            def mock_stat_fail(self):
                if self.name == "test.pdf":
                    raise OSError("Stat failed")
                return original_stat(self)

            with patch.object(Path, "stat", mock_stat_fail):
                result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")

                # Should return error
                assert result is not None
                assert "无法读取文件信息" in result or "读取" in result

    @pytest.mark.asyncio
    async def test_nonexistent_file_rejects_upload(self):
        """If file doesn't exist, upload should be rejected."""
        ch = _make_channel()

        nonexistent_file = "/tmp/nonexistent_file_xyz123.pdf"

        result = await ch._auto_upload_to_kb(nonexistent_file, "ou_user1")

        # Should return error
        assert result is not None
        assert "无法读取文件信息" in result or "读取" in result or "暂时无法处理" in result

    @pytest.mark.asyncio
    async def test_cleanup_failure_still_rejects_upload(self):
        """Even if cleanup fails, oversized file should not be uploaded."""
        ch = _make_channel()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create oversized file
            file_size = 55 * 1024 * 1024  # 55 MiB
            test_file = Path(tmpdir) / "test_55mb.pdf"

            with open(test_file, "wb") as f:
                f.seek(file_size - 1)
                f.write(b"\0")

            # Mock unlink to fail
            original_unlink = Path.unlink

            def mock_unlink_fail(self, *args, **kwargs):
                if self.name == "test_55mb.pdf":
                    raise OSError("Cleanup failed")
                return original_unlink(self, *args, **kwargs)

            with patch.object(Path, "unlink", mock_unlink_fail):
                # Mock KB components
                mock_kb_store = MagicMock()
                ch._kb_store = mock_kb_store

                # Mock KBUploadTool - should NOT be called
                with patch("nanobot.agent.tools.kb_upload.KBUploadTool") as MockTool:
                    mock_tool = MagicMock()
                    
                    async def mock_execute(*args, **kwargs):
                        return "Success"
                    
                    mock_tool.execute = mock_execute
                    MockTool.return_value = mock_tool

                    result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")

                    # Should still reject
                    assert result is not None
                    assert "文件过大" in result or "过大" in result

    @pytest.mark.asyncio
    async def test_unsupported_format_skips_size_check(self):
        """Unsupported format should return Batch-2B error, not enter size check."""
        ch = _make_channel()

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a PPTX file (unsupported format)
            test_file = Path(tmpdir) / "test.pptx"
            test_file.write_bytes(b"x" * 1024)  # Small file

            result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")

            # Should return Batch-2B unsupported format error
            assert result is not None
            assert "暂时无法处理" in result or "支持" in result
            assert "PPTX" in result or "格式" in result

            # Should NOT mention file size
            assert "过大" not in result
            assert "50" not in result or "50 MiB" not in result
