# F06-B Batch-2C：新文件到达时清理旧pending_file - 进展报告

**实施日期**: 2026-10-30  
**批次**: Batch-2C（pending_file清理）  
**状态**: ⚠️ 实现完成，测试调试中  

---

## 一、修改文件清单

1. **nanobot/channels/feishu/runtime.py** (修改中)
   - 新增`_clear_pending_file_for_new_upload()`方法
   - 修改`_save_pending_file_to_session()`支持并发保护
   - 修改文件消息处理逻辑

2. **nanobot/channels/feishu/tests/test_feishu_pending_file_clear.py** (新增, 调试中)
   - 5个测试用例

---

## 二、实施方案总结

### 2.1 并发保护设计

**核心思路**: 使用message_id作为并发保护标识

**流程**:
```
新文件消息到达
  ↓
_clear_pending_file_for_new_upload(sender_id, message_id)
  ↓ 原子操作
- 删除旧pending_file
- 记录current_file_message_id = message_id
  ↓
处理新文件
  ↓
_save_pending_file_to_session(sender_id, file_id, message_id)
  ↓ 验证
if current_file_message_id == message_id:
    保存新pending_file ✅
else:
    拒绝保存 ❌ (更新的请求已到达)
```

### 2.2 代码修改详情

#### 修改1: 文件消息入口清理旧pending_file

**位置**: `runtime.py:3203`

```python
# Auto-upload document files to knowledge base
if msg_type == "file":
    # B2-C: Clear old pending_file when new file arrives (p2p only)
    if chat_type == "p2p":
        await self._clear_pending_file_for_new_upload(
            sender_id=sender_id,
            new_message_id=message_id,
        )
    
    kb_result = await self._auto_upload_to_kb(file_path, sender_id)
```

**改进**:
- ✅ 识别到新文件后立即清理
- ✅ 只在p2p执行
- ✅ 在KB处理前执行

#### 修改2: 新增清理方法

**位置**: `runtime.py:2039` (新增方法)

```python
async def _clear_pending_file_for_new_upload(
    self,
    sender_id: str,
    new_message_id: str,
) -> bool:
    """Clear old pending_file when new file arrives."""
    try:
        session_key = f"{self.name}:{sender_id}"
        
        from nanobot.config.loader import load_config
        from nanobot.session.manager import SessionManager
        
        config = load_config()
        sessions = SessionManager(config.workspace_path)
        
        # Atomic update
        def clear_meta(meta: dict[str, Any]) -> None:
            # Clear old pending_file
            if "pending_file" in meta:
                old_file = meta.get("pending_file", {})
                self.logger.info(
                    "Clearing old pending_file: {} (new file arriving)",
                    old_file.get("filename", "unknown"),
                )
                del meta["pending_file"]
            
            # Mark current file request for concurrency protection
            meta["current_file_message_id"] = new_message_id
            return None
        
        await sessions.state.mutate_metadata(
            session_key,
            clear_meta,
            create_if_missing=True,
        )
        
        return True
        
    except Exception as e:
        self.logger.exception("Failed to clear pending_file (non-blocking): {}", e)
        return False
```

**功能**:
- ✅ 删除旧pending_file
- ✅ 记录当前文件message_id
- ✅ 原子操作
- ✅ 异常不中断

#### 修改3: 保存pending_file时验证message_id

**位置**: `runtime.py:1928-2037`

```python
async def _save_pending_file_to_session(
    self,
    sender_id: str,
    file_id: str,
    chat_type: str,
    message_id: str | None = None,  # B2-C: For concurrency protection
) -> bool:
    # ... 原有逻辑 ...
    
    # Build pending_file structure
    pending_file = {
        "file_id": file_id,
        "filename": file_record["filename"],
        "file_size": file_record["file_size"],
        "file_type": file_record["file_type"],
        "upload_time": datetime.now(UTC).isoformat(),
        "sender_id": sender_id,
        "summary": summary,
        "status": "pending",
        "request_id": str(uuid.uuid4()),
        "message_id": message_id,  # B2-C: Store message_id
    }
    
    # Atomic update with concurrency protection
    save_succeeded = False
    
    def save_to_meta(meta: dict[str, Any]) -> None:
        nonlocal save_succeeded
        
        # B2-C: Check if a newer file request has arrived
        current_file_message_id = meta.get("current_file_message_id")
        if message_id and current_file_message_id and current_file_message_id != message_id:
            # A newer file request has arrived, don't overwrite
            self.logger.info(
                "Skip saving pending_file: newer file request detected (current={}, this={})",
                current_file_message_id,
                message_id,
            )
            save_succeeded = False
            return None
        
        # Save pending_file
        meta["pending_file"] = pending_file
        save_succeeded = True
        return None
    
    await sessions.state.mutate_metadata(
        session_key,
        save_to_meta,
        create_if_missing=True,
    )
    
    return save_succeeded  # Return actual save result
```

**改进**:
- ✅ 增加message_id参数
- ✅ 验证current_file_message_id
- ✅ 旧任务自动放弃保存
- ✅ 返回实际保存结果

#### 修改4: 传递message_id到保存方法

**位置**: `runtime.py:3214`

```python
saved = await self._save_pending_file_to_session(
    sender_id=sender_id,
    file_id=file_id,
    chat_type=chat_type,
    message_id=message_id,  # B2-C: Pass message_id
)
```

---

## 三、测试状态

### 3.1 设计的测试

1. ✅ test_new_pdf_replaces_old_pdf
2. ✅ test_pptx_clears_old_pdf
3. ✅ test_text_message_does_not_clear
4. ✅ test_concurrent_uploads_b_wins
5. ✅ test_different_users_isolated

### 3.2 当前问题

**测试失败原因**: SessionManager实例不一致

**问题分析**:
- 测试中创建SessionManager(Path(tmpdir))
- `_save_pending_file_to_session()`内部创建SessionManager(workspace_path)
- 两个实例操作不同的SQLite文件

**解决方向**:
1. 修改实现：让Channel持有SessionManager实例
2. 修改测试：Mock SessionManager或使用正确的workspace
3. 使用peek_metadata等同步方法

---

## 四、已知未解决问题

### 问题1: 测试需要重新设计

**状态**: ⚠️ 测试失败

**原因**: SessionManager实例不一致

**建议**: 需要重新设计测试策略或修改Channel实现以接受SessionManager注入

### 问题2: 并发保护机制未端到端验证

**状态**: ⚠️ 未验证

**需要**: 实际并发场景测试

---

## 五、Batch-2A和Batch-2B的保留状态

**检查**: 使用git diff确认

```
git status --short
```

**预期输出**:
```
M  nanobot/channels/feishu/runtime.py  (包含2A+2B+2C的修改)
M  nanobot/channels/feishu/tests/test_feishu_welcome.py  (2A)
?? nanobot/channels/feishu/tests/test_feishu_unsupported_format.py  (2B)
?? nanobot/channels/feishu/tests/test_feishu_pending_file_clear.py  (2C, 调试中)
```

---

## 六、建议后续步骤

1. **修复测试**: 重新设计测试以匹配实现
2. **回归测试**: 确保2A和2B不受影响
3. **端到端测试**: 验证实际并发场景
4. **代码审查**: 确认并发保护机制

---

**状态**: ⚠️ **实现完成，测试调试中，暂停等待指导**
