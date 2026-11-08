<img src="images/飞书小文.png" align="left" width="95" alt="Xiaowen Logo" />

# 小文 (Xiaowen)

**具备工具调用、任务执行与个人知识库能力的智能 Agent**  
*Your smart AI agent with Knowledge Base & Tool execution — right in Feishu.*

[![python](https://img.shields.io/badge/python->=3.11-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
![feishu](https://img.shields.io/badge/feishu-WebSocket-00D6B9.svg?logo=feishu&logoColor=white)
![test](https://img.shields.io/badge/Test%20Suite-passing-brightgreen.svg)
![docs](https://img.shields.io/badge/docs-nanobot.wiki-blue.svg)

[简体中文](#小文-xiaowen) | [English](#english)

<br clear="left" />

---

**小文** 帮助你在飞书中完成日常对话、文档处理和知识检索：理解用户意图，调用工具执行任务，并基于上传文档进行内容总结与问答。

---

## 从这里开始

| 你想…… | 前往 |
| :--- | :--- |
| **快速安装与环境配置** | [环境要求与源码安装](#项目运行) |
| **配置大模型与飞书机器人** | [配置 LLM 与飞书登录](#启用飞书并登录) |
| **使用个人知识库处理文档** | [知识库使用说明](#知识库使用说明) |
| **了解系统整体架构** | [系统架构](#系统架构) |
| **查看当前已实现的功能与限制** | [已实现能力范围](#已实现能力范围以源码为准) |
| **参与开发与运行单元测试** | [开发与测试](#开发与测试可选) |

---

## 核心功能

### 🤖 Agent 对话与任务执行
- 理解自然语言请求，结合上下文完成多轮对话
- 调用工具完成读文件、检索、执行等操作
- 在飞书中返回结果，支持进度反馈与流式输出（取决于飞书应用权限）

### 🛠️ 工具调用机制
- 内置工具自动发现与注册
- 支持知识库上传、检索与文件访问相关能力
- 可按配置扩展 Provider / MCP 等外部能力（视本地配置而定）

### 💬 飞书消息交互
- 通过 WebSocket 长连接接入飞书 / Lark，无需公网 Webhook
- 支持私聊与群聊（群聊可配置为 mention 策略）
- 首次交互发送欢迎说明；私聊文件到达后可进行意图确认
- 支持配对（pairing）或白名单控制用户访问

### 📁 飞书文件接收与处理
- 自动接收飞书文件消息并下载到本地运行目录
- 校验文件格式与大小
- 支持 SHA256 去重，避免重复入库
- 超限或不支持格式会给出明确提示，不进入后续索引流程

### 📚 个人知识库
- 文档入库、解析、切分、Embedding 与向量索引
- 基于向量检索关联原始文件来源
- 会话级 pending 文件管理，降低并发上传时的状态混乱
- 文件访问边界约束，降低跨会话误读个人文件的风险

---

### 已实现能力范围（以源码为准）

| 能力 | 状态 |
|------|------|
| 飞书对话 / 文件接收 | 已实现 |
| 格式校验、50 MiB 大小限制 | 已实现 |
| PDF / DOCX / TXT / Markdown / XLSX 解析 | 已实现 |
| 文本分块、Embedding、FAISS 索引 | 已实现（需额外安装向量依赖） |
| 知识库检索与上传工具 | 已实现 |
| PPTX / 旧版 DOC / 图片 OCR | **未实现** |
| 扫描版 PDF OCR | **未实现** |

---

## 系统架构

```mermaid
flowchart LR
  U[飞书用户] -->|消息 / 文件| F[Feishu Channel]
  F --> B[Message Bus]
  B --> A[Agent Loop]
  A --> T[Tools]
  T --> KB[Knowledge Base]
  KB --> S[(SQLite 元数据)]
  KB --> V[(FAISS 向量索引)]
  A -->|回复| F
  F --> U
