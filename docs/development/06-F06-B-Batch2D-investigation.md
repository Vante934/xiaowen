# F06-B Batch-2D 实施前检查报告

**检查日期**: 2026-10-31  
**检查性质**: 只读检查，不修改代码  
**检查范围**: read_file工具权限约束  
**当前状态**: Batch-2A/2B/2C已完成，未提交

---

## 一、read_file工具的定义和实现

### 1.1 工具定义

**文件**: `nanobot/agent/tools/filesystem.py`  
**类名**: `ReadFileTool`  
**基类**: `_FsTool`  
**行号**: 264-460+

**基本信息**:
- **工具名**: `read_file`
- **作用域**: `{"core", "subagent", "memory"}`
- **只读**: 是
- **最大文件大小**: 100 MiB
- **最大字符数**: 128,000

### 1.2 支持的文件类型

```python
# 文本文件: 自动检测编码
# PDF: 通过extract_pdf_pages提取（最多20页）
# Office: .docx, .xlsx, .pptx
# 图片: 通过MIME检测，返回image content blocks
```

### 1.3 调用链

```
Agent LLM生成tool_call
  ↓
AgentRunner.run() [agent/runner.py]
  ↓
tool.execute() [agent/tools/filesystem.py:310+]
  ↓
_resolve_read(path) [filesystem.py:150-177]
  ↓
_resolve_with_extra() [filesystem.py:120-148]
  ↓
current_tool_workspace() [security/workspace_access.py]
  ↓
resolve_workspace_path() [agent/tools/path_utils.py]
  ↓ 权限检查
返回resolved Path或抛出异常
```

---

## 二、当前权限边界

### 2.1 路径解析机制

**核心方法**: `_resolve_read(path: str) -> Path`  
**位置**: `filesystem.py:150-177`

**允许读取的路径**:

1. **workspace目录** (如果restrict_to_workspace=False)
2. **extra_read_allowed_dirs** (额外允许目录):
   - `BUILTIN_SKILLS_DIR` - 内置技能目录
   - `workspace/skills` - 用户技能目录
   - **plugin skill dirs** - 插件技能目录（动态）
3. **extra_read_allowed_files** (额外允许文件):
   - `workspace/memory/history.jsonl` - 历史记录（只读）
4. **media目录** (`include_media_dir=True`):
   - **关键**: `get_media_dir("feishu")` → `~/.nanobot/data/media/feishu/`
   - **此目录包含所有Feishu下载的文件**

### 2.2 当前可读取的范围

| 路径类型 | 示例 | 限制 |
|---------|------|------|
| workspace文件 | `workspace/file.txt` | 根据restrict_to_workspace |
| 内置技能 | `BUILTIN_SKILLS_DIR/**` | 只读 |
| 用户技能 | `workspace/skills/**` | 只读 |
| 历史记录 | `workspace/memory/history.jsonl` | 只读 |
| **Media目录** | `~/.nanobot/data/media/feishu/**` | **✅ 完全可读** |

**关键发现**: 
- ✅ `read_file`通过`include_media_dir=True`可以读取**所有**media目录下的文件
- ❌ **没有按用户隔离**
- ❌ **没有验证文件归属**

---

## 三、文件存储和归属信息

### 3.1 Feishu文件下载位置

**代码位置**: `channels/feishu/runtime.py:1847-1906`

**存储结构**:
```
~/.nanobot/data/media/feishu/
├── user1_file1.pdf
├── user2_file2.pdf
├── user1_file3.docx
└── ...
```

**问题**: 
- ❌ 文件名中**没有用户ID前缀**
- ❌ **所有用户的文件混在同一目录**
- ❌ 文件名可能冲突（依赖safe_filename去重）

### 3.2 KB文件记录

**数据库字段**:
```sql
CREATE TABLE kb_files (
    file_id TEXT PRIMARY KEY,
    filename TEXT,
    file_path TEXT,  -- 物理路径
    sender_id TEXT,  -- 飞书用户ID ← 归属标识
    feishu_message_id TEXT,
    status TEXT,
    ...
)
```

### 3.3 pending_file机制

**结构**:
```python
pending_file = {
    "file_id": "uuid",
    "filename": "file.pdf",
    "sender_id": "ou_user1",  # ← 归属标识
    "status": "pending",
    ...
}
```

---

## 四、已发现的安全问题

### 🔴 问题1: read_file可以越权读取其他用户的文件

**严重程度**: **严重**

**问题描述**:
1. 用户A上传`fileA.pdf`到media目录
2. 用户B知道文件名或路径
3. 用户B调用`read_file("/path/to/fileA.pdf")`
4. ✅ **成功读取**（没有权限检查）

**代码证据**:

`ReadFileTool.execute()` (filesystem.py:310+):
```python
async def execute(self, path: str, ...):
    try:
        fp = self._resolve_read(path)  # ← 只检查路径是否在allowed范围
        # ❌ 没有检查文件归属
        # ❌ 没有检查sender_id
        
        raw = fp.read_bytes()  # ← 直接读取
        ...
```

**攻击场景**:
```
T1: UserA上传sensitive.pdf → media/sensitive.pdf
T2: UserB猜测文件名
T3: UserB: "read /home/user/.nanobot/data/media/feishu/sensitive.pdf"
T4: ✅ UserB成功读取UserA的文件 ❌
```

---

### 🟡 问题2: KB工具与read_file功能交叉

**问题描述**:
- `kb_read`工具: 读取KB文件内容（✅ 有权限检查）
- `read_file`工具: 读取任意文件（❌ 无权限检查）
- 用户可以绕过`kb_read`直接用`read_file`

| 工具 | 权限检查 | 归属验证 |
|------|---------|---------|
| kb_read | ✅ 验证pending_file | ✅ 验证sender_id |
| kb_search | ✅ 验证pending_file | ✅ 验证sender_id |
| **read_file** | ❌ 无 | ❌ 无 |

---

## 五、KB工具的权限机制（参考）

### 5.1 KB工具的权限检查

**代码位置**: `agent/tools/kb_access.py:107-165`

**验证步骤**:
1. ✅ 获取RequestContext (包含sender_id)
2. ✅ 检查session_key
3. ✅ 检查sender_id
4. ✅ 获取pending_file from metadata
5. ✅ 验证pending_file.sender_id == request.sender_id
6. ✅ 获取KB file_record
7. ✅ 验证file_record.sender_id == request.sender_id
8. ✅ 检查文件状态

**关键**: 双重验证（Session层 + KB层）

### 5.2 RequestContext传递

**代码位置**: `agent/tools/context.py:14-44`

```python
@dataclass(frozen=True)
class RequestContext:
    sender_id: str | None  # ← 用户身份标识
    session_key: str | None
    ...

def current_request_context() -> RequestContext | None
```

---

## 六、建议的最小修改方案

### 方案概述

**目标**: 为read_file添加sender_id验证，防止跨用户文件读取

**原则**:
- ✅ 最小侵入
- ✅ 复用现有机制（RequestContext, sender_id）
- ✅ 与KB工具对齐
- ✅ 向后兼容（非Feishu文件不受影响）

### 核心修改

#### 修改1: 为ReadFileTool添加media文件权限检查

**位置**: `agent/tools/filesystem.py:ReadFileTool.execute()`

**逻辑**:
```python
async def execute(self, path: str, ...):
    fp = self._resolve_read(path)
    
    # B2-D: Check media file ownership
    if self._is_media_file(fp):
        validation_result = self._validate_media_file_access(fp)
        if isinstance(validation_result, ToolResult):
            return validation_result
    
    # 继续原有逻辑
    raw = fp.read_bytes()
    ...
```

#### 修改2: 实现_is_media_file检查

```python
def _is_media_file(self, fp: Path) -> bool:
    """Check if file is in media directory."""
    try:
        from nanobot.config.paths import get_media_dir
        media_dir = get_media_dir("feishu").resolve()
        return fp.resolve().is_relative_to(media_dir)
    except (ValueError, OSError):
        return False
```

#### 修改3: 实现_validate_media_file_access

```python
def _validate_media_file_access(self, fp: Path) -> None | ToolResult:
    """Validate media file access against sender_id."""
    # 1. Get RequestContext
    request = current_request_context()
    if not request or not request.sender_id:
        return None  # Allow if no context (CLI mode)
    
    # 2. Get KB store and find file by path
    kb_store = ...
    file_record = kb_store.get_file_by_path(str(fp))
    
    # 3. Verify sender_id
    if file_record:
        file_sender = file_record.get("sender_id")
        if file_sender and file_sender != request.sender_id:
            return ToolResult.error("Error: Permission denied")
    
    return None  # Allow if no record or sender matches
```

#### 修改4: KB store添加get_file_by_path方法

**位置**: `kb/store.py:KnowledgeBaseStore`

```python
def get_file_by_path(self, file_path: str) -> dict[str, Any] | None:
    """Get a file record by file_path."""
    # SQL: SELECT * FROM kb_files WHERE file_path = ?
```

---

## 七、边缘情况处理

| 场景 | 处理策略 |
|------|---------|
| RequestContext缺失 | ✅ 允许（CLI模式） |
| KB中无文件记录 | ✅ 允许（旧文件） |
| sender_id为空 | ✅ 允许（向后兼容） |
| 非media文件 | ✅ 不检查（不影响） |

---

## 八、测试计划

### 新增测试文件

`nanobot/agent/tools/tests/test_read_file_permissions.py`

### 测试用例（8个）

1. ✅ test_read_own_media_file
2. ❌ test_read_other_user_media_file (权限拒绝)
3. ✅ test_read_non_media_file
4. ✅ test_read_media_file_no_kb_record
5. ✅ test_read_media_file_no_sender_id_in_record
6. ✅ test_read_media_file_no_request_context
7. ✅ test_read_media_file_after_pending_file_cleared
8. ❌ test_read_media_file_path_traversal

---

## 九、与Batch-2A/2B/2C的兼容性

| 批次 | 影响 | 说明 |
|------|------|------|
| Batch-2A | ✅ 无冲突 | 欢迎消息不涉及文件 |
| Batch-2B | ✅ 无冲突 | 格式检测在KB层 |
| Batch-2C | 🟡 独立 | 清理机制与read_file权限独立 |

---

## 十、需要确认的业务规则

### 1. 向后兼容策略

**问题**: 现有用户已上传的文件（无sender_id记录）

**选项**:
- A. 禁止读取（严格模式）
- B. 允许读取（兼容模式） ← **推荐**

**建议**: 只验证新文件，旧文件允许访问

### 2. CLI模式行为

**问题**: CLI模式下没有RequestContext

**选项**:
- A. 禁止读取media文件
- B. 允许读取所有文件 ← **推荐**

**建议**: CLI模式不做权限限制

### 3. 群聊文件权限

**当前**: Batch-2A/2C只处理p2p

**建议**: 保持现状，Batch-2D只处理p2p

### 4. 文件删除策略

**问题**: pending_file删除后，物理文件是否允许访问？

**选项**:
- A. 允许（用户自己的历史文件）← **推荐**
- B. 禁止（pending_file删除=权限撤销）

**建议**: 保留访问权限

---

## 十一、总结

### 核心问题

**🔴 read_file工具可以越权读取其他用户上传到media目录的文件**

### 建议方案

1. 为ReadFileTool添加media文件权限检查
2. 复用RequestContext.sender_id验证
3. 通过KB store查询文件归属
4. 向后兼容（旧文件、CLI模式）

### 实施优先级

**🔴 高** - 安全问题，建议尽快实施

### 修改范围

- `filesystem.py`: 3个新方法（~50行）
- `kb/store.py`: 1个新方法（~15行）
- 新测试文件: 8个测试用例（~300行）

---

**检查完成时间**: 2026-10-31  
**下一步**: 等待业务规则确认后开始实施
