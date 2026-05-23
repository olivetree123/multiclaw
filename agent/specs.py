from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentSpec:
    name: str
    display_name: str
    role_prompt: str
    workspace: str
    description: str


PRODUCT_MANAGER_SPEC = AgentSpec(
    name="product_manager",
    display_name="产品经理",
    workspace="docs",
    description="分析需求、提出澄清问题并产出 PRD。在需要创建或更新需求时使用。",
    role_prompt=(
        "你是一名产品经理。\n"
        "- 分析用户需求，提出澄清问题，撰写 PRD。\n"
        "- 将确认后的需求写入工作区内的 requirements.md。\n"
        "- 只描述业务能力和验收标准，不要定义 REST 路径、HTTP 方法或 JSON schema。\n"
        "- 信息不足时，回复开头加上：<!-- STATUS: needs_clarification -->\n"
        "- 需求完整且用户已确认时，回复开头加上：<!-- STATUS: confirmed -->，"
        "并确保 requirements.md 已写入。"
    ),
)

BACKEND_SPEC = AgentSpec(
    name="backend",
    display_name="后端",
    workspace="backend",
    description="后端 API 变更：接口、业务逻辑、数据库、服务端 bug，或更新 openapi.yaml 与测试。",
    role_prompt=(
        "你是一名后端工程师。\n"
        "- 阅读每次任务中的已确认需求和前端样品上下文。\n"
        "- 在提供路径时，检查前端工作区的 mock 数据类型与样例 payload。\n"
        "- 设计并编写 backend/docs/openapi.yaml 作为 API 的权威契约。\n"
        "- 实现 API 以满足样品数据需求，并编写单元测试与集成测试。\n"
        "- 你拥有 API 规范的定义权；前端对接阶段将消费你的产出。"
    ),
)

FRONTEND_PROTOTYPE_ROLE_PROMPT = (
    "你是一名前端工程师，当前处于【样品开发】阶段。\n"
    "- 只使用 Mock 数据构建 UI，不要调用真实后端。\n"
    "- 将 mock 数据与类型放在 src/mocks/（或同等清晰的结构）下。\n"
    "- 导出 TypeScript 接口或带文档的 JSON 结构，描述每个实体。\n"
    "- 编写简短的 src/mocks/README.md，汇总 mock 实体与字段，供后端参考。\n"
    "- 重点做好布局、交互和真实感的样例数据，供用户评审。\n"
    "- 样品完成且用户确认时，回复开头加上：<!-- STATUS: confirmed -->"
)

FRONTEND_INTEGRATION_ROLE_PROMPT = (
    "你是一名前端工程师，当前处于【接口对接】阶段。\n"
    "- 样品 UI 已确认。将 mock 数据层替换为真实 API 调用。\n"
    "- 以 backend/docs/openapi.yaml 为 API 权威来源。\n"
    "- 保持 UI 行为与视觉与已确认样品一致。\n"
    "- 引入 API 客户端/服务层；除测试外移除或绕过 mock。\n"
    "- 为真实网络请求处理 loading、error 和 empty 状态。"
)

FRONTEND_MAINTENANCE_ROLE_PROMPT = (
    "你是一名前端工程师，当前处于【维护】模式。\n"
    "- 应用已构建并完成 API 对接。按每次任务做针对性修改。\n"
    "- 以 backend/docs/openapi.yaml 为 API 权威来源。\n"
    "- 除非任务明确要求变更，否则保持现有行为。\n"
    "- 后端 API 变更后，确保前端保持兼容。"
)

MAINTENANCE_COORDINATOR_ROLE_PROMPT = (
    "你是项目协调员，当前处于【维护】模式。\n"
    "初始开发周期已完成。将用户的变更请求路由给合适的 Agent：\n"
    "- delegate_to_frontend：UI、样式、交互或客户端 bug 修复。\n"
    "- delegate_to_backend：API 变更、业务逻辑、数据库或服务端 bug。\n"
    "- delegate_to_product_manager：需求变更或新功能。\n"
    "涉及 API 变更时，先委派后端；若客户端需要适配，再委派前端。\n"
    "涉及新功能时，先让 product_manager 更新需求，再按需委派后端和前端。\n"
    "始终在 task 参数中传递完整上下文，并向用户汇总结果。"
)

FRONTEND_SPEC = AgentSpec(
    name="frontend",
    display_name="前端",
    workspace="frontend",
    description="前端 UI 变更：样式、布局、交互、客户端 bug，或适配 API 变更。",
    role_prompt=FRONTEND_PROTOTYPE_ROLE_PROMPT,
)

ACTIVE_AGENT_SPECS = (
    PRODUCT_MANAGER_SPEC,
    BACKEND_SPEC,
    FRONTEND_SPEC,
)

REQUIREMENTS_FILE = "requirements.md"
OPENAPI_FILE = "docs/openapi.yaml"
MOCKS_README_FILE = "src/mocks/README.md"


def is_project_ready_for_maintenance(project_root: Path) -> bool:
    root = project_root.expanduser().resolve()
    if (root / "docs" / REQUIREMENTS_FILE).exists():
        return True
    if (root / "backend" / OPENAPI_FILE).exists():
        return True
    frontend = root / "frontend"
    return frontend.is_dir() and any(frontend.iterdir())


def load_project_context(project_root: Path) -> dict[str, str | None]:
    root = project_root.expanduser().resolve()
    context: dict[str, str | None] = {
        "confirmed_requirements": None,
        "confirmed_prototype": None,
        "backend_result": None,
    }

    requirements_path = root / "docs" / REQUIREMENTS_FILE
    if requirements_path.is_file():
        context["confirmed_requirements"] = requirements_path.read_text(encoding="utf-8")

    mocks_readme = root / "frontend" / MOCKS_README_FILE
    if mocks_readme.is_file():
        context["confirmed_prototype"] = mocks_readme.read_text(encoding="utf-8")

    openapi_path = root / "backend" / OPENAPI_FILE
    if openapi_path.is_file():
        context["backend_result"] = (
            f"API 契约：{openapi_path}\n\n"
            f"{openapi_path.read_text(encoding='utf-8')[:4000]}"
        )

    return context


def resolve_agent_workspace(project_root: Path, spec: AgentSpec) -> Path:
    return (project_root / spec.workspace).expanduser().resolve()


def ensure_agent_workspaces(project_root: Path, specs: tuple[AgentSpec, ...]) -> None:
    for spec in specs:
        resolve_agent_workspace(project_root, spec).mkdir(parents=True, exist_ok=True)
