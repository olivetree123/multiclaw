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
- **进入下一阶段**：输入 `确认需求`、`需求确认` 或 `开始开发`
- **退出**：输入 `exit`

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
