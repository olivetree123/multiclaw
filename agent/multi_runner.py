from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

from memory import MemoryApp
from memory.config import load_memory_config

from .console import AgentConsole, SilentAgentConsole, StreamingAgentConsole
from .delegate_tools import build_delegate_tool_schemas, parse_delegate_tool_name
from .requirements_form import generate_clarification_form, needs_clarification
from .runner import AgentRunner
from .stream_events import StreamEvent
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


@dataclass
class AgentReply:
    role: str
    content: str
    agent: str | None = None


@dataclass
class SubmitResult:
    phase: Phase
    phase_changed: bool
    memory_session_id: str
    messages: list[AgentReply] = field(default_factory=list)
    requirements_form: dict | None = None


class MultiAgentRunner:
    """协调多个 AgentRunner：线性开发流程 + 维护阶段。"""

    def __init__(
        self,
        *,
        project_root: Path,
        session_id: str | None = None,
        agent_specs: tuple[AgentSpec, ...] = ACTIVE_AGENT_SPECS,
        continue_maintenance: bool = False,
        console: AgentConsole | None = None,
        user_id: str | None = None,
        close_database_connections: bool = True,
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
        self._console = console
        self._shared_memory_app: MemoryApp | None = None
        if user_id is not None:
            memory_config = replace(load_memory_config(), user_id=user_id)
            self._shared_memory_app = MemoryApp(
                memory_config,
                start_worker=True,
                close_database_connections=close_database_connections,
            )
        uses_shared_memory = self._shared_memory_app is not None
        self._anchor = AgentRunner(
            workspace=None,
            session_id=session_id,
            label="System",
            memory_app=self._shared_memory_app,
            start_worker=not uses_shared_memory,
            owns_memory_app=not uses_shared_memory,
            console=console or AgentConsole(),
        )
        self._root_session_id: str | None = None
        self._opened = False

    async def open(self) -> SubmitResult:
        """初始化工作区与 memory，返回当前阶段说明。"""
        ensure_agent_workspaces(self.project_root, self.agent_specs)
        memory_session_id = await self._ensure_root_session_id()
        self._opened = True

        messages: list[AgentReply] = [
            AgentReply(
                role="status",
                content=f"多 Agent 项目根目录：{self.project_root}",
            )
        ]

        if self.continue_maintenance:
            if not is_project_ready_for_maintenance(self.project_root):
                raise RuntimeError(
                    "未找到项目产物。请先完成开发流程，"
                    "或确保存在 docs/requirements.md、backend/docs/openapi.yaml 或 frontend/。"
                )
            self._apply_project_context(load_project_context(self.project_root))
            self.phase = Phase.MAINTENANCE
            messages.append(AgentReply(role="status", content="检测到已有项目产物，已进入维护阶段。"))
            messages.append(AgentReply(role="status", content=_phase_hint(self.phase)))
        else:
            messages.append(AgentReply(role="status", content=_phase_hint(self.phase)))

        return SubmitResult(
            phase=self.phase,
            phase_changed=False,
            memory_session_id=memory_session_id,
            messages=messages,
        )

    def apply_persisted_state(
        self,
        *,
        phase: Phase,
        confirmed_requirements: str | None = None,
        confirmed_prototype: str | None = None,
        backend_result: str | None = None,
    ) -> None:
        """从持久化记录恢复协调状态。"""
        self.phase = phase
        self.confirmed_requirements = confirmed_requirements
        self.confirmed_prototype = confirmed_prototype
        self.backend_result = backend_result

    async def restore(self) -> str:
        """服务重启后恢复会话，不重复发送 bootstrap 消息。"""
        ensure_agent_workspaces(self.project_root, self.agent_specs)
        memory_session_id = await self._ensure_root_session_id()
        self._opened = True

        if self.phase is Phase.MAINTENANCE or self.continue_maintenance:
            if is_project_ready_for_maintenance(self.project_root):
                self._apply_project_context(load_project_context(self.project_root))

        return memory_session_id

    async def set_project_root(self, project_root: Path) -> None:
        """修改项目工作目录，并重建各子 Agent 的工作区绑定。"""
        if not self._opened:
            raise RuntimeError("MultiAgentRunner 尚未 open()，请先初始化会话。")

        self.project_root = project_root.expanduser().resolve()
        ensure_agent_workspaces(self.project_root, self.agent_specs)

        for agent in self._sub_agents.values():
            await agent.close()
        self._sub_agents.clear()

        if self._coordinator is not None:
            await self._coordinator.close()
            self._coordinator = None

        if self.phase is Phase.MAINTENANCE or self.continue_maintenance:
            self._apply_project_context(load_project_context(self.project_root))

    async def submit(self, message: str) -> SubmitResult:
        """处理一条用户消息并返回 Agent 回复与阶段状态。"""
        if not self._opened:
            raise RuntimeError("MultiAgentRunner 尚未 open()，请先初始化会话。")

        phase_before = self.phase
        memory_session_id = await self._ensure_root_session_id()
        messages: list[AgentReply] = []
        requirements_form: dict | None = None

        if self.phase is Phase.REQUIREMENTS:
            messages, requirements_form = await self._submit_requirements(message)
        elif self.phase is Phase.PROTOTYPE:
            messages.extend(await self._submit_prototype(message))
        elif self.phase is Phase.BACKEND:
            messages.extend(await self._submit_backend(message))
        elif self.phase is Phase.INTEGRATION:
            messages.extend(await self._submit_integration(message))
        elif self.phase is Phase.MAINTENANCE:
            messages.extend(await self._submit_maintenance(message))

        phase_changed = self.phase is not phase_before
        if phase_changed:
            messages.append(AgentReply(role="status", content=_phase_hint(self.phase)))

        return SubmitResult(
            phase=self.phase,
            phase_changed=phase_changed,
            memory_session_id=memory_session_id,
            messages=messages,
            requirements_form=requirements_form,
        )

    async def submit_stream(self, message: str) -> AsyncIterator[StreamEvent]:
        """处理用户消息并以 SSE 事件流式返回执行过程。"""
        if not self._opened:
            raise RuntimeError("MultiAgentRunner 尚未 open()，请先初始化会话。")

        queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()
        stream_console = StreamingAgentConsole(queue)
        previous_console = self._console
        self._apply_console(stream_console)

        async def worker() -> SubmitResult:
            try:
                return await self.submit(message)
            finally:
                await queue.put(None)

        task = asyncio.create_task(worker())
        try:
            yield StreamEvent("started", {"message": message, "phase": self.phase.value})
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
            result = await task
            if result.requirements_form is not None:
                yield StreamEvent("form", result.requirements_form)
            yield StreamEvent(
                "done",
                {
                    "memory_session_id": result.memory_session_id,
                    "phase": result.phase.value,
                    "phase_changed": result.phase_changed,
                    "requirements_form": result.requirements_form,
                    "messages": [
                        {
                            "role": item.role,
                            "content": item.content,
                            "agent": item.agent,
                        }
                        for item in result.messages
                    ],
                },
            )
        except Exception as error:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            yield StreamEvent("error", {"detail": str(error)})
        finally:
            self._apply_console(previous_console or SilentAgentConsole())

    async def run(self) -> None:
        """CLI 交互循环。"""
        result = await self.open()
        self._render_result(result)
        try:
            while True:
                input_text = await self._read_user_input()
                if input_text is None:
                    return
                self.console.print_user_message(input_text)
                result = await self.submit(input_text)
                self._render_result(result)
        finally:
            await self.close()

    async def _submit_requirements(self, message: str) -> tuple[list[AgentReply], dict | None]:
        product_manager = await self._get_sub_agent(PRODUCT_MANAGER_SPEC)
        result = await product_manager.run_task(message)
        replies = [AgentReply(role="assistant", content=result, agent=PRODUCT_MANAGER_SPEC.display_name)]
        requirements_form: dict | None = None

        if _should_confirm_requirements(message, result):
            self.confirmed_requirements = result
            self.phase = Phase.PROTOTYPE
            replies.append(AgentReply(role="status", content="需求已确认，进入样品开发阶段。"))
            replies.extend(await self._bootstrap_prototype())
        elif needs_clarification(result):
            try:
                requirements_form = await generate_clarification_form(result)
            except Exception as error:
                self.console.print_error("需求澄清表单生成失败", error)

        return replies, requirements_form

    async def _submit_prototype(self, message: str) -> list[AgentReply]:
        frontend = await self._get_sub_agent(
            FRONTEND_SPEC,
            role_key="prototype",
            role_prompt=FRONTEND_PROTOTYPE_ROLE_PROMPT,
        )
        result = await frontend.run_task(message)
        replies = [AgentReply(role="assistant", content=result, agent=FRONTEND_SPEC.display_name)]

        if _should_confirm_prototype(message, result):
            self.confirmed_prototype = result
            self.phase = Phase.BACKEND
            replies.append(AgentReply(role="status", content="样品已确认，进入后端接口开发阶段。"))
            replies.extend(await self._bootstrap_backend())
        return replies

    async def _submit_backend(self, message: str) -> list[AgentReply]:
        if _should_start_integration(message):
            self.phase = Phase.INTEGRATION
            replies = [AgentReply(role="status", content="进入前端接口对接阶段。")]
            replies.extend(await self._bootstrap_integration())
            return replies

        backend = await self._get_sub_agent(BACKEND_SPEC)
        result = await backend.run_task(
            _enrich_backend_task(
                confirmed_requirements=self.confirmed_requirements,
                confirmed_prototype=self.confirmed_prototype,
                frontend_workspace=str(resolve_agent_workspace(self.project_root, FRONTEND_SPEC)),
                task=message,
            )
        )
        self.backend_result = result
        return [AgentReply(role="assistant", content=result, agent=BACKEND_SPEC.display_name)]

    async def _submit_integration(self, message: str) -> list[AgentReply]:
        if _should_start_maintenance(message):
            self.phase = Phase.MAINTENANCE
            return [AgentReply(role="status", content="开发流程完成，进入维护阶段。")]

        frontend = await self._get_sub_agent(
            FRONTEND_SPEC,
            role_key="integration",
            role_prompt=FRONTEND_INTEGRATION_ROLE_PROMPT,
        )
        result = await frontend.run_task(
            _enrich_integration_task(
                confirmed_requirements=self.confirmed_requirements,
                confirmed_prototype=self.confirmed_prototype,
                backend_result=self.backend_result,
                backend_workspace=str(resolve_agent_workspace(self.project_root, BACKEND_SPEC)),
                task=message,
            )
        )
        return [AgentReply(role="assistant", content=result, agent=FRONTEND_SPEC.display_name)]

    async def _submit_maintenance(self, message: str) -> list[AgentReply]:
        coordinator = await self._get_coordinator()
        delegate_schemas = build_delegate_tool_schemas(self.agent_specs)
        result = await coordinator.run_task(
            message,
            extra_tool_schemas=delegate_schemas,
            tool_handler=self._handle_maintenance_delegate,
        )
        return [AgentReply(role="assistant", content=result, agent="协调员")]

    async def _bootstrap_prototype(self) -> list[AgentReply]:
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
        result = await frontend.run_task(initial_task)
        return [
            AgentReply(role="status", content="正在生成初始样品..."),
            AgentReply(role="assistant", content=result, agent=FRONTEND_SPEC.display_name),
        ]

    async def _bootstrap_backend(self) -> list[AgentReply]:
        backend = await self._get_sub_agent(BACKEND_SPEC)
        initial_task = _enrich_backend_task(
            confirmed_requirements=self.confirmed_requirements,
            confirmed_prototype=self.confirmed_prototype,
            frontend_workspace=str(resolve_agent_workspace(self.project_root, FRONTEND_SPEC)),
        )
        result = await backend.run_task(initial_task)
        self.backend_result = result
        return [
            AgentReply(role="status", content="正在开发后端接口..."),
            AgentReply(role="assistant", content=result, agent=BACKEND_SPEC.display_name),
        ]

    async def _bootstrap_integration(self) -> list[AgentReply]:
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
        result = await frontend.run_task(initial_task)
        return [
            AgentReply(role="status", content="正在对接前端与后端接口..."),
            AgentReply(role="assistant", content=result, agent=FRONTEND_SPEC.display_name),
        ]

    def _apply_project_context(self, context: dict[str, str | None]) -> None:
        self.confirmed_requirements = context.get("confirmed_requirements")
        self.confirmed_prototype = context.get("confirmed_prototype")
        self.backend_result = context.get("backend_result")

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

        result = await sub_agent.run_task(enriched_task)

        if agent_name == PRODUCT_MANAGER_SPEC.name:
            if _response_is_confirmed(result):
                self.confirmed_requirements = result
            else:
                requirements_file = (
                    resolve_agent_workspace(self.project_root, PRODUCT_MANAGER_SPEC) / "requirements.md"
                )
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
                memory_app=self._shared_memory_app,
                start_worker=False,
                owns_memory_app=False,
                console=self.console,
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
                memory_app=self._shared_memory_app,
                start_worker=False,
                owns_memory_app=False,
                console=self.console,
            )
        return self._sub_agents[cache_key]

    async def _ensure_root_session_id(self) -> str:
        if self._root_session_id is None:
            self._root_session_id, _ = await self._anchor.initialize()
        return self._root_session_id

    async def close(self) -> None:
        for agent in self._sub_agents.values():
            await agent.close()
        if self._coordinator is not None:
            await self._coordinator.close()
        await self._anchor.close()
        if self._shared_memory_app is not None:
            await self._shared_memory_app.close()
        self._opened = False

    def _render_result(self, result: SubmitResult) -> None:
        for item in result.messages:
            if item.role == "status":
                self.console.print_status(item.content)
            elif item.role == "assistant":
                self.console.print_assistant_message(item.content, label=item.agent)

    def _apply_console(self, console: AgentConsole) -> None:
        self._console = console
        self._anchor.console = console
        for agent in self._sub_agents.values():
            agent.console = console
        if self._coordinator is not None:
            self._coordinator.console = console

    @property
    def console(self) -> AgentConsole:
        if self._console is not None:
            return self._console
        return self._anchor.console


def create_api_runner(
    *,
    project_root: Path,
    session_id: str | None = None,
    continue_maintenance: bool = False,
    user_id: str,
) -> MultiAgentRunner:
    return MultiAgentRunner(
        project_root=project_root,
        session_id=session_id,
        continue_maintenance=continue_maintenance,
        console=SilentAgentConsole(),
        user_id=user_id,
        close_database_connections=False,
    )


def _phase_hint(phase: Phase) -> str:
    hints = {
        Phase.REQUIREMENTS: (
            "【阶段 1/4】需求确认：与产品经理对话。"
            "输入「确认需求」进入样品开发。"
        ),
        Phase.PROTOTYPE: (
            "【阶段 2/4】样品开发：前端 Agent 使用 Mock 数据。"
            "输入「确认样品」进入后端开发。"
        ),
        Phase.BACKEND: (
            "【阶段 3/4】后端开发：实现 API。"
            "输入「开始对接」进入前端集成。"
        ),
        Phase.INTEGRATION: (
            "【阶段 4/4】前端对接：替换 Mock 为真实 API。"
            "输入「进入维护」进入维护阶段。"
        ),
        Phase.MAINTENANCE: (
            "【维护阶段】描述修改内容，协调员将委派合适的 Agent。"
        ),
    }
    return hints[phase]


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
