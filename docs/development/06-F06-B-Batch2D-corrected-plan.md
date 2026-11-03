# F06-B Batch-2D 权限设计核实与修正报告

**核实日期**: 2026-10-31  
**核实性质**: 只读检查，不修改代码  
**完整报告**: docs/development/06-F06-B-Batch2D-corrected-plan.md

---

## 🔴 严重发现：KB文件记录缺失sender_id

### 问题描述

**KBUploadTool没有记录sender_id到kb_files表**

**代码证据**:

1. **Feishu调用KB上传** (`runtime.py:2267`):
```python
result = await kb_tool.execute(file_path=file_path)
# ❌ 没有传递sender_id参数
```

2. **KBUploadTool.execute()** (`kb_upload.py:257`):
```python
file_id = self._kb_store.create_file_record(
    filename=resolved.name,
    file_type=resolved.suffix[1:].lower(),
    file_size=resolved.stat().st_size,
    file_path=str(kb_file_path),
    file_hash=file_hash,
    status="pending",
    # ❌ 没有传递sender_id
)
```

3. **KB store支持sender_id** (`kb/store.py:23-48`):
```python
def create_file_record(self, ..., **kwargs):
    db.execute(
        """INSERT INTO kb_files (..., sender_id)
        VALUES (..., ?)""",
        (..., kwargs.get("sender_id"))  # ← 支持但未使用
    )
```

**结论**: ❌ **当前所有通过Feishu上传的文件，kb_files.sender_id都是NULL**

---

## 核实结果总结

### 1. kb_files是否覆盖所有Feishu文件？

**✅ 是** - 所有成功上传的支持格式文件都有记录

**流程**:
```
Feishu文件消息 → _download_and_save_media() → media/feishu/file.pdf
  ↓
_auto_upload_to_kb() → 检查格式
  ↓ (支持格式)
kb_tool.execute() → create_file_record() → kb_files表
  ↓
process_file() → 解析+分块+索引
```

### 2. 是否存在下载但没有kb_files记录的情况？

**✅ 存在** - 以下情况有文件但无记录：

| 场景 | media文件 | kb_files记录 | sender_id |
|------|----------|-------------|----------|
| 不支持格式(PPTX) | ✅ 存在 | ❌ 无 | N/A |
| 下载失败 | ❌ 无 | ❌ 无 | N/A |
| KB上传失败 | ✅ 存在 | ❌ 无 | N/A |
| 重复文件 | ✅ 存在 | ✅ 有（旧记录） | ❌ NULL |
| 正常文件 | ✅ 存在 | ✅ 有 | ❌ NULL |

**关键**: 不支持格式的文件会下载到media，但不会创建kb_files记录

### 3. kb_files.sender_id是否可靠写入？

**❌ 否** - 当前**完全没有写入**

**问题根源**:
1. Feishu调用时没有传递sender_id
2. KBUploadTool.execute()没有接收sender_id参数
3. create_file_record()没有调用时没有传递sender_id

**影响**: 
- ✅ 所有历史文件: sender_id = NULL
- ✅ 所有新上传文件: sender_id = NULL（除非修复）

### 4. 是否存在没有归属记录的情况？

**✅ 大量存在**:

| 文件类型 | kb_files记录 | sender_id | 可识别归属？ |
|---------|-------------|-----------|-----------|
| 不支持格式 | ❌ 无 | N/A | ❌ 无法识别 |
| 所有历史文件 | ✅ 有 | NULL | ❌ 无法识别 |
| 所有当前文件 | ✅ 有 | NULL | ❌ 无法识别 |

**结论**: ❌ **无法通过kb_files.sender_id验证任何文件的归属**

### 5. get_file_by_path()是否能准确区分？

**🟡 有风险** - 需要路径标准化

**问题**:
- 相对路径 vs 绝对路径
- 符号链接
- 大小写（Windows）
- 路径分隔符

**建议**: 存储和查询时都使用`Path.resolve()`标准化

### 6. CLI模式能否可靠区分？

**🟡 无法100%保证**

**问题**:
- RequestContext通过ContextVar传递
- 如果Feishu Channel未正确设置，会缺失
- 无法区分"真CLI"和"Feishu未设置Context"

**建议**: 
- 检查`request.channel`字段
- 如果`channel == "feishu"` → 必须有sender_id
- 如果`channel`为空或其他 → 可能是CLI

### 7. 群聊能否可靠获取sender_id和chat_type？

**需要检查** - 但当前Batch-2A/2C只处理p2p

---

## 🔴 修正后的权限设计

### 核心问题

**无法通过kb_files验证文件归属，因为sender_id全部为NULL**

### 修正方案

#### 方案A: 基于pending_file验证（推荐）

**原理**: 
- pending_file中有sender_id（Batch-2A已实现）
- pending_file是用户当前会话的上下文
- 只允许读取pending_file中的文件

**优点**:
- ✅ 不依赖kb_files.sender_id
- ✅ 与KB工具一致
- ✅ 符合"当前上下文"语义
- ✅ 不需要修改KB上传流程

**缺点**:
- ❌ pending_file清理后无法读取（即使是自己的历史文件）
- ❌ 无法读取非pending_file的文件

**实现**:
```python
def _validate_media_file_access(self, fp: Path) -> None | ToolResult:
    # 1. 获取RequestContext
    request = current_request_context()
    if not request or not request.channel:
        return None  # CLI模式
    
    if request.channel != "feishu":
        return None  # 非Feishu Channel
    
    if not request.sender_id or not request.session_key:
        return ToolResult.error("Permission denied")
    
    # 2. 获取pending_file
    session = await sessions.state.get(request.session_key)
    pending_file = session.metadata.get("pending_file")
    
    if not pending_file:
        return ToolResult.error("No file available. Upload a file first.")
    
    # 3. 验证pending_file.sender_id
    if pending_file.get("sender_id") != request.sender_id:
        return ToolResult.error("Permission denied")
    
    # 4. 通过file_id找到file_path，验证是否匹配
    file_record = kb_store.get_file(pending_file["file_id"])
    if not file_record:
        return ToolResult.error("File not found")
    
    if Path(file_record["file_path"]).resolve() != fp.resolve():
        return ToolResult.error("Permission denied - path mismatch")
    
    return None
```

**限制**: 
- 只能读取pending_file中的文件
- pending_file清理后无法读取

#### 方案B: 修复KB上传+双重验证（完整方案）

**步骤1**: 修复KBUploadTool传递sender_id

**步骤2**: 验证逻辑
```python
def _validate_media_file_access(self, fp: Path) -> None | ToolResult:
    request = current_request_context()
    
    # 1. 检查是否为Feishu请求
    if not request or request.channel != "feishu":
        return None  # CLI或其他Channel
    
    if not request.sender_id:
        return ToolResult.error("Permission denied")
    
    # 2. 查询kb_files
    file_record = kb_store.get_file_by_path(str(fp.resolve()))
    
    if not file_record:
        # 无记录：不支持格式或上传失败
        return ToolResult.error("Permission denied - file not in KB")
    
    file_sender = file_record.get("sender_id")
    if not file_sender:
        # 历史文件（NULL sender_id）
        return ToolResult.error("Permission denied - legacy file")
    
    if file_sender != request.sender_id:
        return ToolResult.error("Permission denied")
    
    return None
```

**优点**:
- ✅ 完整的归属验证
- ✅ 可以读取历史文件（如果修复后重新上传）
- ✅ 安全性最高

**缺点**:
- ❌ 需要修改KB上传流程
- ❌ 历史文件全部被拒绝
- ❌ 不支持格式的文件被拒绝

---

## 📋 推荐实施方案

### 方案：基于pending_file验证（方案A）

**理由**:
1. 不依赖kb_files.sender_id（当前为NULL）
2. 与KB工具权限检查一致
3. 符合用户预期（只能读当前上传的文件）
4. 不需要修改KB上传流程
5. 实施简单

**权限边界**:
- ✅ Feishu p2p: 只能读pending_file中的文件
- ❌ Feishu p2p: 不能读其他文件（包括自己的历史文件）
- ✅ CLI模式: 保持原有行为
- ✅ 非Feishu Channel: 保持原有行为

**向后兼容**:
- ✅ 不影响CLI
- ✅ 不影响其他Channel
- ❌ Feishu用户无法通过read_file读取历史文件（需通过kb_read）

---

## 修改清单

### 1. filesystem.py修改

**位置**: `nanobot/agent/tools/filesystem.py:ReadFileTool`

**新增方法**:
```python
def _is_feishu_media_file(self, fp: Path) -> bool
async def _validate_feishu_media_file_access(self, fp: Path) -> None | ToolResult
```

**修改execute()**:
```python
async def execute(self, path: str, ...):
    fp = self._resolve_read(path)
    
    # B2-D: Validate Feishu media file access
    if self._is_feishu_media_file(fp):
        validation_result = await self._validate_feishu_media_file_access(fp)
        if isinstance(validation_result, ToolResult):
            return validation_result
    
    # 继续原有逻辑
    ...
```

### 2. 无需修改kb/store.py

（方案A不依赖get_file_by_path）

### 3. 新增测试文件

**文件**: `nanobot/agent/tools/tests/test_read_file_feishu_permissions.py`

---

## 测试计划

### 测试用例（9个）

1. **test_feishu_read_pending_file_success**
   - Feishu用户上传文件
   - pending_file存在
   - read_file(该文件)
   - ✅ 成功

2. **test_feishu_read_other_user_file_denied**
   - UserA上传文件
   - UserB尝试read_file(UserA的文件)
   - ❌ Permission denied

3. **test_feishu_read_no_pending_file_denied**
   - Feishu用户没有pending_file
   - read_file(media文件)
   - ❌ No file available

4. **test_feishu_read_after_pending_file_cleared_denied**
   - 上传文件，pending_file被清理
   - read_file(该文件)
   - ❌ No file available

5. **test_feishu_read_path_mismatch_denied**
   - pending_file指向fileA
   - read_file(fileB)
   - ❌ Permission denied

6. **test_cli_read_media_file_allowed**
   - CLI模式（无RequestContext）
   - read_file(media文件)
   - ✅ 成功（保持兼容）

7. **test_non_feishu_channel_allowed**
   - 其他Channel（request.channel != "feishu"）
   - read_file(media文件)
   - ✅ 成功

8. **test_read_non_media_file_unaffected**
   - Feishu用户
   - read_file(workspace文件)
   - ✅ 成功（不受影响）

9. **test_feishu_no_sender_id_denied**
   - request.channel == "feishu"
   - 但request.sender_id为空
   - ❌ Permission denied

---

## 影响现有功能的地方

### 1. Feishu用户使用read_file

**Before**: 可以读取media目录下所有文件

**After**: 只能读取pending_file中的文件

**影响**: 
- ❌ 无法读取历史文件
- ❌ 无法读取其他用户文件
- ✅ 需要通过kb_read/kb_search访问文件内容

### 2. CLI模式

**Before**: 可以读取所有允许路径的文件

**After**: 保持不变

**影响**: ✅ 无

### 3. 其他Channel

**Before**: 可以读取所有允许路径的文件

**After**: 保持不变

**影响**: ✅ 无

---

## 需要确认的问题

### 1. 用户无法read_file读取历史文件

**场景**: 
- 用户上传fileA.pdf
- 稍后上传fileB.pdf（pending_file被清理）
- 用户想read_file读取fileA
- ❌ 被拒绝

**选项**:
- A. 接受此限制，引导用户使用kb_read ← **推荐**
- B. 实施方案B，修复KB上传记录sender_id

**建议**: 选项A，因为kb_read已有完整功能

### 2. 不支持格式的文件

**场景**:
- 用户上传report.pptx
- 下载到media，但无kb_files记录
- read_file被拒绝（无pending_file）

**选项**:
- A. 拒绝（当前方案）← **推荐**
- B. 允许（需要其他验证方式）

**建议**: 选项A，不支持格式本不应被读取

### 3. 群聊文件权限

**当前**: Batch-2A/2C只处理p2p，群聊不创建pending_file

**选项**:
- A. 暂不支持群聊read_file ← **推荐**
- B. 实施群聊共享读取

**建议**: 选项A，保持现状

---

## 总结

### 核心发现

1. ❌ **kb_files.sender_id全部为NULL**，无法用于权限验证
2. ✅ **pending_file有sender_id**，可用于验证
3. ✅ **方案A**基于pending_file验证，不依赖kb_files
4. ❌ 用户无法read_file读取历史文件（需用kb_read）

### 推荐方案

**方案A**: 基于pending_file验证
- 实施简单
- 不需要修改KB上传
- 与KB工具一致
- 限制：只能读当前pending_file

### 修改范围

- `filesystem.py`: 2个新方法 + execute()修改（~60行）
- 新测试文件: 9个测试（~400行）

### 实施优先级

**🔴 高** - 安全问题，建议尽快实施

---

**核实完成时间**: 2026-10-31  
**状态**: ✅ **权限设计已修正，等待确认后实施**
