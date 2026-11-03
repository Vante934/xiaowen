# F06-B Batch-2C：新文件到达时清理旧pending_file - 完成报告

**实施日期**: 2026-10-31  
**批次**: Batch-2C（pending_file清理与并发保护）  
**状态**: ✅ 完成  

---

## 一、测试失败的真实原因

### 原因分析

**问题**: 测试中的SessionManager与生产代码中的SessionManager使用不同的SQLite数据库

**根本原因**:
1. 生产代码每次调用时创建新SessionManager: `SessionManager(config.workspace_path)`
2. 测试创建独立SessionManager: `SessionManager(Path(tmpdir))`
3. 两个实例操作不同的数据库文件，导致测试读取不到生产代码写入的数据

**错误表现**:
```python
# 生产代码写入
await sessions.state.mutate_metadata(...)  # workspace_path数据库

# 测试读取
meta = await sessions.state.read_metadata(...)  # tmpdir数据库
# 结果: None (不同数据库)
```

---

## 二、测试环境修复方式

### 修复方案: Mock load_config返回测试workspace

**实施**:
```python
with tempfile.TemporaryDirectory() as tmpdir:
    with patch('nanobot.config.loader.load_config') as mock_config:
        mock_config.return_value = MagicMock(workspace_path=Path(tmpdir))
        
        # 现在所有SessionManager都使用相同的tmpdir
        sessions = SessionManager(Path(tmpdir))
        
        # 生产代码创建的SessionManager也会使用tmpdir
        await ch._save_pending_file_to_session(...)  # 写入tmpdir
        
        # 测试可以读取
        meta = await sessions.state.read_metadata(...)  # 读取tmpdir
```

**关键改进**:
- ✅ 使用真实SQLite数据库（不是Mock）
- ✅ 测试和生产代码共享同一个数据库实例
- ✅ 临时目录隔离，不影响正式数据
- ✅ 验证原子操作的真实行为

### 额外发现: read_metadata返回格式

**问题**: `read_metadata()`返回完整记录，不只是metadata字段

**返回格式**:
```python
{
    "key": "feishu:ou_user1",
    "created_at": "2026-10-31T16:04:54.874028",
    "updated_at": "2026-10-31T16:04:54.874030",
    "metadata": {
        "pending_file": {...},
        "current_file_message_id": "msg_2"
    }
}
```

**修复**: 访问`meta["metadata"]`而不是直接访问`meta`

---

## 三、清理时机的修改

### 修改前（问题）

**位置**: `runtime.py:3290` (旧代码)

```python
elif msg_type in ("image", "audio", "file", "media"):
    file_path, content_text = await self._download_and_save_media(
        msg_type, content_json, message_id
    )  # ← 下载完成
    if file_path:
        media_paths.append(file_path)
        
        if msg_type == "file":
            # B2-C: Clear old pending_file when new file arrives
            if chat_type == "p2p":
                await self._clear_pending_file_for_new_upload(...)  # ← 清理
```

**问题**: 
- ❌ 清理在下载**成功后**
- ❌ 下载失败时不清理
- ❌ 不符合"识别后立即清理"的要求

### 修改后（正确）

**位置**: `runtime.py:3281`

```python
elif msg_type in ("image", "audio", "file", "media"):
    # B2-C: Clear old pending_file BEFORE downloading (p2p file only)
    if msg_type == "file" and chat_type == "p2p":
        await self._clear_pending_file_for_new_upload(
            sender_id=sender_id,
            new_message_id=message_id,
        )  # ← 立即清理
    
    file_path, content_text = await self._download_and_save_media(
        msg_type, content_json, message_id
    )  # ← 然后下载
```

**改进**:
- ✅ 文件消息识别后**立即**清理
- ✅ 下载前执行，覆盖下载失败场景
- ✅ 不支持格式也会被清理（在下载前）
- ✅ 只针对p2p文件消息

### 场景覆盖验证

| 场景 | 清理时机 | 是否清理 |
|------|---------|---------|
| 新文件正常下载 | 识别后、下载前 | ✅ |
| 新文件下载失败 | 识别后、下载前 | ✅ |
| 新文件不支持（PPTX） | 识别后、下载前 | ✅ |
| 新文件解析失败 | 识别后、下载前 | ✅ |
| 新文件入库失败 | 识别后、下载前 | ✅ |
| 普通文本消息 | 不触发 | ❌ |
| 图片消息 | 不触发（非file） | ❌ |
| 群聊文件 | 不触发（非p2p） | ❌ |

---

## 四、并发保护逻辑

### 核心机制

**使用message_id作为文件请求标识**

**原理**: 飞书保证每个消息的message_id唯一且不变

### 实现细节

#### 1. 清理旧pending_file（原子操作）

**位置**: `runtime.py:2059-2110`

```python
async def _clear_pending_file_for_new_upload(
    self,
    sender_id: str,
    new_message_id: str,
) -> bool:
    """Clear old pending_file when new file arrives."""
    
    def clear_meta(meta: dict[str, Any]) -> None:
        # 1. 删除旧pending_file
        if "pending_file" in meta:
            old_file = meta.get("pending_file", {})
            self.logger.info(
                "Clearing old pending_file: {} (new file arriving)",
                old_file.get("filename", "unknown"),
            )
            del meta["pending_file"]
        
        # 2. 记录当前文件请求标识
        meta["current_file_message_id"] = new_message_id
        return None
    
    await sessions.state.mutate_metadata(
        session_key,
        clear_meta,
        create_if_missing=True,
    )  # ← 原子执行
```

**关键**: 删除和标记在同一个原子操作中

#### 2. 保存新pending_file（验证message_id）

**位置**: `runtime.py:2004-2037`

```python
async def _save_pending_file_to_session(
    self,
    sender_id: str,
    file_id: str,
    chat_type: str,
    message_id: str | None = None,  # ← 新增参数
) -> bool:
    
    # Build pending_file structure
    pending_file = {
        "file_id": file_id,
        "filename": file_record["filename"],
        ...
        "message_id": message_id,  # ← 存储message_id
    }
    
    save_succeeded = False
    
    def save_to_meta(meta: dict[str, Any]) -> None:
        nonlocal save_succeeded
        
        # B2-C: Check if a newer file request has arrived
        current_file_message_id = meta.get("current_file_message_id")
        if message_id and current_file_message_id and current_file_message_id != message_id:
            # 更新的请求已到达，拒绝保存旧结果
            self.logger.info(
                "Skip saving pending_file: newer file request detected",
            )
            save_succeeded = False
            return None
        
        # 验证通过，保存
        meta["pending_file"] = pending_file
        save_succeeded = True
        return None
    
    await sessions.state.mutate_metadata(...)
    
    return save_succeeded  # ← 返回实际结果
```

**关键**: 
- ✅ 验证和保存在同一个原子操作中
- ✅ 返回实际保存结果

### 并发场景分析

#### 场景1: A、B连续上传，B先完成

```
T1: A消息到达 (message_id=msg_a)
    ↓
    清理旧pending_file, current_file_message_id=msg_a
    ↓
    开始处理A（慢）

T2: B消息到达 (message_id=msg_b)
    ↓
    清理旧pending_file, current_file_message_id=msg_b  ← 覆盖
    ↓
    开始处理B（快）

T3: B处理完成
    ↓
    验证: current_file_message_id(msg_b) == message_id(msg_b) ✅
    ↓
    保存pending_file={file_id: B} ✅

T4: A处理完成
    ↓
    验证: current_file_message_id(msg_b) != message_id(msg_a) ❌
    ↓
    拒绝保存 ✅
    ↓
    最终: pending_file={file_id: B} ✅
```

#### 场景2: A的旧任务不能清除B的新pending_file

```
T1: B已保存
    ↓
    pending_file = {file_id: B, message_id: msg_b}
    current_file_message_id = msg_b

T2: A的旧清理任务执行
    ↓
    尝试清理: current_file_message_id = msg_a
    ↓
    ❌ 问题: 会删除B的pending_file

解决方案: 清理只在新文件消息到达时执行，不会有旧任务
```

**保护**: 清理操作只在新文件消息识别时触发，不存在"旧清理任务"

#### 场景3: 不同用户隔离

```
User1: session_key = "feishu:ou_user1"
User2: session_key = "feishu:ou_user2"

SQLite中不同记录，完全隔离 ✅
```

### 并发保护验证清单

| 检查项 | 状态 | 说明 |
|--------|------|------|
| 清理+标记原子性 | ✅ | 同一个mutate_metadata |
| 验证+保存原子性 | ✅ | 同一个mutate_metadata |
| A后完成不覆盖B | ✅ | message_id验证 |
| A不清除B的pending_file | ✅ | 清理只在新消息时触发 |
| 不同用户隔离 | ✅ | 不同session_key |
| message_id缺失处理 | ✅ | 条件检查 `if message_id and ...` |
| 返回实际结果 | ✅ | save_succeeded flag |

---

## 五、修改文件列表

### 1. nanobot/channels/feishu/runtime.py

**统计**: +178, -82 (net +96行)

**修改内容**:

#### A. 调整清理时机 (3281-3289)
- 移动到文件消息识别后、下载前
- 只针对p2p文件消息

#### B. 新增清理方法 (2059-2110)
```python
async def _clear_pending_file_for_new_upload(
    self,
    sender_id: str,
    new_message_id: str,
) -> bool
```
- 删除旧pending_file
- 记录current_file_message_id
- 原子操作

#### C. 修改保存方法 (1928-2037)
```python
async def _save_pending_file_to_session(
    self,
    sender_id: str,
    file_id: str,
    chat_type: str,
    message_id: str | None = None,  # ← 新增
) -> bool
```
- 增加message_id参数
- 验证current_file_message_id
- 返回实际保存结果

#### D. 传递message_id (3307)
```python
saved = await self._save_pending_file_to_session(
    sender_id=sender_id,
    file_id=file_id,
    chat_type=chat_type,
    message_id=message_id,  # ← 传递
)
```

### 2. nanobot/channels/feishu/tests/test_feishu_pending_file_clear.py

**新增文件**: 263行

**测试用例**: 5个

1. `test_new_pdf_replaces_old_pdf` - 新PDF替换旧PDF
2. `test_pptx_clears_old_pdf` - 不支持格式清理旧文件
3. `test_text_message_does_not_clear` - 文本消息不清理
4. `test_concurrent_uploads_b_wins` - 并发上传B获胜
5. `test_different_users_isolated` - 不同用户隔离

**测试策略**:
- Mock `load_config`统一workspace路径
- 使用真实SQLite数据库
- 验证原子操作实际效果

---

## 六、测试结果

### Batch-2C专项测试

**命令**:
```bash
python -m pytest nanobot/channels/feishu/tests/test_feishu_pending_file_clear.py -v
```

**结果**: ✅ **5/5通过 (100%)**

```
test_new_pdf_replaces_old_pdf PASSED [ 20%]
test_pptx_clears_old_pdf PASSED [ 40%]
test_text_message_does_not_clear PASSED [ 60%]
test_concurrent_uploads_b_wins PASSED [ 80%]
test_different_users_isolated PASSED [100%]

============================= 5 passed in 17.68s ==============================
```

### Batch-2A回归测试

**命令**:
```bash
python -m pytest nanobot/channels/feishu/tests/test_feishu_welcome.py -v
```

**结果**: ✅ **13/13通过 (100%)**

```
============================= 13 passed in 10.18s ==============================
```

### Batch-2B回归测试

**命令**:
```bash
python -m pytest nanobot/channels/feishu/tests/test_feishu_unsupported_format.py -v
```

**结果**: ✅ **9/9通过 (100%)**

```
============================= 9 passed in 13.29s ==============================
```

### KB上传回归测试

**命令**:
```bash
python -m pytest nanobot/channels/feishu/tests/test_feishu_kb_upload.py -v
```

**结果**: ✅ **6/6通过 (100%)**

```
============================== 6 passed in 0.35s ===============================
```

### 总计

| 测试类别 | 数量 | 通过 | 失败 |
|---------|------|------|------|
| Batch-2C | 5 | 5 | 0 |
| Batch-2A | 13 | 13 | 0 |
| Batch-2B | 9 | 9 | 0 |
| KB回归 | 6 | 6 | 0 |
| **总计** | **33** | **33** | **0** |

**通过率**: **100%**

---

## 七、未解决问题

### 无重大问题

✅ 所有设计目标已实现
✅ 所有测试通过
✅ 并发保护机制完整

### 潜在改进（非阻塞）

1. **SessionManager依赖注入**
   - 当前: 每次创建新实例
   - 建议: Channel持有SessionManager实例
   - 优先级: 低（当前实现可用）

2. **端到端集成测试**
   - 当前: 单元测试覆盖
   - 建议: 真实飞书消息流测试
   - 优先级: 中（生产验证）

---

## 八、Git状态

### 修改文件

```
M  nanobot/channels/feishu/runtime.py
?? nanobot/channels/feishu/tests/test_feishu_pending_file_clear.py
?? docs/development/06-F06-B-Batch2C-progress.md
```

### Batch-2A和2B保留确认

**检查方法**:
```bash
git diff nanobot/channels/feishu/runtime.py | grep -E "welcome|SUPPORTED_EXTS|unsupported"
```

**结果**:
- ✅ Batch-2A欢迎消息代码保留
- ✅ Batch-2B不支持格式检测保留
- ✅ Batch-2C清理机制新增

### 代码质量检查

```bash
git diff --check
```

**结果**: ✅ 无尾随空格，无格式问题

### 统计

- **runtime.py**: +178, -82 (net +96行)
- **test_feishu_pending_file_clear.py**: 263行 (新增)
- **总增加**: 359行

---

## 九、核心改进总结

### 1. 清理时机优化

- **Before**: 下载成功后清理
- **After**: 消息识别后立即清理
- **改进**: 覆盖下载失败、格式不支持等所有场景

### 2. 并发保护

- **机制**: message_id作为请求标识
- **保护**: 旧任务不能覆盖新任务
- **实现**: SQLite原子操作

### 3. 测试质量

- **策略**: 真实SQLite + Mock配置
- **覆盖**: 5个场景，33个测试通过
- **验证**: 原子操作的实际效果

---

**完成时间**: 2026-10-31  
**状态**: ✅ **Batch-2C完成，所有测试通过，等待审查**
