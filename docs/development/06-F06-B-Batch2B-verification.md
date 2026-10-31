# F06-B Batch-2B 最终定点核验报告（只读）

**核验日期**: 2026-10-30  
**性质**: 只读源码追踪  
**状态**: ✅ 完成  

---

## 核验结论

✅ **所有核验点通过，Batch-2B实施正确**

---

## 核验1：错误消息返回和后续处理

### 1.1 错误消息返回

**代码位置**: `nanobot/channels/feishu/runtime.py:2101-2119`

```python
# Only process supported document types
SUPPORTED_EXTS = {".pdf", ".docx", ".txt", ".md", ".xlsx"}
if ext not in SUPPORTED_EXTS:
    # Log unsupported format
    self.logger.info("Unsupported file format: {} ({})", path.name, ext)

    # Return user-friendly error message
    format_display = {...}

    return (
        f"⚠️ 暂时无法处理这个文件：{path.name}\n\n"
        f"目前支持 PDF、DOCX、TXT、Markdown、XLSX 格式。\n\n"
        f"PPTX、DOC 和图片内容识别暂不支持。"
    )
```

**返回值**: 包含错误提示的字符串（不是None）

### 1.2 调用点接收

**代码位置**: `nanobot/channels/feishu/runtime.py:3203`

```python
# Auto-upload document files to knowledge base
if msg_type == "file":
    kb_result = await self._auto_upload_to_kb(file_path, sender_id)
    
    # B2-A: Extract file_id and save pending_file
    if kb_result:  # ← 错误消息不是None，进入此分支 ✅
        result_str = str(kb_result)
        file_id = self._extract_file_id(result_str)  # ← 从错误消息提取file_id
```

**关键点**: 
- ✅ `kb_result`包含错误消息字符串
- ✅ `if kb_result:`判断为True（非空字符串）

---

## 核验2：错误消息到达Agent

### 2.1 file_id提取失败

**代码位置**: `nanobot/channels/feishu/runtime.py:1913-1926`

```python
def _extract_file_id(self, result_str: str) -> str | None:
    """Extract file_id from KBUploadTool result string.
    
    Handles formats:
    - "ID: {file_id}"
    - "(ID: {file_id}, ...)"
    - "File ID: {file_id}"
    """
    # Match various ID formats (case-insensitive)
    match = re.search(r'(?:File\s+)?ID:\s*([a-f0-9\-]{36})', result_str, re.IGNORECASE)
    if match:
        return match.group(1)
    return None  # ← 错误消息中无ID，返回None ✅
```

**错误消息内容**: `"⚠️ 暂时无法处理这个文件：..."`

**匹配结果**: ❌ 无"ID:"字样，返回None

### 2.2 不进入成功分支

**代码位置**: `nanobot/channels/feishu/runtime.py:3207-3228`

```python
if kb_result:
    result_str = str(kb_result)
    file_id = self._extract_file_id(result_str)
    
    if file_id:  # ← file_id=None，不进入 ✅
        # Save pending_file to session metadata
        saved = await self._save_pending_file_to_session(...)
        
        # B2-D: Send greeting and block only if saved successfully
        if chat_type == "p2p" and saved:
            try:
                await self._send_file_greeting(sender_id, file_id)
                # Successfully sent greeting, don't send to Agent
                self.logger.info("File greeting sent, blocking Agent flow")
                return  # ← ❌ 不会执行到这里
            except Exception as e:
                self.logger.warning("File greeting failed, using fallback: {}", e)
    
    # Group chat, save failed, or greeting failed: append KB result to content
    content_text = f"{content_text}\n{kb_result}"  # ← ✅ 执行到这里
```

**执行路径**:
```
kb_result = "⚠️ 暂时无法处理..."
  ↓
if kb_result: ✅ True
  ↓
file_id = None
  ↓
if file_id: ❌ False（跳过成功分支）
  ↓
content_text = f"{content_text}\n{kb_result}"  # ← ✅ 拼接错误消息
```

### 2.3 错误消息添加到content_text

**代码位置**: `nanobot/channels/feishu/runtime.py:3228`

```python
# Group chat, save failed, or greeting failed: append KB result to content
content_text = f"{content_text}\n{kb_result}"
```

**content_text值**:
```
[file: /path/to/file.pptx]
⚠️ 暂时无法处理这个文件：file.pptx

目前支持 PDF、DOCX、TXT、Markdown、XLSX 格式。

PPTX、DOC 和图片内容识别暂不支持。
```

### 2.4 添加到content_parts

**代码位置**: `nanobot/channels/feishu/runtime.py:3236`

```python
content_parts.append(content_text)
```

**content_parts内容**: 包含错误消息

### 2.5 构建最终content

**代码位置**: `nanobot/channels/feishu/runtime.py:3271`

```python
content = "\n".join(content_parts) if content_parts else ""
```

**最终content**: 包含错误消息的完整字符串

### 2.6 发送给Agent

**代码位置**: `nanobot/channels/feishu/runtime.py:3302-3315`

```python
# Forward to message bus
reply_to = chat_id if chat_type == "group" else sender_id
await self._handle_message(
    sender_id=sender_id,
    chat_id=reply_to,
    content=content,  # ← 包含错误消息 ✅
    media=media_paths,
    metadata={...},
    session_key=session_key,
    is_dm=chat_type == "p2p",
)
```

**结论**: ✅ **错误消息通过content参数发送给Agent**

---

## 核验3：B2-D early return检查

### 3.1 B2-D early return条件

**代码位置**: `nanobot/channels/feishu/runtime.py:3217-3221`

```python
# B2-D: Send greeting and block only if saved successfully
if chat_type == "p2p" and saved:  # ← 两个条件都必须满足
    try:
        await self._send_file_greeting(sender_id, file_id)
        # Successfully sent greeting, don't send to Agent
        self.logger.info("File greeting sent, blocking Agent flow")
        return  # ← early return（阻断Agent）
```

**触发条件**:
1. `chat_type == "p2p"` ✅
2. `saved == True` ← 关键

**saved变量来源**: `_save_pending_file_to_session()`的返回值

### 3.2 不支持格式不会触发early return

**执行路径**:
```
kb_result = "⚠️ 暂时无法处理..."
  ↓
file_id = self._extract_file_id(kb_result) → None
  ↓
if file_id:  ← ❌ False
  ↓
❌ 不调用_save_pending_file_to_session()
  ↓
❌ saved变量未定义/不存在
  ↓
❌ if saved: 不会执行
  ↓
❌ return不会执行
  ↓
✅ 继续执行，错误消息添加到content_text
  ↓
✅ 发送给Agent
```

**结论**: ✅ **不支持格式不会触发B2-D early return，错误消息不会被吞掉**

### 3.3 代码证据

**关键点**: `if file_id:`检查在`_save_pending_file_to_session()`调用前

```python
if kb_result:
    result_str = str(kb_result)
    file_id = self._extract_file_id(result_str)
    
    if file_id:  # ← 不支持格式这里为False
        # 整个成功分支不执行
        saved = await self._save_pending_file_to_session(...)
        if chat_type == "p2p" and saved:
            ...
            return  # ← 不会到达这里
    
    # 不支持格式执行到这里
    content_text = f"{content_text}\n{kb_result}"  # ← ✅
```

---

## 核验4：不会错误触发成功反馈

### 4.1 成功入库反馈逻辑

**成功入库返回值**:
```python
# runtime.py:2180-2186
if "ID:" in result_str:
    return "✅ File added to knowledge base successfully"
elif "already exists" in result_str.lower():
    return "ℹ️ File already in knowledge base"
else:
    return "✅ File uploaded to knowledge base"
```

**包含"ID:"**: 成功消息

### 4.2 不支持格式返回值

```python
# runtime.py:2116-2119
return (
    f"⚠️ 暂时无法处理这个文件：{path.name}\n\n"
    f"目前支持 PDF、DOCX、TXT、Markdown、XLSX 格式。\n\n"
    f"PPTX、DOC 和图片内容识别暂不支持。"
)
```

**不包含"ID:"**: 错误消息

### 4.3 file_id提取

**不支持格式**:
```python
file_id = self._extract_file_id("⚠️ 暂时无法处理...")
# 正则匹配: r'(?:File\s+)?ID:\s*([a-f0-9\-]{36})'
# 结果: None ✅
```

**成功入库**:
```python
file_id = self._extract_file_id("✅ File added (ID: abc-123-...)")
# 正则匹配成功
# 结果: "abc-123-..." ✅
```

**结论**: ✅ **不支持格式不会提取到file_id，不会触发成功反馈**

---

## 核验5：不会写入新的pending_file

### 5.1 pending_file写入条件

**代码位置**: `nanobot/channels/feishu/runtime.py:3211-3213`

```python
if file_id:  # ← 必须有file_id
    # Save pending_file to session metadata
    saved = await self._save_pending_file_to_session(
        sender_id=sender_id,
        file_id=file_id,
        chat_type=chat_type,
    )
```

**触发条件**: `file_id != None`

### 5.2 不支持格式的file_id

**执行路径**:
```
kb_result = "⚠️ 暂时无法处理..."
  ↓
file_id = self._extract_file_id(kb_result)
  ↓
正则匹配: r'(?:File\s+)?ID:\s*([a-f0-9\-]{36})'
  ↓
错误消息中无"ID:"
  ↓
file_id = None ✅
  ↓
if file_id:  ← ❌ False
  ↓
❌ 不调用_save_pending_file_to_session()
  ↓
❌ pending_file不会被更新
```

### 5.3 pending_file状态

**场景1**: 无旧pending_file
```
用户上传PPTX
  ↓
pending_file保持为空 ✅
```

**场景2**: 有旧pending_file
```
T1: 上传PDF → pending_file = {file_id: abc}
T2: 上传PPTX → pending_file仍 = {file_id: abc} ⚠️
```

**注意**: 场景2是已知问题，留给Batch-2C解决（清理旧pending_file）

**结论**: ✅ **不支持格式不会写入新的pending_file**

---

## 完整调用链追踪

### 成功格式（PDF）

```
_auto_upload_to_kb("test.pdf", sender_id)
  ↓
ext = ".pdf" ∈ SUPPORTED_EXTS ✅
  ↓
KBUploadTool.execute()
  ↓
返回: "✅ File uploaded (ID: abc-123-...)"
  ↓
kb_result = "✅ File uploaded (ID: abc-123-...)"
  ↓
file_id = "abc-123-..." ✅
  ↓
_save_pending_file_to_session() ✅
  ↓
saved = True
  ↓
chat_type == "p2p" and saved ✅
  ↓
_send_file_greeting() ✅
  ↓
return（阻断Agent）
  ↓
❌ 消息不发送给Agent（已通过greeting反馈）
```

### 不支持格式（PPTX）

```
_auto_upload_to_kb("test.pptx", sender_id)
  ↓
ext = ".pptx" ∉ SUPPORTED_EXTS ❌
  ↓
返回: "⚠️ 暂时无法处理这个文件：test.pptx\n\n..."
  ↓
kb_result = "⚠️ 暂时无法处理..."
  ↓
file_id = None ❌
  ↓
❌ 不调用_save_pending_file_to_session()
  ↓
❌ 不调用_send_file_greeting()
  ↓
❌ 不执行return
  ↓
content_text = f"[file: ...]\n{kb_result}"
  ↓
content_parts.append(content_text)
  ↓
content = "\n".join(content_parts)
  ↓
_handle_message(content=content) ✅
  ↓
InboundMessage发送给Agent ✅
  ↓
Agent收到错误消息，转述给用户 ✅
```

---

## 测试覆盖验证

### 已有测试覆盖

**test_unsupported_format_not_uploaded_to_kb**:
```python
# 验证不支持格式不触发KB工具
result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")
mock_tool.execute.assert_not_called()  # ✅
assert "⚠️" in result  # ✅
```

**test_pptx_rejected_with_message**:
```python
# 验证返回错误消息
result = await ch._auto_upload_to_kb(str(test_file), "ou_user1")
assert result is not None  # ✅
assert "⚠️" in result  # ✅
assert "presentation.pptx" in result  # ✅
```

### 测试缺口检查

**已覆盖**:
- ✅ 不支持格式返回错误消息
- ✅ 错误消息包含文件名
- ✅ 不触发KB工具
- ✅ 所有支持格式正常

**未直接覆盖**（但逻辑正确）:
- ⚠️ 错误消息到达Agent（集成测试层面）
- ⚠️ B2-D early return不会被触发（需要端到端测试）

**建议**: 端到端测试验证完整流程

---

## 核验结论

### 核验1：错误消息返回和后续处理
✅ **通过** - 错误消息正确返回并添加到content_text

### 核验2：错误消息到达Agent
✅ **通过** - 错误消息通过InboundMessage.content发送给Agent

### 核验3：B2-D early return检查
✅ **通过** - 不支持格式不会触发early return，错误消息不会被吞掉

### 核验4：不会错误触发成功反馈
✅ **通过** - 错误消息无"ID:"，file_id=None，不触发成功流程

### 核验5：不会写入新pending_file
✅ **通过** - file_id=None，不调用_save_pending_file_to_session()

---

## 最终结论

✅ **Batch-2B实施正确，所有核验点通过**

**核心机制**:
1. 不支持格式返回包含"⚠️"的错误消息（不是None）
2. 错误消息无"ID:"，`_extract_file_id()`返回None
3. `if file_id:`检查失败，跳过成功分支
4. 错误消息拼接到content_text
5. 通过InboundMessage发送给Agent
6. Agent转述给用户

**已知限制**（Batch-2C处理）:
- ⚠️ 不支持格式不清理旧pending_file

**建议**:
- 端到端测试验证用户实际收到错误消息
- 验证群聊和私聊都能正确展示

---

**核验完成时间**: 2026-10-30  
**状态**: ✅ **核验通过，Batch-2B可以提交**
