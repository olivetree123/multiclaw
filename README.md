# MultiClaw

MultiClaw 是一个支持单 Agent 与多 Agent 协作的 AI 编程助手。多 Agent 模式按固定流程推进：**需求确认 → 样品开发 → 后端开发 → 前端对接 → 维护阶段**。

## 环境准备

```shell
# 1. 编辑环境变量
# 配置 LLM_MODEL、LLM_BASE_URL、LLM_API_KEY 等
./docker/.env

# 2. 启动数据库（PostgreSQL + Qdrant）
cd docker
docker compose -f docker-compose.yaml up -d

# 3. 回到项目根目录
cd ..
```

## 运行模式

### 单 Agent 模式

适合通用对话，或手动指定一个目录进行开发：

```shell
uv run python main.py --workspace /path/to/your/project
```

可选参数：

| 参数 | 说明 |
|------|------|
| `--workspace` | 限制文件/Shell 工具只能操作该目录 |
| `--session-id` | 继续已有会话（UUID） |

任意阶段输入 `exit` 退出。

---

### 多 Agent 模式（推荐）

适合从零搭建前后端分离项目，由不同 Agent 分工协作：

```shell
uv run python main.py --multi-agent --project-root ./my-project
```

启动后会在 `project-root` 下自动创建：

```
my-project/
├── docs/       # 产品经理：需求文档（requirements.md）
├── frontend/   # 前端：样品页面 + 对接后的 UI
└── backend/    # 后端：API 实现（docs/openapi.yaml）与测试
```

可选参数：

| 参数 | 说明 |
|------|------|
| `--multi-agent` | 启用多 Agent 开发流程 |
| `--project-root` | 项目根目录，默认当前目录 `.` |
| `--session-id` | 继续已有会话，保留各 Agent 的对话记忆 |

---

## 多 Agent 执行流程

整体为**线性五阶段**，每阶段内可反复对话修改，通过关键词进入下一阶段：

```
┌─────────────┐    确认需求     ┌─────────────┐    确认样品     ┌─────────────┐
│ 1. 需求确认  │ ──────────────► │ 2. 样品开发  │ ──────────────► │ 3. 后端开发  │
│  产品经理    │                 │  前端(Mock)  │                 │    后端      │
└─────────────┘                 └─────────────┘                 └──────┬──────┘
                                                                       │ 开始对接
                                                                       ▼
┌─────────────┐    进入维护     ┌─────────────┐                 ┌─────────────┐
│ 5. 维护阶段  │ ◄────────────── │ 4. 前端对接  │ ◄───────────────│             │
│   协调员     │                 │    前端      │                 │             │
└─────────────┘                 └─────────────┘                 └─────────────┘
```

### 阶段 1：需求确认（产品经理）

- **职责**：分析需求、追问澄清、撰写 PRD，写入 `docs/requirements.md`
- **操作**：描述你想做的产品，与产品经理反复对话
- **进入下一阶段**：输入 `确认需求`、`需求确认` 或 `开始开发`；或产品经理回复中含 `<!-- STATUS: confirmed -->`
- **退出**：输入 `exit`

信息不足时，产品经理会在回复中标记 `<!-- STATUS: needs_clarification -->`。在 **HTTP API 模式**下，系统会自动将其转为结构化澄清表单（见下文「需求澄清表单」）。

示例：

```
Enter your message: 做一个 Todo 应用，支持优先级和截止日期
Enter your message: 确认需求
```

### 阶段 2：样品开发（前端 · Mock 数据）

- **职责**：用 Mock 数据搭建可交互的前端页面，不涉及真实后端
- **产出**：`frontend/src/mocks/` 下的样例数据与类型说明
- **操作**：查看样品效果，提出 UI/交互修改，可反复迭代
- **进入下一阶段**：输入 `确认样品`、`样品确认` 或 `确认原型`
- **退出**：输入 `exit`

### 阶段 3：后端开发（后端）

- **职责**：根据需求与样品 Mock 数据结构，设计并实现 API
- **产出**：`backend/docs/openapi.yaml`（API 权威契约）、接口实现与测试
- **操作**：首次会自动开始开发；之后可继续提修改意见
- **进入下一阶段**：输入 `开始对接`、`对接接口` 或 `开始集成`
- **退出**：输入 `exit`

### 阶段 4：前端对接（前端 · 真实 API）

- **职责**：将 Mock 数据层替换为真实 API 调用，保持 UI 与样品一致
- **操作**：对接完成后可继续提出修改
- **进入下一阶段**：输入 `进入维护`、`完成开发` 或 `维护模式`
- **退出**：输入 `exit`（直接退出，不进入维护）

### 阶段 5：维护阶段（协调员）

- **职责**：项目初始开发完成后，协调员根据你的描述委派合适的 Agent 修改
- **路由规则**：
  - UI / 样式 / 交互 / 前端 bug → **前端**
  - API / 业务逻辑 / 后端 bug → **后端**
  - 需求变更 / 新功能 → **产品经理** → 再按需调用后端、前端
- **操作**：直接描述要改什么，协调员自动委派
- **退出**：输入 `exit`

示例：

```
Enter your message: 把删除按钮改成红色          → 委派前端
Enter your message: 列表接口需要支持分页        → 委派后端
Enter your message: 加一个导出 Excel 功能      → 委派产品经理，再委派后端/前端
```

---

## 继续维护已有项目

开发流程已完成、程序已退出后，可跳过前四个阶段，直接进入维护：

```shell
uv run python main.py --continue --project-root ./my-project --session-id <之前的UUID>
```

说明：

- `--continue` 会检测 `docs/requirements.md`、`backend/docs/openapi.yaml` 或 `frontend/` 是否存在
- 建议配合 `--session-id` 使用，以恢复各 Agent 的对话记忆
- 若不传 `--session-id`，仍会从磁盘加载需求与 API 文件作为上下文，但 Agent memory 为新会话

---

## HTTP API 模式

> 完整接口文档见 **[API.md](./API.md)**，供前端项目对接。

将多 Agent 流程封装为 REST API，便于集成到 Web 前端或其他服务。

### 启动服务

```shell
uv run python main.py --serve --host 0.0.0.0 --port 8000
```

启动后访问 `http://localhost:8000/docs` 查看 Swagger 文档。

### 接口说明

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `GET` | `/api/v1/sessions` | 获取会话列表 |
| `POST` | `/api/v1/sessions` | 创建会话 |
| `GET` | `/api/v1/sessions/{session_id}` | 查询会话详情 |
| `PATCH` | `/api/v1/sessions/{session_id}` | 修改会话工作目录 |
| `GET` | `/api/v1/sessions/{session_id}/messages` | 获取会话消息列表 |
| `POST` | `/api/v1/sessions/{session_id}/messages` | 发送用户消息（`stream: true` 时返回 SSE） |
| `POST` | `/api/v1/sessions/{session_id}/messages/stream` | 发送用户消息（SSE 流式） |
| `DELETE` | `/api/v1/sessions/{session_id}` | 删除会话 |

### 创建会话

```shell
curl -X POST http://localhost:8000/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d "{\"project_root\": \"./my-project\"}"
```

响应示例：

```json
{
  "session_id": "api-会话ID",
  "memory_session_id": "底层-memory-UUID",
  "phase": "requirements",
  "project_root": "D:/code/my-project",
  "messages": [
    {"role": "status", "content": "多 Agent 项目根目录：...", "agent": null},
    {"role": "status", "content": "【阶段 1/4】需求确认...", "agent": null}
  ]
}
```

直接进入维护阶段：

```json
{
  "project_root": "./my-project",
  "continue_maintenance": true,
  "memory_session_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### 发送消息

```shell
curl -X POST http://localhost:8000/api/v1/sessions/{session_id}/messages \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"做一个 Todo 应用，支持优先级和截止日期\"}"
```

响应字段：

| 字段 | 说明 |
|------|------|
| `phase` | 当前阶段（requirements / prototype / backend / integration / maintenance） |
| `phase_changed` | 本次消息是否触发了阶段切换 |
| `requirements_form` | 需求澄清表单 JSON；无待填表单时为 `null` |
| `messages` | 状态提示（`role=status`）与 Agent 回复（`role=assistant`） |

阶段切换关键词与 CLI 模式相同，例如：`确认需求`、`确认样品`、`开始对接`、`进入维护`。

### 需求澄清表单

在 `requirements` 阶段，若产品经理认为信息不足，会在回复中带上 `<!-- STATUS: needs_clarification -->`（或文本 `status: needs_clarification`）。此时服务端会**额外调用一次大模型**，把澄清问题转成前端可渲染的 JSON 表单，并写入会话的 `requirements_form` 字段（PostgreSQL `api_session` 表）。

#### 工作流程

```
用户描述需求
    │
    ▼
产品经理 Agent 回复
    │
    ├─ needs_clarification ──► 调用 generate_clarification_form()
    │                              │
    │                              ▼
    │                         生成 JSON 表单 → 写入 requirements_form
    │                              │
    │                              ▼
    │                         SSE: form + done.requirements_form
    │
    ├─ confirmed ───────────► 清空 requirements_form，进入 prototype
    │
    └─ 普通回复（无 status）──► 清空 requirements_form
```

用户提交表单答案或直接发送聊天消息时，服务端在**开始处理该条消息前**立即将 `requirements_form` 置为 `null`（SSE 会先发 `form_clear`），避免处理过程中页面仍显示旧表单。若产品经理本轮仍返回 `needs_clarification`，处理结束后再写入新表单。

#### 表单 JSON 格式

```json
{
  "id": "req-clarify-a1b2c3d4",
  "title": "需求澄清",
  "status": "needs_clarification",
  "fields": [
    {
      "id": "target_user",
      "label": "目标用户与场景",
      "type": "radio",
      "required": true,
      "options": [
        { "value": "personal", "label": "个人使用" },
        { "value": "team", "label": "团队协作" }
      ]
    },
    {
      "id": "extra_notes",
      "label": "其他补充",
      "type": "textarea",
      "required": false,
      "placeholder": "只回答部分问题也可以"
    }
  ]
}
```

支持的 `type`：`radio`、`checkbox`、`select`、`text`、`textarea`。

#### 如何获取表单

| 方式 | 字段 / 事件 |
|------|-------------|
| `GET /api/v1/sessions/{session_id}` | 响应体 `requirements_form` |
| `POST /api/v1/sessions/{id}/messages` | 响应体 `requirements_form` |
| SSE `event: form` | 完整表单 JSON |
| SSE `event: form_clear` | 用户已回复，立即隐藏当前表单 |
| SSE `event: done` | `data.requirements_form` |

产品经理的 Markdown 澄清说明仍通过 `event: assistant` 返回；表单 JSON 供前端单独渲染为控件。

#### 用户未填表单、直接发聊天消息时

澄清表单与聊天**共用** `POST /messages` 通道。用户可以直接输入文字，而不必提交表单。

**用户发送消息时**（无论是否填写表单）：服务端立即清空 `requirements_form`，流式模式下先发 `event: form_clear`。

**Agent 处理完成后**，根据产品经理**本轮**回复更新 `requirements_form`：

| 产品经理本轮回复 | `requirements_form` 处理 |
|------------------|--------------------------|
| 仍含 `needs_clarification` | **更新**为新表单（覆盖旧值） |
| 含 `confirmed` / 阶段进入 `prototype` | **清空**（`null`） |
| 普通回复（无上述 status） | **清空**（`null`） |

用户通过聊天补充的内容会进入产品经理 Agent 的对话记忆；若仍需澄清，下一轮会生成新的表单。需求确认完成后表单不再出现。

#### 前端建议

- 收到 `form` 或 `requirements_form` 非空时，渲染表单；`assistant` 事件展示说明文字
- 用户发送消息后立即隐藏表单（或监听 `form_clear`）；用户可**填表单后通过聊天发送汇总**（将答案格式化为一条消息），或直接**在聊天框补充**——两种方式等价，均走 `POST /messages`
- 收到 `done` 且 `phase` 仍为 `requirements` 时，根据 `requirements_form` 是否为 `null` 决定是否再次展示表单
- `event: assistant` 与 `done.messages` 中 assistant 内容可能相同，展示时避免重复渲染（见 [API.md](./API.md)）

### SSE 流式发送（推荐前端使用）

Agent 执行可能耗时较长，建议用 SSE 实时接收进度，并在收到 `done` 事件时判定完成。

**方式 1：** 请求体设 `stream: true`

```shell
curl -N -X POST http://localhost:8000/api/v1/sessions/{session_id}/messages \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d "{\"message\": \"做一个 Todo 应用\", \"stream\": true}"
```

**方式 2：** 专用流式端点

```shell
curl -N -X POST http://localhost:8000/api/v1/sessions/{session_id}/messages/stream \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"做一个 Todo 应用\"}"
```

**事件类型：**

| 事件 | 说明 |
|------|------|
| `started` | 开始处理用户消息 |
| `status` | 阶段提示、状态变更 |
| `thinking` | Agent 推理过程（若模型支持） |
| `tool_call` | 工具调用开始 |
| `tool_result` | 工具执行结果摘要 |
| `assistant` | Agent 回复内容 |
| `form` | 需求澄清表单 JSON（仅 `requirements` 阶段且 PM 返回 `needs_clarification` 时） |
| `form_clear` | 用户已回复，立即隐藏当前澄清表单 |
| `done` | **执行完成**，含 `phase`、`phase_changed`、`requirements_form`、`messages` |
| `error` | 执行出错 |

**前端示例（fetch + 流式解析）：**

```javascript
const response = await fetch(`/api/v1/sessions/${sessionId}/messages/stream`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ message: userInput }),
});

const reader = response.body.getReader();
const decoder = new TextDecoder();
let buffer = "";

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });

  const parts = buffer.split("\n\n");
  buffer = parts.pop() ?? "";

  for (const part of parts) {
    const eventLine = part.match(/^event: (.+)/m)?.[1];
    const dataLine = part.match(/^data: (.+)/m)?.[1];
    if (!eventLine || !dataLine) continue;

    const data = JSON.parse(dataLine);
    if (eventLine === "done") {
      setLoading(false);  // Agent 执行完成
      setPhase(data.phase);
      setRequirementsForm(data.requirements_form ?? null);
    } else if (eventLine === "error") {
      setLoading(false);
      showError(data.detail);
    } else if (eventLine === "form") {
      setRequirementsForm(data);
    } else if (eventLine === "form_clear") {
      setRequirementsForm(null);
    } else if (eventLine === "assistant") {
      appendAssistant(data.content, data.agent);
    }
  }
}
```

> 注意：SSE 通过 POST 发送时需用 `fetch` 读取流；`EventSource` 仅支持 GET。

### 获取会话列表

```shell
curl http://localhost:8000/api/v1/sessions
```

### 修改工作目录

```shell
curl -X PATCH http://localhost:8000/api/v1/sessions/{session_id} \
  -H "Content-Type: application/json" \
  -d "{\"project_root\": \"./another-project\"}"
```

修改后会重建各 Agent 的工作区绑定；若处于维护阶段，会重新加载新目录下的项目上下文。

### 获取消息列表

```shell
curl http://localhost:8000/api/v1/sessions/{session_id}/messages
```

返回该会话的全部历史消息（含 `user`、`status`、`assistant`），按时间顺序排列。

### 删除会话

```shell
curl -X DELETE http://localhost:8000/api/v1/sessions/{session_id}
```

### 典型调用流程

```
1. POST /api/v1/sessions          → 获取 session_id
2. POST /api/v1/sessions/{id}/messages  {"message": "描述需求..."}
3. POST /api/v1/sessions/{id}/messages  {"message": "确认需求"}
4. ... 按阶段继续发送消息 ...
5. DELETE /api/v1/sessions/{id}   → 结束会话
```

说明：

- `session_id` 是 API 层会话 ID；`memory_session_id` 是底层 memory 会话，可通过创建会话时传入以恢复历史
- 同一会话的消息会串行处理，请勿并发发送
- 会话元数据与消息历史持久化在 PostgreSQL；服务重启后可继续会话，活跃 Runner 会在首次发消息时懒加载恢复

---

## 完整使用示例

```shell
# 新建项目，走完整开发流程
uv run python main.py --multi-agent --project-root ./todo-app

# 阶段 1：描述需求 → 确认需求
# 阶段 2：评审样品 → 确认样品
# 阶段 3：等待后端完成 → 开始对接
# 阶段 4：验证对接 → 进入维护
# 阶段 5：持续迭代修改

# 下次启动，直接进入维护（session-id 从上次终端输出中获取）
uv run python main.py --continue --project-root ./todo-app --session-id 550e8400-e29b-41d4-a716-446655440000
```

---

## 一些说明

### Shell 命令安全

`The command is not run through a system shell` 表示：程序不会把整段命令交给 `cmd.exe`、PowerShell、bash 等 shell 解释执行。

不会这样执行：

```python
subprocess.run(command, shell=True)
```

而是先把命令拆成参数列表，再直接执行程序：

```python
subprocess.run(["curl", "wttr.in/Beijing?format=3"], shell=False)
```

这样更安全，因为 `&&`、管道、重定向等 shell 特性不会生效：

```bash
curl example.com && delete something
curl example.com | other-command
curl example.com > output.txt
```

`execute_shell_command` 只会执行白名单里的程序（如 `curl`），不会让模型借助 shell 语法执行额外命令。

### 各 Agent 职责摘要

| Agent | 工作目录 | 职责 |
|-------|----------|------|
| 产品经理 | `docs/` | 需求分析、PRD，不定义 API 技术细节 |
| 前端 | `frontend/` | 样品（Mock）→ 对接真实 API → 维护 UI |
| 后端 | `backend/` | 设计 openapi.yaml、实现 API 与测试 |
| 协调员 | 无文件权限 | 维护阶段路由，委派上述 Agent |

API 规范由**后端**定义（`backend/docs/openapi.yaml`），前端消费该契约。
