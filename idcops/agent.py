"""Active read-only incident investigator with honest baseline and test-stub modes."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from .agent_model import AgentModelError, OpenAICompatibleAgentPlanner
from .agent_tools import AgentToolRegistry
from .agent_trace import AgentTraceRecorder
from .ai import AIEnricher
from .store import IncidentStore


class AgentInvestigator:
    def __init__(
        self,
        store: IncidentStore,
        tools: AgentToolRegistry,
        traces: AgentTraceRecorder,
        ai: AIEnricher,
        planner: Optional[Any] = None,
    ) -> None:
        self.store = store
        self.tools = tools
        self.traces = traces
        self.ai = ai
        self.planner = planner or OpenAICompatibleAgentPlanner(ai)

    def run(self, incident: Mapping[str, Any], mode: str = "baseline", max_rounds: int = 5) -> Dict[str, Any]:
        if mode not in {"baseline", "test_stub", "model"}:
            raise ValueError("调查模式必须是 baseline、test_stub 或 model")
        rounds = max(1, min(int(max_rounds), 8))
        if mode == "model" and not self.ai.enabled:
            run_id = self.traces.start(
                str(incident["id"]),
                "model",
                "not-configured",
                "not-configured",
                "agent-v1",
                rounds,
            )
            self.traces.add_step(
                run_id,
                1,
                {
                    "step_type": "model_availability",
                    "status": "not_run",
                    "rationale": "真实模型没有连通，不能把测试规划器冒充成AI。",
                    "validation": {"model_enabled": False},
                },
            )
            return self.traces.finish(
                run_id,
                "not_run",
                "model_not_configured",
                {"message": "真实模型未配置；固定基线仍可使用。", "real_ai": False},
            )
        if mode == "model":
            return self._run_model(incident, rounds)
        if mode == "baseline":
            return self._run_baseline(incident)
        return self._run_test_stub(incident, rounds)

    def _run_baseline(self, incident: Mapping[str, Any]) -> Dict[str, Any]:
        investigation = incident.get("investigation", {})
        hypotheses = list(investigation.get("hypotheses", []))
        run_id = self.traces.start(
            str(incident["id"]), "baseline", "none", "none", "rules-and-knowledge", 1
        )
        self.traces.add_step(
            run_id,
            1,
            {
                "step_type": "baseline_result",
                "status": "completed",
                "rationale": "固定规则和知识库整理已有证据，不主动调用外部工具。",
                "input": {"evidence": incident.get("evidence", [])},
                "hypotheses_after": hypotheses,
                "evidence_ids": [item.get("id") for item in incident.get("evidence", [])],
                "validation": {"real_ai": False, "active_tool_calls": False},
            },
        )
        return self.traces.finish(
            run_id,
            "completed",
            "baseline_complete",
            {
                "real_ai": False,
                "label": "固定规则基线",
                "hypotheses": hypotheses,
                "conclusion": investigation.get("conclusion", {}),
            },
        )

    def _run_test_stub(self, incident: Mapping[str, Any], max_rounds: int) -> Dict[str, Any]:
        investigation = incident.get("investigation", {})
        hypotheses = list(investigation.get("hypotheses", []))
        category = str(incident.get("category") or "unknown")
        plans = {
            "network": [
                ("network.query_port", "先核对端口抖动、CRC和链路状态。"),
                ("bmc.query_health", "确认服务器是否仍通电并核对网卡链路，削弱掉电假设。"),
                ("linux.query_logs", "核对系统侧carrier和连接超时是否晚于网络告警。"),
                ("oms.query_asset", "补充完整资产和端口映射；无数据时明确缺口。"),
            ],
            "facility": [
                ("facility.query_environment", "先核对动环环境与区域范围。"),
                ("bmc.query_health", "查询服务器传感器是否出现同向温度或电源信号。"),
                ("linux.query_logs", "查询系统是否出现降频、关机或不可达。"),
            ],
            "hardware": [
                ("bmc.query_health", "读取硬件健康和SEL。"),
                ("linux.query_logs", "对照操作系统日志。"),
                ("oms.query_asset", "核对完整资产和工单上下文。"),
            ],
        }
        plan = plans.get(
            category,
            [
                ("linux.query_logs", "查询系统和应用侧同一时间窗证据。"),
                ("network.query_port", "检查网络路径是否造成上层症状。"),
                ("bmc.query_health", "检查带外硬件状态。"),
            ],
        )[:max_rounds]
        run_id = self.traces.start(
            str(incident["id"]),
            "test_stub",
            "test-stub",
            "deterministic-protocol-stub",
            "agent-v1",
            max_rounds,
        )
        evidence_ids = [str(item.get("id")) for item in incident.get("evidence", []) if item.get("id")]
        for round_no, (tool_name, rationale) in enumerate(plan, start=1):
            before = list(hypotheses)
            arguments = {"incident_id": str(incident["id"]), "limit": 50}
            output = self.tools.execute(tool_name, arguments)
            tool_evidence = [str(item.get("id")) for item in output.get("records", [])]
            evidence_ids.extend(item for item in tool_evidence if item not in evidence_ids)
            hypotheses = self._stub_update(hypotheses, tool_name, output)
            self.traces.add_step(
                run_id,
                round_no,
                {
                    "step_type": "tool_investigation",
                    "status": output.get("state", "completed"),
                    "rationale": rationale,
                    "input": {"available_evidence_ids": evidence_ids},
                    "tool_name": tool_name,
                    "tool_args": arguments,
                    "tool_output": output,
                    "evidence_ids": tool_evidence,
                    "hypotheses_before": before,
                    "hypotheses_after": hypotheses,
                    "validation": {
                        "read_only": True,
                        "identity_inferred_by_model": False,
                        "real_ai": False,
                        "mode_label": "test_stub_not_real_ai",
                    },
                    "model_output": {},
                },
            )
        return self.traces.finish(
            run_id,
            "completed",
            "test_stub_plan_complete",
            {
                "real_ai": False,
                "label": "测试模型桩（非真实AI）",
                "hypotheses": hypotheses,
                "tool_calls": len(plan),
                "message": "该运行只验证Agent协议、工具和回放，不证明AI推理能力。",
            },
        )

    @staticmethod
    def _stub_update(hypotheses: list, tool_name: str, output: Mapping[str, Any]) -> list:
        result = [dict(item) for item in hypotheses[:6]]
        text = str(output).lower()
        title = ""
        if tool_name.startswith("network.") and any(term in text for term in ("crc", "link", "optical")):
            title = "交换机端口、模块或链路路径异常"
        elif tool_name == "bmc.query_health" and "power_state" in text and "on" in text:
            title = "服务器仍通电，整机掉电方向被削弱"
        elif tool_name == "facility.query_environment" and any(term in text for term in ("temperature", "cooling")):
            title = "制冷或环境温度异常"
        elif tool_name == "linux.query_logs" and any(term in text for term in ("carrier", "timeout", "thrott")):
            title = "系统日志与上游故障时间线一致"
        if title and not any(item.get("title") == title for item in result):
            result.insert(0, {"title": title, "status": "candidate", "generated_by": "test_stub"})
        return result[:6]

    def _run_model(self, incident: Mapping[str, Any], max_rounds: int) -> Dict[str, Any]:
        """Run a real model/tool loop while persisting an explicit audit trace."""
        run_id = self.traces.start(
            str(incident["id"]),
            "model",
            self.ai.url,
            self.ai.model,
            getattr(self.planner, "prompt_version", "agent-investigator-v1"),
            max_rounds,
        )
        hypotheses = list(incident.get("investigation", {}).get("hypotheses", []))
        history = []
        tools = self.tools.list_tools()
        for round_no in range(1, max_rounds + 1):
            before = list(hypotheses)
            try:
                decision = self.planner.propose(
                    incident, hypotheses, tools, history, round_no
                )
            except AgentModelError as exc:
                self.traces.add_step(
                    run_id,
                    round_no,
                    {
                        "step_type": "model_decision",
                        "status": "failed",
                        "rationale": "真实模型未返回通过结构校验的调查决策。",
                        "hypotheses_before": before,
                        "hypotheses_after": hypotheses,
                        "validation": {
                            "real_ai": True,
                            "accepted": False,
                            "error": str(exc),
                        },
                    },
                )
                return self.traces.finish(
                    run_id,
                    "failed",
                    "model_or_contract_error",
                    {
                        "real_ai": True,
                        "hypotheses": hypotheses,
                        "message": "模型调用或输出校验失败；没有生成伪造结论。",
                    },
                )

            proposed = list(decision.get("hypotheses", []))
            if proposed:
                hypotheses = proposed
            trace = dict(decision.get("_trace") or {})
            public_decision = {key: value for key, value in decision.items() if key != "_trace"}
            if decision.get("stop"):
                self.traces.add_step(
                    run_id,
                    round_no,
                    {
                        "step_type": "model_stop",
                        "status": "completed",
                        "rationale": decision.get("rationale"),
                        "input": {"history": history},
                        "evidence_ids": decision.get("evidence_ids", []),
                        "hypotheses_before": before,
                        "hypotheses_after": hypotheses,
                        "validation": {
                            "real_ai": True,
                            "read_only": True,
                            "identity_inferred_by_model": False,
                            "accepted": True,
                        },
                        "model_output": {**public_decision, "trace": trace},
                    },
                )
                return self.traces.finish(
                    run_id,
                    "completed",
                    str(decision.get("stop_reason") or "model_stop"),
                    {
                        "real_ai": True,
                        "label": "真实AI只读调查",
                        "hypotheses": hypotheses,
                        "conclusion": decision.get("conclusion") or "保持候选，等待人工判断。",
                        "tool_calls": len(history),
                    },
                )

            action = dict(decision["next_action"])
            tool_name = str(action["tool"])
            arguments = dict(action["args"])
            output = self.tools.execute(tool_name, arguments)
            record_ids = [str(item.get("id")) for item in output.get("records", []) if item.get("id")]
            history.append(
                {
                    "round": round_no,
                    "tool": tool_name,
                    "state": output.get("state", "completed"),
                    "record_ids": record_ids,
                    "record_count": len(output.get("records", [])),
                    "platform_message": output.get("message", ""),
                }
            )
            self.traces.add_step(
                run_id,
                round_no,
                {
                    "step_type": "model_tool_investigation",
                    "status": output.get("state", "completed"),
                    "rationale": decision.get("rationale"),
                    "input": {"prior_tool_history": history[:-1]},
                    "tool_name": tool_name,
                    "tool_args": arguments,
                    "tool_output": output,
                    "evidence_ids": list(dict.fromkeys(decision.get("evidence_ids", []) + record_ids)),
                    "hypotheses_before": before,
                    "hypotheses_after": hypotheses,
                    "validation": {
                        "real_ai": True,
                        "read_only": True,
                        "identity_inferred_by_model": False,
                        "tool_allow_listed": True,
                        "accepted": True,
                    },
                    "model_output": {**public_decision, "trace": trace},
                },
            )

        return self.traces.finish(
            run_id,
            "completed",
            "max_rounds_reached",
            {
                "real_ai": True,
                "label": "真实AI只读调查",
                "hypotheses": hypotheses,
                "conclusion": "达到调查轮数上限，保持候选并交由人工判断。",
                "tool_calls": len(history),
            },
        )
