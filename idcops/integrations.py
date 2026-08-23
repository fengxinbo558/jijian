"""Read-only integrations for telemetry storage and AI investigation services."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .models import utc_now


class IntegrationError(RuntimeError):
    """An external service could not complete a read-only request."""


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(number, maximum))


def _service_url(value: str, name: str) -> str:
    cleaned = value.strip().rstrip("/")
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{name} 必须是 http 或 https 地址")
    if parsed.username or parsed.password:
        raise ValueError(f"{name} 不允许在地址中携带账号或密码")
    return cleaned


@dataclass(frozen=True)
class IntegrationSettings:
    signoz_url: str = ""
    signoz_api_key: str = ""
    holmes_url: str = ""
    holmes_api_key: str = ""
    holmes_model: str = ""
    request_timeout: int = 4
    query_window_minutes: int = 20
    max_records: int = 40
    max_response_bytes: int = 512_000

    @classmethod
    def from_environ(cls, environ: Optional[Mapping[str, str]] = None) -> "IntegrationSettings":
        values = environ or os.environ
        return cls(
            signoz_url=_service_url(values.get("IDCAI_SIGNOZ_URL", ""), "IDCAI_SIGNOZ_URL"),
            signoz_api_key=values.get("IDCAI_SIGNOZ_API_KEY", "").strip(),
            holmes_url=_service_url(values.get("IDCAI_HOLMES_URL", ""), "IDCAI_HOLMES_URL"),
            holmes_api_key=values.get("IDCAI_HOLMES_API_KEY", "").strip(),
            holmes_model=values.get("IDCAI_HOLMES_MODEL", "").strip(),
            request_timeout=_bounded_int(values.get("IDCAI_INTEGRATION_TIMEOUT"), 4, 1, 30),
            query_window_minutes=_bounded_int(
                values.get("IDCAI_QUERY_WINDOW_MINUTES"), 20, 5, 180
            ),
            max_records=_bounded_int(values.get("IDCAI_QUERY_MAX_RECORDS"), 40, 1, 200),
            max_response_bytes=_bounded_int(
                values.get("IDCAI_QUERY_MAX_BYTES"), 512_000, 16_384, 2_000_000
            ),
        )


def _json_request(
    method: str,
    url: str,
    headers: Optional[Mapping[str, str]],
    payload: Optional[Mapping[str, Any]],
    timeout: int,
    max_bytes: int,
) -> Tuple[int, Any]:
    body = None
    merged_headers: MutableMapping[str, str] = {"Accept": "application/json"}
    if headers:
        merged_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        merged_headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=dict(merged_headers), method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            if len(raw) > max_bytes:
                raise IntegrationError("外部服务响应超过安全大小限制")
            if not raw:
                return int(response.status), {}
            try:
                return int(response.status), json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise IntegrationError("外部服务返回的不是有效 JSON") from exc
    except HTTPError as exc:
        if exc.code in {401, 403}:
            raise IntegrationError("认证失败，请检查只读访问密钥") from exc
        raise IntegrationError(f"外部服务返回 HTTP {exc.code}") from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        raise IntegrationError("无法连接服务或请求超时") from exc


def _status(
    source_id: str,
    name: str,
    role: str,
    state: str,
    message: str,
    configured: bool,
    automatic: bool,
) -> Dict[str, Any]:
    return {
        "id": source_id,
        "name": name,
        "role": role,
        "state": state,
        "message": message,
        "configured": configured,
        "automatic": automatic,
        "read_only": True,
        "checked_at": utc_now(),
    }


def _parse_time(value: Any) -> datetime:
    text = str(value or "").strip()
    if text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _sql_literal(value: Any) -> str:
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")[:300]


def _walk_records(value: Any, result: List[Dict[str, Any]], limit: int) -> None:
    if len(result) >= limit:
        return
    if isinstance(value, list):
        if value and all(isinstance(item, Mapping) for item in value):
            for item in value[: limit - len(result)]:
                result.append(dict(item))
            return
        for item in value:
            _walk_records(item, result, limit)
    elif isinstance(value, Mapping):
        preferred = ("rows", "records", "hits", "result", "results", "data", "payload")
        for key in preferred:
            if key in value:
                _walk_records(value[key], result, limit)
                if result:
                    return


def _record_text(record: Mapping[str, Any]) -> str:
    preferred = ("body", "message", "log", "event", "description")
    for key in preferred:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.strip().split())[:1200]
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))[:1200]


class SigNozClient:
    """Minimal read-only client for the SigNoz Community API."""

    def __init__(self, settings: IntegrationSettings) -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(self.settings.signoz_url)

    def check(self) -> Dict[str, Any]:
        if not self.configured:
            return _status(
                "signoz",
                "SigNoz 监控底座",
                "保存并查询真实日志、指标和告警",
                "not_configured",
                "尚未填写 SigNoz 地址，当前不会自动读取监控数据",
                False,
                True,
            )
        try:
            status, _payload = _json_request(
                "GET",
                self.settings.signoz_url + "/api/v1/health",
                self._headers(),
                None,
                self.settings.request_timeout,
                self.settings.max_response_bytes,
            )
            if status != 200:
                raise IntegrationError(f"健康检查返回 HTTP {status}")
            return _status(
                "signoz",
                "SigNoz 监控底座",
                "保存并查询真实日志、指标和告警",
                "connected",
                "服务可连接；是否已有客户数据需要在具体调查时确认",
                True,
                True,
            )
        except IntegrationError as exc:
            return _status(
                "signoz",
                "SigNoz 监控底座",
                "保存并查询真实日志、指标和告警",
                "failed",
                str(exc),
                True,
                True,
            )

    def _headers(self) -> Dict[str, str]:
        if not self.settings.signoz_api_key:
            return {}
        return {"SIGNOZ-API-KEY": self.settings.signoz_api_key}

    def query_logs(self, incident: Mapping[str, Any]) -> Dict[str, Any]:
        if not self.configured:
            return {
                "provider": "signoz",
                "state": "not_configured",
                "message": "SigNoz 尚未连接，未执行自动日志查询",
                "records": [],
                "tool_calls": [],
                "checked_at": utc_now(),
            }
        devices = incident.get("devices") if isinstance(incident.get("devices"), list) else []
        first = devices[0] if devices and isinstance(devices[0], Mapping) else {}
        identifiers = [first.get("name"), first.get("sn"), first.get("ip")]
        identifiers = [str(item).strip() for item in identifiers if str(item or "").strip()]
        if not identifiers:
            return {
                "provider": "signoz",
                "state": "skipped",
                "message": "缺少设备名、完整 SN 或 IP，无法构造安全查询条件",
                "records": [],
                "tool_calls": [],
                "checked_at": utc_now(),
            }

        center = _parse_time(incident.get("updated_at") or incident.get("created_at"))
        half_window = timedelta(minutes=self.settings.query_window_minutes)
        start = int((center - half_window).timestamp() * 1000)
        end = int((center + half_window).timestamp() * 1000)
        clauses = [f"body CONTAINS '{_sql_literal(item)}'" for item in identifiers]
        expression = " OR ".join(clauses)
        query = {
            "start": start,
            "end": end,
            "requestType": "raw",
            "compositeQuery": {
                "queries": [
                    {
                        "type": "builder_query",
                        "spec": {
                            "name": "A",
                            "signal": "logs",
                            "filter": {"expression": expression},
                            "limit": self.settings.max_records,
                            "offset": 0,
                            "disabled": False,
                        },
                    }
                ]
            },
        }
        started = utc_now()
        try:
            _status_code, payload = _json_request(
                "POST",
                self.settings.signoz_url + "/api/v5/query_range",
                self._headers(),
                query,
                self.settings.request_timeout,
                self.settings.max_response_bytes,
            )
            records: List[Dict[str, Any]] = []
            _walk_records(payload, records, self.settings.max_records)
            return {
                "provider": "signoz",
                "state": "completed",
                "message": f"按设备身份和故障时间窗查询到 {len(records)} 条日志记录",
                "records": [
                    {"text": _record_text(item), "attributes": dict(item)} for item in records
                ],
                "record_count": len(records),
                "query": {
                    "signal": "logs",
                    "identifiers": identifiers,
                    "start": start,
                    "end": end,
                    "limit": self.settings.max_records,
                },
                "tool_calls": [
                    {
                        "tool": "signoz.query_logs",
                        "started_at": started,
                        "finished_at": utc_now(),
                        "read_only": True,
                    }
                ],
                "checked_at": utc_now(),
            }
        except IntegrationError as exc:
            return {
                "provider": "signoz",
                "state": "failed",
                "message": str(exc),
                "records": [],
                "record_count": 0,
                "query": {
                    "signal": "logs",
                    "identifiers": identifiers,
                    "start": start,
                    "end": end,
                    "limit": self.settings.max_records,
                },
                "tool_calls": [],
                "checked_at": utc_now(),
            }

    def query_metrics(self, incident: Mapping[str, Any]) -> Dict[str, Any]:
        if not self.configured:
            return {
                "provider": "signoz",
                "signal": "metrics",
                "state": "not_configured",
                "message": "SigNoz 尚未连接，未执行主机指标查询",
                "metrics": [],
                "tool_calls": [],
                "checked_at": utc_now(),
            }
        devices = incident.get("devices") if isinstance(incident.get("devices"), list) else []
        first = devices[0] if devices and isinstance(devices[0], Mapping) else {}
        hostname = str(first.get("name") or "").strip()
        if not hostname:
            return {
                "provider": "signoz",
                "signal": "metrics",
                "state": "skipped",
                "message": "缺少设备名，暂不执行主机指标查询",
                "metrics": [],
                "tool_calls": [],
                "checked_at": utc_now(),
            }
        center = _parse_time(incident.get("updated_at") or incident.get("created_at"))
        half_window = timedelta(minutes=self.settings.query_window_minutes)
        start = int((center - half_window).timestamp() * 1000)
        end = int((center + half_window).timestamp() * 1000)
        metric_names = (
            "system.cpu.time",
            "system.memory.usage",
            "system.filesystem.usage",
            "system.disk.io",
            "system.network.io",
        )
        queries = []
        for index, metric_name in enumerate(metric_names):
            queries.append(
                {
                    "type": "builder_query",
                    "spec": {
                        "name": chr(ord("A") + index),
                        "signal": "metrics",
                        "stepInterval": 60,
                        "aggregations": [
                            {
                                "metricName": metric_name,
                                "timeAggregation": "avg",
                                "spaceAggregation": "sum",
                            }
                        ],
                        "filter": {
                            "expression": f"host.name = '{_sql_literal(hostname)}'"
                        },
                        "disabled": False,
                    },
                }
            )
        query = {
            "start": start,
            "end": end,
            "requestType": "time_series",
            "compositeQuery": {"queries": queries},
        }
        started = utc_now()
        try:
            _status_code, payload = _json_request(
                "POST",
                self.settings.signoz_url + "/api/v5/query_range",
                self._headers(),
                query,
                self.settings.request_timeout,
                self.settings.max_response_bytes,
            )
            metrics: List[Dict[str, Any]] = []
            _walk_records(payload, metrics, self.settings.max_records)
            return {
                "provider": "signoz",
                "signal": "metrics",
                "state": "completed",
                "message": f"按设备名和故障时间窗查询到 {len(metrics)} 组主机指标结果",
                "metrics": [
                    {"text": _record_text(item), "attributes": dict(item)} for item in metrics
                ],
                "metric_count": len(metrics),
                "query": {
                    "signal": "metrics",
                    "host_name": hostname,
                    "metric_names": list(metric_names),
                    "start": start,
                    "end": end,
                },
                "tool_calls": [
                    {
                        "tool": "signoz.query_metrics",
                        "started_at": started,
                        "finished_at": utc_now(),
                        "read_only": True,
                    }
                ],
                "checked_at": utc_now(),
            }
        except IntegrationError as exc:
            return {
                "provider": "signoz",
                "signal": "metrics",
                "state": "failed",
                "message": str(exc),
                "metrics": [],
                "metric_count": 0,
                "query": {
                    "signal": "metrics",
                    "host_name": hostname,
                    "metric_names": list(metric_names),
                    "start": start,
                    "end": end,
                },
                "tool_calls": [],
                "checked_at": utc_now(),
            }

    def query_telemetry(self, incident: Mapping[str, Any]) -> Dict[str, Any]:
        logs = self.query_logs(incident)
        metrics = self.query_metrics(incident)
        states = {str(logs.get("state")), str(metrics.get("state"))}
        if "completed" in states:
            state = "completed"
        elif states == {"not_configured"}:
            state = "not_configured"
        elif "failed" in states:
            state = "failed"
        else:
            state = "skipped"
        return {
            "provider": "signoz",
            "state": state,
            "message": f"日志：{logs.get('message', '未执行')}；指标：{metrics.get('message', '未执行')}",
            "records": logs.get("records", []),
            "record_count": logs.get("record_count", 0),
            "metrics": metrics.get("metrics", []),
            "metric_count": metrics.get("metric_count", 0),
            "queries": [logs.get("query", {}), metrics.get("query", {})],
            "tool_calls": list(logs.get("tool_calls", [])) + list(metrics.get("tool_calls", [])),
            "checked_at": utc_now(),
        }


class HolmesClient:
    """Authenticated read-only client for the HolmesGPT HTTP API."""

    def __init__(self, settings: IntegrationSettings) -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(self.settings.holmes_url)

    def _headers(self) -> Dict[str, str]:
        if not self.settings.holmes_api_key:
            return {}
        return {"X-API-Key": self.settings.holmes_api_key}

    def check(self) -> Dict[str, Any]:
        if not self.configured:
            return _status(
                "holmes",
                "HolmesGPT AI 调查",
                "调用只读工具补充调查，并返回工具记录",
                "not_configured",
                "尚未填写 HolmesGPT 地址，当前使用规则和知识库分析",
                False,
                True,
            )
        try:
            status, _payload = _json_request(
                "GET",
                self.settings.holmes_url + "/healthz",
                self._headers(),
                None,
                self.settings.request_timeout,
                self.settings.max_response_bytes,
            )
            if status != 200:
                raise IntegrationError(f"健康检查返回 HTTP {status}")
            return _status(
                "holmes",
                "HolmesGPT AI 调查",
                "调用只读工具补充调查，并返回工具记录",
                "connected",
                "AI 调查服务可连接；具体可用工具以调查返回记录为准",
                True,
                True,
            )
        except IntegrationError as exc:
            return _status(
                "holmes",
                "HolmesGPT AI 调查",
                "调用只读工具补充调查，并返回工具记录",
                "failed",
                str(exc),
                True,
                True,
            )

    def investigate(
        self, incident: Mapping[str, Any], observations: Sequence[Mapping[str, Any]]
    ) -> Dict[str, Any]:
        if not self.configured:
            return {
                "provider": "holmes",
                "state": "not_configured",
                "message": "HolmesGPT 尚未连接，未执行 AI 工具调查",
                "analysis": "",
                "tool_calls": [],
                "checked_at": utc_now(),
            }
        investigation = incident.get("investigation")
        facts = investigation.get("extracted_facts", []) if isinstance(investigation, Mapping) else []
        prompt = {
            "task": "只读调查当前 IDC 故障。只使用已连接工具和给定事实，不执行修复，不决定断电或现场操作许可。",
            "incident": {
                "id": incident.get("id"),
                "site": incident.get("site"),
                "title": incident.get("title"),
                "summary": incident.get("summary"),
                "devices": incident.get("devices", []),
                "created_at": incident.get("created_at"),
                "updated_at": incident.get("updated_at"),
            },
            "known_facts": facts[:30],
            "external_observations": list(observations)[-5:],
            "required_output": "说明查询了什么、发现了什么、仍缺什么；结论必须能对应工具结果。",
        }
        body: Dict[str, Any] = {
            "ask": json.dumps(prompt, ensure_ascii=False, separators=(",", ":"))
        }
        if self.settings.holmes_model:
            body["model"] = self.settings.holmes_model
        started = utc_now()
        try:
            _status_code, response = _json_request(
                "POST",
                self.settings.holmes_url + "/api/chat",
                self._headers(),
                body,
                max(self.settings.request_timeout, 10),
                self.settings.max_response_bytes,
            )
            if not isinstance(response, Mapping):
                raise IntegrationError("HolmesGPT 返回结构无效")
            raw_calls = response.get("tool_calls")
            calls = list(raw_calls) if isinstance(raw_calls, list) else []
            safe_calls = []
            for item in calls[:50]:
                if not isinstance(item, Mapping):
                    continue
                safe_calls.append(
                    {
                        "tool": str(item.get("tool_name") or item.get("name") or item.get("tool") or "只读工具")[:120],
                        "description": str(item.get("description") or item.get("result") or "")[:800],
                        "read_only": True,
                    }
                )
            analysis = str(response.get("analysis") or response.get("answer") or "")[:12_000]
            return {
                "provider": "holmes",
                "state": "completed",
                "message": f"AI 调查完成，返回 {len(safe_calls)} 条工具调用记录",
                "analysis": analysis,
                "tool_calls": safe_calls,
                "started_at": started,
                "checked_at": utc_now(),
                "evidence_policy": "回答不能覆盖设备身份、操作许可或未经工具验证的事实",
            }
        except IntegrationError as exc:
            return {
                "provider": "holmes",
                "state": "failed",
                "message": str(exc),
                "analysis": "",
                "tool_calls": [],
                "started_at": started,
                "checked_at": utc_now(),
            }


class IntegrationHub:
    """Expose honest source state and coordinate one bounded investigation pass."""

    def __init__(self, settings: Optional[IntegrationSettings] = None) -> None:
        self.settings = settings or IntegrationSettings.from_environ()
        self.signoz = SigNozClient(self.settings)
        self.holmes = HolmesClient(self.settings)

    def source_statuses(self, check_external: bool = True) -> List[Dict[str, Any]]:
        now = utc_now()
        built_in = [
            {
                "id": "manual_log",
                "name": "上传或粘贴日志",
                "role": "分析手里已有的 Linux、BMC、交换机或应用日志",
                "state": "available",
                "message": "可使用；数据只提交到当前机鉴服务",
                "configured": True,
                "automatic": False,
                "read_only": True,
                "checked_at": now,
            },
            {
                "id": "onsite_report",
                "name": "现场发现异常",
                "role": "记录完整 SN、机架位和现场观察",
                "state": "available",
                "message": "可使用；身份和权限仍需人工确认",
                "configured": True,
                "automatic": False,
                "read_only": True,
                "checked_at": now,
            },
            {
                "id": "monitor_webhook",
                "name": "监控系统自动告警",
                "role": "让已有监控平台把告警送入机鉴",
                "state": "available",
                "message": "通用告警接口可用；需要监控管理员完成发送配置",
                "configured": True,
                "automatic": True,
                "read_only": True,
                "checked_at": now,
            },
        ]
        if check_external:
            built_in.extend([self.signoz.check(), self.holmes.check()])
        else:
            built_in.extend(
                [
                    _status(
                        "signoz", "SigNoz 监控底座", "保存并查询真实日志、指标和告警",
                        "configured" if self.signoz.configured else "not_configured",
                        "已配置，等待连接检查" if self.signoz.configured else "尚未填写 SigNoz 地址",
                        self.signoz.configured, True,
                    ),
                    _status(
                        "holmes", "HolmesGPT AI 调查", "调用只读工具补充调查，并返回工具记录",
                        "configured" if self.holmes.configured else "not_configured",
                        "已配置，等待连接检查" if self.holmes.configured else "尚未填写 HolmesGPT 地址",
                        self.holmes.configured, True,
                    ),
                ]
            )
        built_in.extend(
            [
                _status(
                    "snmp_redfish",
                    "交换机与 BMC 采集",
                    "通过 SNMP、Redfish 或现有网络监控获取设备状态",
                    "planned",
                    "本轮保留接口，尚未连接真实设备",
                    False,
                    True,
                ),
                _status(
                    "asset_system",
                    "OMS / CMDB 资产信息",
                    "核对完整 SN、机架位、设备名和备件变化",
                    "planned",
                    "需要客户提供只读接口后才能连接",
                    False,
                    True,
                ),
            ]
        )
        return built_in

    def connected_collectors(self) -> List[str]:
        return [
            item["id"]
            for item in self.source_statuses(check_external=True)
            if item.get("state") == "connected"
        ]

    def investigate(self, incident: Mapping[str, Any]) -> List[Dict[str, Any]]:
        signoz_result = self.signoz.query_telemetry(incident)
        holmes_observation = {
            "provider": signoz_result.get("provider"),
            "state": signoz_result.get("state"),
            "message": signoz_result.get("message"),
            "query": signoz_result.get("query", {}),
            "records": [
                {"text": item.get("text", "")}
                for item in signoz_result.get("records", [])[: self.settings.max_records]
                if isinstance(item, Mapping)
            ],
            "metrics": [
                {"text": item.get("text", "")}
                for item in signoz_result.get("metrics", [])[: self.settings.max_records]
                if isinstance(item, Mapping)
            ],
        }
        holmes_result = self.holmes.investigate(incident, [holmes_observation])
        return [signoz_result, holmes_result]
