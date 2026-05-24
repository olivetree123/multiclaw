# MultiClaw HTTP API

面向前端项目的 Multi-Agent 开发流程接口文档。

- **Base URL**：`http://localhost:8000`（默认）
- **API 前缀**：`/api/v1`
- **Swagger 文档**：`http://localhost:8000/docs`
- **OpenAPI JSON**：`http://localhost:8000/openapi.json`

## 启动服务

```shell
# 需先启动 PostgreSQL + Qdrant，并配置 docker/.env
uv run python main.py --serve --host 0.0.0.0 --port 8000
```

---

## 概述

MultiClaw API 将多 Agent 开发流程暴露为 REST 接口。一次 **API 会话（session）** 对应一个项目目录，按以下阶段线性推进：

```
requirements → prototype → backend → integration → maintenance
  需求确认       样品开发     后端开发     前端对接       维护
```

### 两种 ID

| 字段 | 说明 |
|------|------|
| `session_id` | API 层会话 ID，**前端主要使用**，创建会话时返回 |
| `memory_session_id` | 底层 Agent 记忆会话 UUID，用于跨重启恢复 Agent 对话记忆 |

### 阶段（phase）

| 值 | 说明 | 负责 Agent |
|----|------|-----------|
| `requirements` | 需求确认 | 产品经理 |
| `prototype` | 样品开发（Mock 前端） | 前端 |
| `backend` | 后端 API 开发 | 后端 |
| `integration` | 前端对接真实 API | 前端 |
| `maintenance` | 维护与迭代 | 协调员（委派各 Agent） |

### 阶段切换关键词

用户消息中包含以下关键词可触发阶段切换（也支持英文）：

| 当前阶段 | 关键词示例 | 下一阶段 |
|----------|-----------|----------|
| `requirements` | `确认需求`、`需求确认`、`开始开发` | `prototype` |
| `prototype` | `确认样品`、`样品确认`、`确认原型` | `backend` |
| `backend` | `开始对接`、`对接接口`、`开始集成` | `integration` |
| `integration` | `进入维护`、`完成开发`、`维护模式` | `maintenance` |

阶段切换后，服务端可能自动执行 bootstrap 任务（如生成初始样品、开发 API），响应中会包含相应消息。

在 `requirements` 阶段，除关键词外，产品经理回复中含 `<!-- STATUS: confirmed -->` 也会触发进入 `prototype`；含 `needs_clarification` 时会生成澄清表单（见下文）。

### 项目目录结构

创建会话时在 `project_root` 下自动创建：

```
{project_root}/
├── docs/       # 产品经理：requirements.md
├── frontend/   # 前端代码
└── backend/    # 后端代码：docs/openapi.yaml
```

---

## 认证

除 `GET /health` 与认证接口外，所有 `/api/v1/*` 接口均需要 **Bearer Token**。

采用 FastAPI 推荐的 **OAuth2 Password Bearer + JWT** 方案。

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `JWT_SECRET` | JWT 签名密钥 | `change-me-in-production` |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | Token 有效期（分钟） | `1440` |

### 注册

```http
POST /api/v1/auth/register
Content-Type: application/json
```

**请求体**

```json
{
  "username": "alice",
  "password": "secret123",
  "email": "alice@example.com"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `username` | string | 是 | 3–64 字符，唯一 |
| `password` | string | 是 | 至少 8 字符 |
| `email` | string | 否 | 邮箱，唯一 |

**响应 201**

```json
{
  "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "username": "alice",
  "email": "alice@example.com",
  "is_active": true,
  "created_at": "2026-05-23T10:30:00+00:00"
}
```

### 登录（获取 Token）

```http
POST /api/v1/auth/token
Content-Type: application/x-www-form-urlencoded
```

**表单字段**（`OAuth2PasswordRequestForm`，Swagger Authorize 按钮使用此接口）

| 字段 | 说明 |
|------|------|
| `username` | 用户名 |
| `password` | 密码 |

**响应 200**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer"
}
```

Swagger UI 中点击 **Authorize**，输入用户名和密码即可自动获取 Token。

### 当前用户

```http
GET /api/v1/auth/me
Authorization: Bearer <access_token>
```

**响应 200**：与注册响应结构相同。

### 受保护接口请求头

```http
Content-Type: application/json
Authorization: Bearer <access_token>
```

会话按用户隔离：用户只能访问自己创建的 `session_id`。

---

## 通用约定

### 请求头

```http
Content-Type: application/json
Authorization: Bearer <access_token>
```

SSE 流式请求建议额外携带：

```http
Accept: text/event-stream
```

### 错误响应

HTTP 4xx/5xx 时，FastAPI 默认返回：

```json
{
  "detail": "错误描述"
}
```

| 状态码 | 含义 |
|--------|------|
| `400` | 请求参数无效，或业务规则不满足（如维护模式但项目产物不存在） |
| `401` | 未登录或 Token 无效/过期 |
| `404` | 会话不存在（或不属于当前用户） |
| `204` | 删除成功，无响应体 |

### 时间格式

所有 `created_at`、`updated_at` 字段为 **ISO 8601 UTC** 时间，例如：`2026-05-23T10:30:00+00:00`。

### 并发限制

同一会话的 `POST /messages` 请求**串行处理**（服务端加锁）。前端应避免对同一会话并发发送多条消息。

### 会话存储

API 会话元数据与消息历史持久化在 **PostgreSQL**：

| 表 | 内容 |
|----|------|
| `api_session` | 会话 ID、用户、项目路径、阶段、`memory_session_id`、`requirements_form` 等 |
| `api_session_message` | API 层展示用消息（user / status / assistant） |

服务重启后：

- 会话列表、详情、消息历史可从数据库恢复
- 首次继续对话时会懒加载 `MultiAgentRunner`（恢复阶段与 Agent 记忆）
- 内存中仅缓存活跃会话的 Runner 实例

项目文件仍保留在 `project_root` 磁盘上。

---

## 接口列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查（无需认证） |
| `POST` | `/api/v1/auth/register` | 注册 |
| `POST` | `/api/v1/auth/token` | 登录，获取 Token（OAuth2） |
| `GET` | `/api/v1/auth/me` | 当前用户信息 |
| `GET` | `/api/v1/sessions` | 获取会话列表 |
| `POST` | `/api/v1/sessions` | 创建会话 |
| `GET` | `/api/v1/sessions/{session_id}` | 获取会话详情 |
| `PATCH` | `/api/v1/sessions/{session_id}` | 修改工作目录 |
| `GET` | `/api/v1/sessions/{session_id}/messages` | 获取消息历史 |
| `POST` | `/api/v1/sessions/{session_id}/messages` | 发送消息（可选 SSE） |
| `POST` | `/api/v1/sessions/{session_id}/messages/stream` | 发送消息（SSE 流式） |
| `DELETE` | `/api/v1/sessions/{session_id}` | 删除会话 |

---

## 接口详情

### 健康检查

```http
GET /health
```

**响应 200**

```json
{
  "status": "ok"
}
```

---

### 创建会话

```http
POST /api/v1/sessions
```

**请求体**

```json
{
  "project_root": "./my-project",
  "continue_maintenance": false,
  "memory_session_id": null
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `project_root` | string | 是 | 项目根目录绝对或相对路径 |
| `continue_maintenance` | boolean | 否 | 默认 `false`；为 `true` 时跳过开发流程，直接进入 `maintenance` 阶段 |
| `memory_session_id` | string \| null | 否 | 复用已有 memory 会话 UUID |

**响应 200**

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "memory_session_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "phase": "requirements",
  "project_root": "D:/code/my-project",
  "messages": [
    {
      "role": "status",
      "content": "多 Agent 项目根目录：D:/code/my-project",
      "agent": null
    },
    {
      "role": "status",
      "content": "【阶段 1/4】需求确认：与产品经理对话。输入「确认需求」进入样品开发。",
      "agent": null
    }
  ]
}
```

**响应 400**：`continue_maintenance=true` 但项目目录缺少必要产物。

---

### 获取会话列表

```http
GET /api/v1/sessions
```

**响应 200**

```json
{
  "sessions": [
    {
      "session_id": "550e8400-e29b-41d4-a716-446655440000",
      "memory_session_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
      "phase": "prototype",
      "project_root": "D:/code/my-project",
      "continue_maintenance": false,
      "message_count": 12,
      "created_at": "2026-05-23T10:00:00+00:00",
      "updated_at": "2026-05-23T10:15:00+00:00"
    }
  ],
  "total": 1
}
```

---

### 获取会话详情

```http
GET /api/v1/sessions/{session_id}
```

**响应 200**

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "memory_session_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "phase": "requirements",
  "project_root": "D:/code/my-project",
  "continue_maintenance": false,
  "message_count": 4,
  "requirements_form": {
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
      }
    ]
  },
  "created_at": "2026-05-23T10:00:00+00:00",
  "updated_at": "2026-05-23T10:15:00+00:00"
}
```

| 字段 | 说明 |
|------|------|
| `requirements_form` | 待填写的需求澄清表单；无待填表单时为 `null` |

**响应 404**：会话不存在。

---

### 修改工作目录

```http
PATCH /api/v1/sessions/{session_id}
```

**请求体**

```json
{
  "project_root": "./another-project"
}
```

**响应 200**

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "memory_session_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "phase": "maintenance",
  "project_root": "D:/code/another-project",
  "continue_maintenance": false,
  "message_count": 13,
  "created_at": "2026-05-23T10:00:00+00:00",
  "updated_at": "2026-05-23T10:20:00+00:00"
}
```

修改后会重建各 Agent 的工作区绑定；维护阶段会重新加载新目录下的项目上下文。消息历史中会追加一条 `status` 记录。

---

### 获取消息历史

```http
GET /api/v1/sessions/{session_id}/messages
```

**响应 200**

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "total": 3,
  "messages": [
    {
      "id": "msg-uuid-1",
      "role": "user",
      "content": "做一个 Todo 应用",
      "agent": null,
      "phase": "requirements",
      "created_at": "2026-05-23T10:01:00+00:00"
    },
    {
      "id": "msg-uuid-2",
      "role": "assistant",
      "content": "好的，我来分析需求...",
      "agent": "产品经理",
      "phase": "requirements",
      "created_at": "2026-05-23T10:01:30+00:00"
    }
  ]
}
```

**消息 role 说明**

| role | 说明 |
|------|------|
| `user` | 用户发送的消息 |
| `status` | 系统状态提示（阶段切换、bootstrap 等） |
| `assistant` | Agent 回复 |

---

### 发送消息（同步）

```http
POST /api/v1/sessions/{session_id}/messages
```

**请求体**

```json
{
  "message": "做一个 Todo 应用，支持优先级和截止日期",
  "stream": false
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message` | string | 是 | 用户消息，最少 1 个字符 |
| `stream` | boolean | 否 | 默认 `false`；为 `true` 时返回 SSE 流（见下文） |

**响应 200**（`stream: false`）

```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "memory_session_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "phase": "requirements",
  "phase_changed": false,
  "requirements_form": {
    "id": "req-clarify-a1b2c3d4",
    "title": "需求澄清",
    "status": "needs_clarification",
    "fields": []
  },
  "messages": [
    {
      "role": "assistant",
      "content": "好的，我来分析需求...",
      "agent": "产品经理"
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `phase` | 处理完成后的当前阶段 |
| `phase_changed` | 本次消息是否触发了阶段切换 |
| `requirements_form` | 需求澄清表单 JSON；无待填表单时为 `null` |
| `messages` | 本次产生的状态提示与 Agent 回复（不含用户消息本身） |

> **注意**：同步模式下 HTTP 响应返回即表示 Agent 执行完成。长任务可能导致请求超时，**生产环境推荐使用 SSE**。

---

### 发送消息（SSE 流式）

两种方式等价，任选其一：

```http
POST /api/v1/sessions/{session_id}/messages
Content-Type: application/json

{"message": "...", "stream": true}
```

```http
POST /api/v1/sessions/{session_id}/messages/stream
Content-Type: application/json

{"message": "..."}
```

**响应**：`Content-Type: text/event-stream`

每个 SSE 事件格式：

```
event: {事件名}
data: {JSON 对象}

```

#### SSE 事件类型

| 事件 | data 字段 | 说明 |
|------|-----------|------|
| `started` | `message`, `phase` | 开始处理用户消息 |
| `status` | `content` | 状态/阶段提示 |
| `thinking` | `content`, `agent` | Agent 推理内容（模型支持时） |
| `tool_call` | `tool_name`, `arguments`, `agent` | 开始调用工具 |
| `tool_result` | `tool_name`, `content`, `agent` | 工具执行结果摘要 |
| `assistant` | `content`, `agent` | Agent 回复片段 |
| `form` | 见「需求澄清表单」 | 需求澄清表单 JSON（`requirements` 阶段且 PM 返回 `needs_clarification` 时） |
| `form_clear` | — | 用户已回复，立即隐藏当前澄清表单 |
| `done` | 见下方 | **执行完成** |
| `error` | `detail` 或 `title` + `detail` | 执行出错 |

**`done` 事件 data 结构**

```json
{
  "memory_session_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
  "phase": "prototype",
  "phase_changed": true,
  "requirements_form": null,
  "messages": [
    {
      "role": "assistant",
      "content": "...",
      "agent": "产品经理"
    },
    {
      "role": "status",
      "content": "需求已确认，进入样品开发阶段。",
      "agent": null
    }
  ]
}
```

**`form` 事件 data 结构**（与 `requirements_form` 字段相同，见「需求澄清表单」）

```
event: form
data: {"id":"req-clarify-a1b2c3d4","title":"需求澄清","status":"needs_clarification","fields":[...]}

```

**前端判定规则**

- 收到 `event: form` → 渲染或更新澄清表单
- 收到 `event: form_clear` → 立即隐藏澄清表单
- 收到 `event: assistant` → 展示 Agent 说明文字（勿与 `done.messages` 重复渲染）
- 收到 `event: done` → Agent 执行完成，关闭 loading，更新 `phase` 与 `requirements_form`
- 收到 `event: error` → 出错，关闭 loading，展示错误
- 连接断开且无 `done` → 按失败处理

> **注意**：POST 流式响应不能使用浏览器 `EventSource`（仅支持 GET），需使用 `fetch` + `ReadableStream` 解析。

---

### 需求澄清表单

在 `requirements` 阶段，若产品经理回复中含 `<!-- STATUS: needs_clarification -->`（或 `status: needs_clarification`），服务端会额外调用大模型，将澄清问题转为 JSON 表单，写入 `api_session.requirements_form` 并随接口/SSE 返回。

#### 工作流程

```
用户 POST /messages
    │
    ├─ 有待填表单 → 立即清空 requirements_form（SSE: form_clear）
    │
    ▼
产品经理 Agent 回复
    │
    ├─ needs_clarification → 生成 requirements_form → SSE: form + done.requirements_form
    ├─ confirmed           → requirements_form = null，phase → prototype
    └─ 普通回复（无 status）→ requirements_form = null
```

澄清表单与聊天共用 `POST /messages`。用户可直接发文字，也可将表单答案格式化为一条消息后发送。

#### 表单 JSON Schema

`form` 事件与 `requirements_form` 字段使用相同结构：

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
      "id": "platforms",
      "label": "支持平台",
      "type": "checkbox",
      "required": false,
      "options": [
        { "value": "web", "label": "Web" },
        { "value": "mobile", "label": "移动端" }
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

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 表单唯一 ID，如 `req-clarify-a1b2c3d4` |
| `title` | string | 表单标题 |
| `status` | string | 固定为 `needs_clarification` |
| `fields` | array | 表单项列表 |
| `fields[].id` | string | 字段 ID（snake_case） |
| `fields[].label` | string | 显示标签 |
| `fields[].type` | string | `radio` / `checkbox` / `select` / `text` / `textarea` |
| `fields[].required` | boolean | 是否必填 |
| `fields[].options` | array | 选项（`radio`/`checkbox`/`select` 必填） |
| `fields[].placeholder` | string | 占位符（`text`/`textarea` 可选） |

#### 获取与更新 `requirements_form`

| 时机 | 来源 |
|------|------|
| 页面加载 / 刷新 | `GET /api/v1/sessions/{session_id}` → `requirements_form` |
| 同步发消息 | `POST /messages` 响应 → `requirements_form` |
| SSE 流式 | `event: form`（完整 JSON）或 `event: done` → `requirements_form` |

#### 用户未填表单、直接发聊天消息时

澄清表单与聊天共用 `POST /messages`。用户可直接发文字，也可将表单答案格式化为一条消息后发送。

**用户发送消息时**（无论是否填写表单）：服务端立即清空 `requirements_form`；流式模式下先发 `event: form_clear`。

**Agent 处理完成后**，根据产品经理**本轮**回复更新 `requirements_form`：

| 产品经理本轮回复 | `requirements_form` |
|------------------|----------------------|
| 仍含 `needs_clarification` | **更新**为新表单（覆盖旧值） |
| 含 `confirmed` / 进入 `prototype` | **清空**（`null`） |
| 普通回复（无上述 status） | **清空**（`null`） |

#### 提交表单答案

当前无独立「提交表单」接口。前端将用户填写结果格式化为一条用户消息，调用 `POST /messages` 即可，例如：

```json
{
  "message": "【需求澄清回复】\n- 目标用户：个人使用\n- 支持平台：Web\n- 其他补充：需要暗黑模式"
}
```

---

### 删除会话

```http
DELETE /api/v1/sessions/{session_id}
```

**响应 204**：无响应体。

**响应 404**：会话不存在。

删除会释放服务端资源，**不会删除** `project_root` 下的项目文件。

---

## 前端对接指南

### 推荐流程

```
1. POST /api/v1/auth/token                    → 获取 access_token
2. POST /api/v1/sessions                     → 保存 session_id
3. 渲染 messages 中的初始 status 提示
4. GET  /api/v1/sessions/{id}                → 恢复 requirements_form（requirements 阶段）
5. POST /api/v1/sessions/{id}/messages/stream → SSE 流式对话
6. GET  /api/v1/sessions/{id}/messages      → 页面刷新时恢复历史（可选）
7. DELETE /api/v1/sessions/{id}             → 用户退出时释放
```

### 继续已有项目

**开发完成后进入维护：**

```json
POST /api/v1/sessions
{
  "project_root": "./my-project",
  "continue_maintenance": true,
  "memory_session_id": "上次保存的 memory_session_id"
}
```

### TypeScript 类型参考

```typescript
type Phase =
  | "requirements"
  | "prototype"
  | "backend"
  | "integration"
  | "maintenance";

type FormFieldType = "radio" | "checkbox" | "select" | "text" | "textarea";

interface FormOption {
  value: string;
  label: string;
}

interface FormField {
  id: string;
  label: string;
  type: FormFieldType;
  required: boolean;
  options?: FormOption[];
  placeholder?: string;
}

interface RequirementsForm {
  id: string;
  title: string;
  status: "needs_clarification";
  fields: FormField[];
}

interface AgentReply {
  role: "user" | "status" | "assistant";
  content: string;
  agent: string | null;
}

interface SessionSummary {
  session_id: string;
  memory_session_id: string;
  phase: Phase;
  project_root: string;
  continue_maintenance: boolean;
  message_count: number;
  created_at: string;
  updated_at: string;
}

interface SessionInfo extends SessionSummary {
  requirements_form: RequirementsForm | null;
}

interface SessionMessage {
  id: string;
  role: "user" | "status" | "assistant";
  content: string;
  agent: string | null;
  phase: Phase | null;
  created_at: string;
}

interface SubmitMessageResponse {
  session_id: string;
  memory_session_id: string;
  phase: Phase;
  phase_changed: boolean;
  requirements_form: RequirementsForm | null;
  messages: AgentReply[];
}

type SSEEventType =
  | "started"
  | "status"
  | "thinking"
  | "tool_call"
  | "tool_result"
  | "assistant"
  | "form"
  | "form_clear"
  | "done"
  | "error";
```

### SSE 解析示例

```typescript
async function sendMessageStream(
  sessionId: string,
  message: string,
  accessToken: string,
  onEvent: (event: SSEEventType, data: Record<string, unknown>) => void,
): Promise<void> {
  const response = await fetch(
    `/api/v1/sessions/${sessionId}/messages/stream`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${accessToken}`,
      },
      body: JSON.stringify({ message }),
    },
  );

  if (!response.ok || !response.body) {
    throw new Error(`HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";

    for (const chunk of chunks) {
      const event = chunk.match(/^event: (.+)/m)?.[1] as SSEEventType | undefined;
      const dataStr = chunk.match(/^data: (.+)/m)?.[1];
      if (event && dataStr) {
        onEvent(event, JSON.parse(dataStr));
      }
    }
  }
}

// 使用
let loading = true;
await sendMessageStream(sessionId, userInput, accessToken, (event, data) => {
  switch (event) {
    case "assistant":
      appendMessage(data.content as string, data.agent as string);
      break;
    case "form":
      setRequirementsForm(data as unknown as RequirementsForm);
      break;
    case "form_clear":
      setRequirementsForm(null);
      break;
    case "tool_call":
      showToolCall(data.tool_name as string);
      break;
    case "done":
      loading = false;
      setPhase(data.phase as Phase);
      setRequirementsForm(
        (data.requirements_form as RequirementsForm | null) ?? null,
      );
      break;
    case "error":
      loading = false;
      showError(data.detail as string);
      break;
  }
});
```

### UI 建议

| 阶段 | 建议 UI |
|------|---------|
| `requirements` | 聊天气泡 + **需求澄清表单**（`requirements_form` 非空时展示）；用户可填表单或直接发消息；提供「确认需求」快捷按钮 |
| `prototype` | 聊天气泡 + 预览链接/截图区域，提供「确认样品」按钮 |
| `backend` | 聊天气泡 + 日志/文件树，提供「开始对接」按钮 |
| `integration` | 聊天气泡 + 预览，提供「进入维护」按钮 |
| `maintenance` | 自由对话，展示协调员委派进度（`tool_call` 事件） |

**需求澄清表单 UI 建议**

- 进入会话或收到 `event: form` 时渲染表单；`requirements_form === null` 时隐藏
- 收到 `event: form_clear` 或用户点击发送后立即隐藏表单
- 表单提交：将答案格式化为一条消息，调用 `POST /messages`（与聊天输入框共用）
- Agent 处理完成后，根据 `done.requirements_form` 决定是否再次展示表单

### curl 调试示例

```shell
# 登录获取 Token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=alice&password=secret123" | jq -r .access_token)

# 创建会话
SESSION=$(curl -s -X POST http://localhost:8000/api/v1/sessions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${TOKEN}" \
  -d '{"project_root": "./my-project"}' | jq -r .session_id)

# SSE 流式发送
curl -N -X POST "http://localhost:8000/api/v1/sessions/${SESSION}/messages/stream" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${TOKEN}" \
  -d '{"message": "做一个 Todo 应用"}'

# 查看会话详情（含 requirements_form）
curl -H "Authorization: Bearer ${TOKEN}" \
  "http://localhost:8000/api/v1/sessions/${SESSION}"

# 查看历史
curl -H "Authorization: Bearer ${TOKEN}" \
  "http://localhost:8000/api/v1/sessions/${SESSION}/messages"

# 删除会话
curl -X DELETE -H "Authorization: Bearer ${TOKEN}" \
  "http://localhost:8000/api/v1/sessions/${SESSION}"
```

---

## 附录：Agent 职责

| Agent | 工作目录 | 职责 |
|-------|----------|------|
| 产品经理 | `docs/` | 需求分析、PRD |
| 前端 | `frontend/` | Mock 样品 → API 对接 → UI 维护 |
| 后端 | `backend/` | API 设计（`docs/openapi.yaml`）、实现与测试 |
| 协调员 | — | 维护阶段任务路由与委派 |

API 规范由后端定义（`backend/docs/openapi.yaml`），前端消费该契约。
