# F06-B Batch-2E 飞书文件大小限制前置调查报告

**调查日期**: 2026-10-31  
**任务**: 增加50 MiB文件大小限制  
**限制值**: 50 × 1024 × 1024 = 52,428,800 bytes  
**状态**: ✅ **调查完成**

---

## 一、当前文件上传链路及关键代码位置

### 1.1 完整调用链

**入口**: `_on_message()` 接收飞书文件消息事件

**处理流程**:
```
1. _on_message() [runtime.py:3280+]
   ↓
2. 解析消息类型和内容 (msg_type="file", content_json)
   ↓
3. [Batch-2C] _clear_pending_file_for_new_upload() [行3266-3270]
   - 只针对p2p file
   - 清理旧pending_file
   ↓
4. _download_and_save_media() [行3272, 函数1847-1907]
   - 调用_download_file_sync()下载文件
   - 保存到本地media目录
   ↓
5. [Batch-2B] _auto_upload_to_kb() [行3279, 函数2173-2290+]
   - 检查文件扩展名（不支持格式拦截）
   - 创建KBUploadTool
   - 调用kb_tool.execute()
   ↓
6. KBUploadTool.execute() [kb_upload.py:214+]
   - 验证路径
   - 验证扩展名
   - **验证文件大小** [当前已存在]
   - 计算hash
   - 保存到KB
   - 解析和索引
   ↓
7. [Batch-2A] _save_pending_file_to_session() [行3290, 函数1929-2050]
   - 验证message_id
   - 原子保存pending_file
   ↓
8. [Batch-2A] _send_file_greeting() [行3297+, 函数2095-2169]
   - 发送欢迎消息
```

### 1.2 关键代码位置

| 函数 | 位置 | 功能 |
|------|------|------|
| `_on_message()` | runtime.py:3200+ | 消息接收入口 |
| `_clear_pending_file_for_new_upload()` | runtime.py:2056-2105 | Batch-2C清理 |
| `_download_and_save_media()` | runtime.py:1847-1907 | 下载文件 |
| `_download_file_sync()` | runtime.py:1797-1832 | 同步下载 |
| `_auto_upload_to_kb()` | runtime.py:2173-2290+ | 自动入库 |
| `_save_pending_file_to_session()` | runtime.py:1929-2050 | 保存pending_file |
| `_send_file_greeting()` | runtime.py:2095-2169 | 发送欢迎消息 |
| `KBUploadTool.execute()` | kb_upload.py:214+ | KB上传执行 |
| `KBUploadTool._validate_file_size()` | kb_upload.py:164-175 | **文件大小验证** |

---

## 二、文件大小元数据来源和可靠性

### 2.1 飞书消息事件中的文件大小字段

**调查结果**: ❌ **飞书文件消息事件中不包含可靠的文件大小字段**

**依据**:
1. 查看`_download_and_save_media()`函数，只从`content_json`提取`file_key`
2. 查看`_download_file_sync()`函数，使用`GetMessageResourceRequest`下载
3. 飞书SDK只提供`message_id`和`file_key`，不提供`size`字段
4. 代码中没有从`content_json`提取size的逻辑

**content_json结构** (实际):
```python
{
    "file_key": "xxx",
    # 没有size字段
}
```

### 2.2 文件大小获取方式

**当前实现**: 下载后获取

**位置**: `KBUploadTool._validate_file_size()` [kb_upload.py:164-175]

```python
def _validate_file_size(self, path: Path) -> str | None:
    size = path.stat().st_size  # 从本地文件获取
    if size > self._max_file_size_bytes:
        max_mb = self._max_file_size_bytes // (1024 * 1024)
        actual_mb = size / (1024 * 1024)
        return f"File too large: {actual_mb:.1f} MB (max: {max_mb} MB)"
    return None
```

**调用时机**: 在`execute()`中，下载后、解析前

### 2.3 可靠性评估

| 来源 | 可用性 | 可靠性 | 时机 |
|------|--------|--------|------|
| 飞书content_json | ❌ 不可用 | - | 消息接收时 |
| 飞书API元数据 | ❓ 未知 | ❓ 未验证 | 下载前 |
| 本地文件stat | ✅ 可用 | ✅ 100%准确 | 下载后 |

**结论**: ✅ **只能在下载后通过`path.stat().st_size`获取可靠的文件大小**

---

## 三、当前下载逻辑分析

### 3.1 下载流程

**_download_file_sync()** [runtime.py:1797-1832]:
```python
def _download_file_sync(self, message_id: str, file_key: str, resource_type: str = "file"):
    request = GetMessageResourceRequest.builder()
        .message_id(message_id)
        .file_key(file_key)
        .type(resource_type)
        .build()
    
    response = self._client.im.v1.message_resource.get(request)
    if response.success():
        file_data = response.file
        if hasattr(file_data, "read"):
            file_data = file_data.read()  # 完整读取到内存
        return file_data, response.file_name
```

**特点**:
- ✅ 完整下载到内存 (`file_data.read()`)
- ✅ 返回bytes数据
- ❌ 下载前无法获取size
- ❌ 无流式下载或部分下载

### 3.2 保存流程

**_download_and_save_media()** [runtime.py:1847-1907]:
```python
# 1. 下载（完整）
data, filename = await loop.run_in_executor(
    None, self._download_file_sync, message_id, file_key, msg_type
)

# 2. 保存到文件
if data:
    file_path = media_dir / filename
    with open(file_path, "wb") as f:
        f.write(data)
```

**问题**: ❌ **大文件会先完整下载到内存和磁盘，再检查大小**

---

## 四、推荐拦截位置

### 4.1 选项对比

| 拦截位置 | 时机 | 优点 | 缺点 | 推荐 |
|---------|------|------|------|------|
| 消息接收时 | 下载前 | 最早拦截，无开销 | ❌ 无size元数据 | ❌ 不可行 |
| 下载完成后 | 保存前 | 有size，拦截早 | 已下载到内存 | 🟡 次选 |
| KB上传前 | 当前位置 | ✅ 已实现 | ✅ 太晚，已保存文件 | ✅ 当前 |
| 保存pending_file前 | 入库后 | - | ❌ 太晚，已入库 | ❌ 不推荐 |

### 4.2 当前实现评估

**位置**: `KBUploadTool.execute()` → `_validate_file_size()` [kb_upload.py:228]

**执行顺序**:
```python
async def execute(self, file_path: str, **context) -> str | ToolResult:
    # 1. 解析路径
    resolved = self._resolve_path(file_path)
    
    # 2. 验证扩展名
    error = self._validate_extension(resolved)
    if error: return ToolResult.error(error)
    
    # 3. 验证文件大小 ✅ 当前位置
    error = self._validate_file_size(resolved)
    if error: return ToolResult.error(error)
    
    # 4. 计算hash
    # 5. 检查重复
    # 6. 保存到KB
    # 7. 解析和索引
```

**优点**:
- ✅ 已实现，默认50 MiB
- ✅ 在解析、索引前拦截
- ✅ 返回清晰的错误消息

**缺点**:
- ❌ 文件已下载到内存（可能OOM）
- ❌ 文件已保存到磁盘（浪费空间）

### 4.3 推荐方案

**方案A**: 保持当前位置，增强清理 ✅ **推荐**

**理由**:
1. 飞书API不提供下载前的size元数据
2. 无法在下载前拦截
3. 当前位置已有实现，只需：
   - 确保KBUploadTool使用50 MiB限制
   - 在runtime.py中提前检查本地文件大小
   - 超限时删除本地文件

**方案B**: 在runtime.py下载后立即检查 🟡 **可选**

**实现**:
```python
# _download_and_save_media() 中
if data:
    # 检查大小
    data_size = len(data)
    MAX_SIZE = 50 * 1024 * 1024
    if data_size > MAX_SIZE:
        # 不保存文件
        return None, f"[文件过大: {data_size/(1024*1024):.1f} MB，限制 50 MiB]"
    
    # 保存文件
    file_path = media_dir / filename
    with open(file_path, "wb") as f:
        f.write(data)
```

**优点**: 避免保存超限文件到磁盘  
**缺点**: 仍会下载到内存

---

## 五、超限文件完整处理流程

### 5.1 推荐处理流程（方案A增强版）

```
1. 飞书文件消息到达
   ↓
2. [Batch-2C] 清理旧pending_file ✅
   ↓
3. 下载文件到内存和磁盘 ❌ (无法避免)
   ↓
4. 在_auto_upload_to_kb()中：
   a. 检查本地文件大小（新增）
   b. 如果超限：
      - 删除本地文件
      - 返回用户友好提示
      - 不调用KBUploadTool
   ↓
5. 返回超限消息给用户
   ↓
6. 不保存pending_file ✅
   ↓
7. 不发送file greeting ✅
```

### 5.2 错误消息格式

**建议消息**:
```
⚠️ 文件过大：{filename} ({actual_size} MiB)

文件大小限制：50 MiB

请压缩文件后重新上传，或将文件分割成多个小文件。
```

**对齐Batch-2B格式**:
- 使用emoji警告
- 说明原因
- 给出建议

### 5.3 清理逻辑

**本地文件清理**:
```python
# 超限后删除本地文件
if file_path and Path(file_path).exists():
    Path(file_path).unlink()
    self.logger.info("Deleted oversized file: {}", file_path)
```

---

## 六、异常和边界场景

### 6.1 文件大小检查失败

**场景**: `path.stat()`抛出异常

**处理**: 
- 捕获异常
- 记录日志
- 返回通用错误消息

### 6.2 文件刚好等于50 MiB

**规则**: `size > MAX_SIZE` → 50 MiB可以上传

### 6.3 空文件

**当前**: KB工具会处理  
**无需特殊处理**

### 6.4 下载失败

**当前**: 返回None，不进入KB流程  
**无影响**

### 6.5 部分下载

**飞书SDK**: 全部成功或全部失败  
**无影响**

### 6.6 文件大小单位

**统一**: 使用MiB (1024)  
**限制**: 50 × 1024 × 1024 = 52,428,800 bytes

**显示格式**:
- 代码中: `50 MiB`
- 用户消息: `50 MiB` 或 `50 MB`（用户友好）

---

## 七、预计修改文件及最小修改方案

### 7.1 修改文件清单

| 文件 | 修改内容 | 行数估计 |
|------|---------|---------|
| runtime.py | _auto_upload_to_kb()增加size检查 | +20行 |
| 新增测试文件 | test_feishu_file_size_limit.py | +150行 |

### 7.2 最小修改方案

**runtime.py** - `_auto_upload_to_kb()`:

```python
async def _auto_upload_to_kb(self, file_path: str, sender_id: str | None = None) -> str | None:
    from pathlib import Path
    
    path = Path(file_path)
    ext = path.suffix.lower()
    
    # [Batch-2B] Check extension (existing)
    SUPPORTED_EXTS = {".pdf", ".docx", ".txt", ".md", ".xlsx"}
    if ext not in SUPPORTED_EXTS:
        # ... existing unsupported format handling
        return "⚠️ 暂时无法处理这个文件..."
    
    # [Batch-2E] Check file size (NEW)
    MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MiB
    try:
        file_size = path.stat().st_size
        if file_size > MAX_FILE_SIZE_BYTES:
            # Delete oversized file
            try:
                path.unlink()
                self.logger.info("Deleted oversized file: {} ({} bytes)", path.name, file_size)
            except Exception as e:
                self.logger.warning("Failed to delete oversized file: {}", e)
            
            # Return user-friendly message
            size_mb = file_size / (1024 * 1024)
            return (
                f"⚠️ 文件过大：{path.name} ({size_mb:.1f} MiB)\n\n"
                f"文件大小限制：50 MiB\n\n"
                f"请压缩文件后重新上传，或将文件分割成多个小文件。"
            )
    except Exception as e:
        self.logger.warning("Failed to check file size: {}", e)
        # Continue to KB upload (let it handle the error)
    
    # Continue to KB upload (existing code)
    try:
        # ... existing KB upload logic
```

### 7.3 无需修改的部分

| 组件 | 理由 |
|------|------|
| KBUploadTool | 已有50 MiB限制（防御性） |
| _download_and_save_media() | 无法下载前拦截 |
| _save_pending_file_to_session() | 依赖上游拦截 |
| filesystem.py (Batch-2D) | 不涉及上传 |

---

## 八、测试方案和回归范围

### 8.1 新增测试（Batch-2E专项）

**文件**: `nanobot/channels/feishu/tests/test_feishu_file_size_limit.py`

**测试场景** (至少6个):

1. **test_file_within_limit_uploaded**
   - 49 MiB文件成功上传

2. **test_file_exactly_limit_uploaded**
   - 50 MiB文件成功上传

3. **test_file_exceeds_limit_rejected**
   - 51 MiB文件被拒绝
   - 返回超限消息
   - 本地文件被删除

4. **test_oversized_file_not_saved_to_kb**
   - 超限文件不入库
   - KB中无记录

5. **test_oversized_file_no_pending_file**
   - 超限文件不保存pending_file
   - session无pending_file

6. **test_oversized_file_no_greeting**
   - 超限文件不发送greeting消息

### 8.2 回归测试范围

**必须运行**:
- Batch-2A: test_feishu_welcome.py (13个)
- Batch-2B: test_feishu_unsupported_format.py (9个)
- Batch-2C: test_feishu_pending_file_clear.py (8个)
- Batch-2D: test_read_file_feishu_permissions.py (8个)
- Batch-2E: test_feishu_file_size_limit.py (6个)
- KB上传: test_kb_upload.py + test_feishu_kb_upload.py (23个)

**总计**: 67个测试

### 8.3 测试实现注意事项

**Mock文件大小**:
```python
# Mock大文件
with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
    f.write(b"x" * (51 * 1024 * 1024))  # 51 MiB
    oversized_file = f.name

# 或使用truncate创建稀疏文件
```

**验证文件删除**:
```python
assert not Path(file_path).exists(), "Oversized file should be deleted"
```

---

## 九、与Batch-2B/2C/2D的兼容性分析

### 9.1 与Batch-2B (不支持格式) 的关系

**执行顺序**:
```
1. Batch-2B: 检查扩展名
   ↓ (不支持 → 返回错误)
2. Batch-2E: 检查文件大小 (NEW)
   ↓ (超限 → 返回错误)
3. KB上传
```

**兼容性**: ✅ **完全兼容**

**理由**:
- Batch-2B先执行（扩展名检查优先）
- Batch-2E后执行（大小检查次之）
- 两者都返回用户友好消息
- 都不会入库或保存pending_file

**消息格式对齐**:
- Batch-2B: `⚠️ 暂时无法处理这个文件：{filename}\n\n目前支持...`
- Batch-2E: `⚠️ 文件过大：{filename} ({size})\n\n文件大小限制...`

### 9.2 与Batch-2C (清理pending_file) 的关系

**执行顺序**:
```
1. Batch-2C: 清理旧pending_file
   ↓
2. 下载文件
   ↓
3. Batch-2E: 检查文件大小 (NEW)
   ↓ (超限 → 不保存新pending_file)
4. Batch-2C: 保存新pending_file (跳过)
```

**兼容性**: ✅ **完全兼容**

**关键点**:
- ✅ 超限文件不会保存pending_file
- ✅ 旧pending_file已被清理（Batch-2C完成）
- ✅ 不影响Batch-2C的并发保护
- ✅ 不影响用户隔离

**场景验证**:
```
用户上传51 MiB的fileA.pdf:
1. 清理旧pending_file ✅
2. 下载fileA.pdf
3. 检查大小：超限 ❌
4. 删除fileA.pdf
5. 返回超限消息
6. 不保存pending_file ✅
7. session中无pending_file ✅
```

### 9.3 与Batch-2D (read_file权限) 的关系

**Batch-2D功能**: read_file基于pending_file验证Feishu media文件权限

**影响分析**: ✅ **无影响**

**理由**:
- Batch-2E不修改read_file逻辑
- 超限文件不保存pending_file，read_file会拒绝访问（正确行为）
- 超限文件本地文件已删除，read_file找不到文件（正确行为）

**场景**:
```
用户上传51 MiB文件 → 超限拒绝
用户尝试read_file该文件:
1. 无pending_file → 拒绝 ✅
2. 文件不存在 → 拒绝 ✅
```

### 9.4 兼容性总结

| 批次 | 关系 | 兼容性 | 说明 |
|------|------|--------|------|
| Batch-2A | 独立 | ✅ 兼容 | 超限文件不发greeting |
| Batch-2B | 串行 | ✅ 兼容 | 扩展名优先，大小次之 |
| Batch-2C | 依赖 | ✅ 兼容 | 清理在前，拒绝保存在后 |
| Batch-2D | 独立 | ✅ 兼容 | 超限文件无pending_file |

---

## 十、其他考虑事项

### 10.1 配置化限制值

**当前**: 硬编码50 MiB

**建议**: 保持硬编码

**理由**:
- 简单直接
- KB工具已硬编码50 MiB
- 统一限制值

### 10.2 群聊文件

**当前**: 群聊文件也会下载和检查

**Batch-2E**: 同样限制50 MiB

**无特殊处理**

### 10.3 其他渠道

**范围**: 只影响飞书渠道

**其他渠道**: 不受影响

### 10.4 监控和日志

**建议日志**:
```python
self.logger.info(
    "File size limit exceeded: {} ({} bytes > {} bytes)",
    filename,
    actual_size,
    max_size
)
```

---

## 十一、总结与建议

### 11.1 关键发现

1. ❌ **飞书消息事件不提供文件大小元数据**
2. ✅ **只能在下载后检查大小**
3. ✅ **KB工具已有50 MiB限制（防御性）**
4. ✅ **推荐在_auto_upload_to_kb()中提前检查**
5. ✅ **超限文件需要删除本地文件**

### 11.2 推荐实现方案

**位置**: `runtime.py` - `_auto_upload_to_kb()`

**修改**: 
- 在Batch-2B扩展名检查后
- 在KB上传前
- 检查文件大小
- 超限则删除文件并返回错误

**优点**:
- ✅ 最小修改（~20行）
- ✅ 与Batch-2B/2C/2D完全兼容
- ✅ 用户友好的错误消息
- ✅ 不影响其他功能

### 11.3 实施检查清单

- [ ] 修改runtime.py的_auto_upload_to_kb()
- [ ] 增加文件大小检查逻辑
- [ ] 超限时删除本地文件
- [ ] 返回用户友好消息
- [ ] 编写6个专项测试
- [ ] 运行67个回归测试
- [ ] 验证与Batch-2B/2C/2D兼容性
- [ ] 更新文档

### 11.4 预计工作量

| 任务 | 工作量 | 说明 |
|------|--------|------|
| 代码修改 | 1小时 | runtime.py ~20行 |
| 测试编写 | 2小时 | 6个测试 ~150行 |
| 回归测试 | 0.5小时 | 运行67个测试 |
| 文档更新 | 0.5小时 | 添加Batch-2E文档 |
| **总计** | **4小时** | |

---

**调查完成时间**: 2026-10-31  
**状态**: ✅ **调查完成，等待实施确认**  
**推荐方案**: 在runtime.py的_auto_upload_to_kb()中增加大小检查  
**修改文件**: 1个代码文件 + 1个测试文件  
**回归范围**: 67个测试

