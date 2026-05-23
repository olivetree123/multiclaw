from __future__ import annotations

import asyncio
import json
import uuid
from enum import Enum
from pathlib import Path

from .delegate_tools import build_delegate_tool_schemas, parse_delegate_tool_name
from .runner import AgentRunner
from .specs import (
    ACTIVE_AGENT_SPECS,
    BACKEND_SPEC,
    FRONTEND_INTEGRATION_ROLE_PROMPT,
    FRONTEND_MAINTENANCE_ROLE_PROMPT,
    FRONTEND_PROTOTYPE_ROLE_PROMPT,
    FRONTEND_SPEC,
    MAINTENANCE_COORDINATOR_ROLE_PROMPT,
    PRODUCT_MANAGER_SPEC,
    AgentSpec,
    ensure_agent_workspaces,
    is_project_ready_for_maintenance,
    load_project_context,
    resolve_agent_workspace,
)


class Phase(str, Enum):
    REQUIREMENTS = "requirements"
    PROTOTYPE = "prototype"
    BACKEND = "backend"
    INTEGRATION = "integration"
    MAINTENANCE = "maintenance"


REQUIREMENTS_CONFIRM_PHRASES = (
    "确认需求",
    "需求确认",
    "开始开发",
    "confirm requirements",
    "start development",
)

PROTOTYPE_CONFIRM_PHRASES = (
    "确认样品",
    "样品确认",
    "确认原型",
    "原型确认",
    "confirm prototype",
)

INTEGRATION_START_PHRASES = (
    "开始对接",
    "对接接口",
    "开始集成",
    "start integration",
)

MAINTENANCE_START_PHRASES = (
    "完成开发",
    "进入维护",
    "维护模式",
    "finish development",
    "enter maintenance",
)


class MultiAgentRunner:
    """协调多个 AgentRunner：线性开发流程 + 维护阶段。"""

    def __init__(
        self,
        *,
        project_root: Path,
        session_id: str | None = None,
        agent_specs: tuple[AgentSpec, ...] = ACTIVE_AGENT_SPECS,
        continue_maintenance: bool = False,
    ) -> None:
        self.project_root = project_root.expanduser().resolve()
        self.agent_specs = agent_specs
        self.spec_by_name = {spec.name: spec for spec in agent_specs}
        self.continue_maintenance = continue_maintenance
        self.phase = Phase.MAINTENANCE if continue_maintenance else Phase.REQUIREMENTS
        self.confirmed_requirements: str | None = None
        self.confirmed_prototype: str | None = None
        self.backend_result: str | None = None
        self._sub_agents: dict[str, AgentRunner] = {}
        self._coordinator: AgentRunner | None = None
        self._anchor = AgentRunner(
            workspace=None,
            session_id=session_id,
            label="System",
            start_worker=True,
        )
        self._root_session_id: str | None = None

    async def run(self) -> None:
        ensure_agent_workspaces(self.project_root, self.agent_specs)
        self.console.print_status(f"多 Agent 项目根目录：{self.project_root}")

        try:
            if self.continue_maintenance:
                await self._start_maintenance_from_continue()
                return

            await self._requirements_phase()
            if self.phase is not Phase.PROTOTYPE:
                return
            await self._prototype_phase()
            if self.phase is not Phase.BACKEND:
                return
            await self._backend_phase()
            if self.phase is not Phase.INTEGRATION:
                return
            await self._integration_phase()
            if self.phase is not Phase.MAINTENANCE:
                return
            await self._maintenance_phase()
        finally:
            await self._close_agents()

    async def _start_maintenance_from_continue(self) -> None:
        if not is_project_ready_for_maintenance(self.project_root):
            self.console.print_error(
                "无法进入维护模式",
                RuntimeError(
                    "未找到项目产物。请先完成开发流程，"
                    "或确保存在 docs/requirements.md、backend/docs/openapi.yaml 或 frontend/。"
                ),
            )
            return

        self._apply_project_context(load_project_context(self.project_root))
        self.console.print_status("检测到已有项目产物，直接进入维护阶段。")
        await self._maintenance_phase()

    def _apply_project_context(self, context: dict[str, str | None]) -> None:
        self.confirmed_requirements = context.get("confirmed_requirements")
        self.confirmed_prototype = context.get("confirmed_prototype")
        self.backend_result = context.get("backend_result")

    async def _requirements_phase(self) -> None:
        self.console.print_status(
            "【阶段 1/4】需求确认：与产品经理对话。"
            "输入「确认需求」进入样品开发；输入 exit 退出。"
        )
        product_manager = await self._get_sub_agent(PRODUCT_MANAGER_SPEC)

        while self.phase is Phase.REQUIREMENTS:
            input_text = await self._read_user_input()
            if input_text is None:
                return

            self.console.print_user_message(input_text)
            result = await product_manager.run_task(input_text)

            if _should_confirm_requirements(input_text, result):
                self.confirmed_requirements = result
                self.phase = Phase.PROTOTYPE
                self.console.print_status("需求已确认，进入样品开发阶段。")
                return

    async def _prototype_phase(self) -> None:
        self.console.print_status(
            "【阶段 2/4】样品开发：前端 Agent 将用 Mock 数据搭建页面。"
            "可多次提出修改；输入「确认样品」进入后端开发；输入 exit 退出。"
        )
        frontend = await self._get_sub_agent(
            FRONTEND_SPEC,
            role_key="prototype",
            role_prompt=FRONTEND_PROTOTYPE_ROLE_PROMPT,
        )
        initial_task = _enrich_task(
            task=(
                "根据已确认的需求，搭建可交互的前端样品页面，使用 Mock 数据，"
                "不要调用真实后端。请建立清晰的 mock 类型与样例数据。"
            ),
            confirmed_requirements=self.confirmed_requirements,
        )
        self.console.print_status("正在生成初始样品...")
        await frontend.run_task(initial_task)

        while self.phase is Phase.PROTOTYPE:
            input_text = await self._read_user_input()
            if input_text is None:
                return

            self.console.print_user_message(input_text)
            result = await frontend.run_task(input_text)

            if _should_confirm_prototype(input_text, result):
                self.confirmed_prototype = result
                self.phase = Phase.BACKEND
                self.console.print_status("样品已确认，进入后端接口开发阶段。")
                return

    async def _backend_phase(self) -> None:
        self.console.print_status(
            "【阶段 3/4】后端开发：根据需求与样品 Mock 数据结构实现 API。"
            "完成后输入「开始对接」进入前端集成；输入 exit 退出。"
        )
        backend = await self._get_sub_agent(BACKEND_SPEC)
        initial_task = _enrich_backend_task(
            confirmed_requirements=self.confirmed_requirements,
            confirmed_prototype=self.confirmed_prototype,
            frontend_workspace=str(resolve_agent_workspace(self.project_root, FRONTEND_SPEC)),
        )
        self.console.print_status("正在开发后端接口...")
        self.backend_result = await backend.run_task(initial_task)

        while self.phase is Phase.BACKEND:
            input_text = await self._read_user_input()
            if input_text is None:
                return

            if _should_start_integration(input_text):
                self.phase = Phase.INTEGRATION
                self.console.print_status("进入前端接口对接阶段。")
                return

            self.console.print_user_message(input_text)
            self.backend_result = await backend.run_task(
                _enrich_backend_task(
                    confirmed_requirements=self.confirmed_requirements,
                    confirmed_prototype=self.confirmed_prototype,
                    frontend_workspace=str(resolve_agent_workspace(self.project_root, FRONTEND_SPEC)),
                    task=input_text,
                )
            )

    async def _integration_phase(self) -> None:
        self.console.print_status(
            "【阶段 4/4】前端对接：将 Mock 数据替换为真实 API 调用。"
            "可继续提出修改；输入「进入维护」进入维护阶段；输入 exit 退出。"
        )
        frontend = await self._get_sub_agent(
            FRONTEND_SPEC,
            role_key="integration",
            role_prompt=FRONTEND_INTEGRATION_ROLE_PROMPT,
        )
        initial_task = _enrich_integration_task(
            confirmed_requirements=self.confirmed_requirements,
            confirmed_prototype=self.confirmed_prototype,
            backend_result=self.backend_result,
            backend_workspace=str(resolve_agent_workspace(self.project_root, BACKEND_SPEC)),
        )
        self.console.print_status("正在对接前端与后端接口...")
        await frontend.run_task(initial_task)

        while self.phase is Phase.INTEGRATION:
            input_text = await self._read_user_input()
            if input_text is None:
                return

            if _should_start_maintenance(input_text):
                self.phase = Phase.MAINTENANCE
                self.console.print_status("开发流程完成，进入维护阶段。")
                return

            self.console.print_user_message(input_text)
            await frontend.run_task(
                _enrich_integration_task(
                    confirmed_requirements=self.confirmed_requirements,
                    confirmed_prototype=self.confirmed_prototype,
                    backend_result=self.backend_result,
                    backend_workspace=str(resolve_agent_workspace(self.project_root, BACKEND_SPEC)),
                    task=input_text,
                )
            )

    async def _maintenance_phase(self) -> None:
        self.console.print_status(
            "【维护阶段】描述需要修改的内容，Coordinator 会委派给合适的 Agent。"
            "输入 exit 退出。"
        )
        coordinator = await self._get_coordinator()
        delegate_schemas = build_delegate_tool_schemas(self.agent_specs)

        await coordinator.run(
            extra_tool_schemas=delegate_schemas,
            tool_handler=self._handle_maintenance_delegate,
        )

    async def _handle_maintenance_delegate(
        self,
        tool_name: str,
        tool_arguments: str,
    ) -> dict[str, str]:
        agent_name = parse_delegate_tool_name(tool_name)
        if agent_name is None:
            raise ValueError(f"Unknown delegate tool: {tool_name}")

        spec = self.spec_by_name.get(agent_name)
        if spec is None:
            raise ValueError(f"Unknown agent: {agent_name}")

        arguments = json.loads(tool_arguments) if isinstance(tool_arguments, str) else tool_arguments
        task = arguments.get("task", "")
        if not task.strip():
            raise ValueError("Delegate task cannot be empty.")

        enriched_task = _enrich_maintenance_task(
            task=task,
            confirmed_requirements=self.confirmed_requirements,
            confirmed_prototype=self.confirmed_prototype,
            backend_result=self.backend_result,
            frontend_workspace=str(resolve_agent_workspace(self.project_root, FRONTEND_SPEC)),
            backend_workspace=str(resolve_agent_workspace(self.project_root, BACKEND_SPEC)),
        )

        if agent_name == FRONTEND_SPEC.name:
            sub_agent = await self._get_sub_agent(
                FRONTEND_SPEC,
                role_key="maintenance",
                role_prompt=FRONTEND_MAINTENANCE_ROLE_PROMPT,
            )
        else:
            sub_agent = await self._get_sub_agent(spec)

        self.console.print_status(f"正在委派给 {spec.display_name}...")
        result = await sub_agent.run_task(enriched_task)

        if agent_name == PRODUCT_MANAGER_SPEC.name:
            if _response_is_confirmed(result):
                self.confirmed_requirements = result
                self.console.print_status("需求文档已更新。")
            else:
                requirements_path = resolve_agent_workspace(self.project_root, PRODUCT_MANAGER_SPEC)
                requirements_file = requirements_path / "requirements.md"
                if requirements_file.is_file():
                    self.confirmed_requirements = requirements_file.read_text(encoding="utf-8")

        if agent_name == BACKEND_SPEC.name:
            self.backend_result = result

        return {
            "agent": spec.display_name,
            "workspace": str(resolve_agent_workspace(self.project_root, spec)),
            "result": result,
        }

    async def _get_coordinator(self) -> AgentRunner:
        if self._coordinator is None:
            root_session_id = await self._ensure_root_session_id()
            coordinator_session_id = str(
                uuid.uuid5(uuid.UUID(root_session_id), "multiclaw-coordinator-maintenance")
            )
            self._coordinator = AgentRunner(
                workspace=None,
                session_id=coordinator_session_id,
                role_prompt=MAINTENANCE_COORDINATOR_ROLE_PROMPT,
                label="协调员",
                console=self.console,
                start_worker=False,
            )
        return self._coordinator

    async def _read_user_input(self) -> str | None:
        input_text = await asyncio.to_thread(self.console.input_message)
        if input_text == "exit":
            return None
        return input_text

    async def _get_sub_agent(
        self,
        spec: AgentSpec,
        *,
        role_key: str | None = None,
        role_prompt: str | None = None,
    ) -> AgentRunner:
        cache_key = f"{spec.name}:{role_key or 'default'}"
        if cache_key not in self._sub_agents:
            root_session_id = await self._ensure_root_session_id()
            agent_session_id = str(
                uuid.uuid5(uuid.UUID(root_session_id), f"multiclaw-agent-{cache_key}")
            )
            workspace = resolve_agent_workspace(self.project_root, spec)
            self._sub_agents[cache_key] = AgentRunner(
                workspace=workspace,
                session_id=agent_session_id,
                role_prompt=role_prompt or spec.role_prompt,
                label=spec.display_name,
                console=self.console,
                start_worker=False,
            )
        return self._sub_agents[cache_key]

    async def _ensure_root_session_id(self) -> str:
        if self._root_session_id is None:
            self._root_session_id, _ = await self._anchor.initialize()
        return self._root_session_id

    async def _close_agents(self) -> None:
        for agent in self._sub_agents.values():
            await agent.close()
        if self._coordinator is not None:
            await self._coordinator.close()
        await self._anchor.close()

    @property
    def console(self):
        return self._anchor.console


def _should_confirm_requirements(user_input: str, agent_response: str) -> bool:
    normalized_input = user_input.strip().lower()
    if any(phrase in normalized_input for phrase in REQUIREMENTS_CONFIRM_PHRASES):
        return True
    return _response_is_confirmed(agent_response)


def _should_confirm_prototype(user_input: str, agent_response: str) -> bool:
    normalized_input = user_input.strip().lower()
    if any(phrase in normalized_input for phrase in PROTOTYPE_CONFIRM_PHRASES):
        return True
    return _response_is_confirmed(agent_response)


def _should_start_integration(user_input: str) -> bool:
    normalized_input = user_input.strip().lower()
    return any(phrase in normalized_input for phrase in INTEGRATION_START_PHRASES)


def _should_start_maintenance(user_input: str) -> bool:
    normalized_input = user_input.strip().lower()
    return any(phrase in normalized_input for phrase in MAINTENANCE_START_PHRASES)


def _response_is_confirmed(agent_response: str) -> bool:
    normalized = agent_response.lower()
    return "<!-- status: confirmed -->" in normalized or "status: confirmed" in normalized


def _enrich_task(*, task: str, confirmed_requirements: str | None) -> str:
    if not confirmed_requirements:
        return task
    return (
        "## 已确认需求\n"
        f"{confirmed_requirements}\n\n"
        "## 任务\n"
        f"{task}"
    )


def _enrich_backend_task(
    *,
    confirmed_requirements: str | None,
    confirmed_prototype: str | None,
    frontend_workspace: str,
    task: str | None = None,
) -> str:
    sections = []
    if confirmed_requirements:
        sections.append(f"## 已确认需求\n{confirmed_requirements}")
    if confirmed_prototype:
        sections.append(f"## 已确认样品摘要\n{confirmed_prototype}")
    sections.append(
        "## 前端样品位置\n"
        f"{frontend_workspace}\n"
        "请检查 src/mocks/ 及相关类型定义以了解数据结构。"
    )
    sections.append(
        "## 任务\n"
        + (
            task
            or (
                "设计 backend/docs/openapi.yaml 并实现 API 与测试。"
                "请求/响应 schema 应与前端 mock 数据结构对齐。"
            )
        )
    )
    return "\n\n".join(sections)


def _enrich_integration_task(
    *,
    confirmed_requirements: str | None,
    confirmed_prototype: str | None,
    backend_result: str | None,
    backend_workspace: str,
    task: str | None = None,
) -> str:
    sections = []
    if confirmed_requirements:
        sections.append(f"## 已确认需求\n{confirmed_requirements}")
    if confirmed_prototype:
        sections.append(f"## 已确认样品摘要\n{confirmed_prototype}")
    if backend_result:
        sections.append(f"## 后端实现摘要\n{backend_result}")
    sections.append(
        "## 后端 API 位置\n"
        f"{backend_workspace}/docs/openapi.yaml"
    )
    sections.append(
        "## 任务\n"
        + (
            task
            or (
                "将 mock 数据层替换为真实 API 对接。"
                "保持 UI 与已确认样品一致。"
            )
        )
    )
    return "\n\n".join(sections)


def _enrich_maintenance_task(
    *,
    task: str,
    confirmed_requirements: str | None,
    confirmed_prototype: str | None,
    backend_result: str | None,
    frontend_workspace: str,
    backend_workspace: str,
) -> str:
    sections = ["## 模式\n维护 — 对已有项目做针对性修改。"]
    if confirmed_requirements:
        sections.append(f"## 已确认需求\n{confirmed_requirements}")
    if confirmed_prototype:
        sections.append(f"## 样品参考\n{confirmed_prototype}")
    if backend_result:
        sections.append(f"## 后端上下文\n{backend_result}")
    sections.append(
        "## 项目路径\n"
        f"- 前端：{frontend_workspace}\n"
        f"- 后端：{backend_workspace}\n"
        f"- API 规范：{backend_workspace}/docs/openapi.yaml"
    )
    sections.append(f"## 任务\n{task}")
    return "\n\n".join(sections)
