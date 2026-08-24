"""Visible model-provider adapters without exposing or persisting plaintext secrets."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlparse

from .models import utc_now
from .store import IncidentStore, _dump, _load


SEEDS = (
    {
        "provider_key": "local-openai-compatible",
        "display_name": "本地 OpenAI 兼容模型",
        "provider_type": "local",
        "config": {
            "endpoint": "http://127.0.0.1:8000/v1",
            "model": "",
            "data_residency": "local_machine_or_customer_datacenter",
            "timeout_seconds": 20,
            "description": "默认优先。本地接口未启动前不会声称已经调用模型。",
        },
    },
    {
        "provider_key": "private-partner-adapter",
        "display_name": "合作厂商私有化接口",
        "provider_type": "private_partner",
        "config": {
            "endpoint": "",
            "model": "",
            "data_residency": "customer_datacenter",
            "timeout_seconds": 20,
            "description": "为后续合作模型厂商保留；必须先确认接口契约和数据驻留。",
        },
    },
    {
        "provider_key": "authorized-cloud-adapter",
        "display_name": "经授权的云模型接口",
        "provider_type": "cloud",
        "config": {
            "endpoint": "",
            "model": "",
            "data_residency": "external_authorized_region",
            "timeout_seconds": 20,
            "description": "默认禁用。没有数据授权时不得发送原始日志、完整 SN 或内网地址。",
        },
    },
)


class ProviderRegistry:
    def __init__(self, store: IncidentStore) -> None:
        self.store = store

    def ensure_seeded(self) -> None:
        now = utc_now()
        with self.store.connect() as connection:
            for seed in SEEDS:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO model_providers (
                        provider_key, display_name, provider_type, enabled,
                        config_json, secret_configured, created_at, updated_at
                    ) VALUES (?, ?, ?, 0, ?, 0, ?, ?)
                    """,
                    (
                        seed["provider_key"],
                        seed["display_name"],
                        seed["provider_type"],
                        _dump(seed["config"]),
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO model_policies (
                        policy_key, provider_key, task_type, data_policy_json,
                        fallback_json, updated_at
                    ) VALUES (?, ?, 'incident_hypothesis', ?, ?, ?)
                    """,
                    (
                        f"{seed['provider_key']}:incident_hypothesis",
                        seed["provider_key"],
                        _dump(
                            {
                                "allowed": ["redacted_log_excerpt", "evidence_ids", "knowledge_card_ids"],
                                "blocked_without_explicit_authorization": [
                                    "raw_log",
                                    "full_sn",
                                    "internal_ip",
                                    "customer_identity",
                                    "business_payload",
                                ],
                            }
                        ),
                        _dump({"on_failure": "rules_and_local_knowledge", "cross_vendor_fallback": False}),
                        now,
                    ),
                )

    def list(self) -> list:
        with self.store.connect() as connection:
            rows = connection.execute(
                """
                SELECT provider_key FROM model_providers
                ORDER BY CASE provider_type
                    WHEN 'local' THEN 0
                    WHEN 'private_partner' THEN 1
                    ELSE 2
                END, provider_key
                """
            ).fetchall()
        return [self.get(str(row["provider_key"])) for row in rows]

    def get(self, provider_key: str) -> Optional[Dict[str, Any]]:
        with self.store.connect() as connection:
            provider = connection.execute(
                "SELECT * FROM model_providers WHERE provider_key = ?", (provider_key,)
            ).fetchone()
            if provider is None:
                return None
            policy_rows = connection.execute(
                "SELECT * FROM model_policies WHERE provider_key = ? ORDER BY policy_key",
                (provider_key,),
            ).fetchall()
        result = dict(provider)
        result["enabled"] = bool(result["enabled"])
        result["secret_configured"] = bool(result["secret_configured"])
        result["config"] = _load(result.pop("config_json"), {})
        result["policies"] = [
            {
                "policy_key": row["policy_key"],
                "task_type": row["task_type"],
                "data_policy": _load(row["data_policy_json"], {}),
                "fallback": _load(row["fallback_json"], {}),
                "updated_at": row["updated_at"],
            }
            for row in policy_rows
        ]
        configured = bool(result["enabled"] and result["config"].get("endpoint"))
        result["connection_state"] = "configured_not_tested" if configured else "not_configured"
        return result

    def upsert(self, provider_key: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        key = str(provider_key or "").strip()
        if not key:
            raise ValueError("模型提供方标识不能为空")
        provider_type = str(payload.get("provider_type") or "private_partner").strip()
        if provider_type not in {"local", "private_partner", "cloud"}:
            raise ValueError("模型提供方类型无效")
        endpoint = str(payload.get("endpoint") or "").strip()
        if endpoint:
            parsed = urlparse(endpoint)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("模型接口地址必须是有效的 http 或 https URL")
            if parsed.username or parsed.password:
                raise ValueError("模型接口地址不能包含账号或密钥")
        config = {
            "endpoint": endpoint,
            "model": str(payload.get("model") or "").strip(),
            "data_residency": str(payload.get("data_residency") or "unknown").strip(),
            "timeout_seconds": max(1, min(int(payload.get("timeout_seconds") or 20), 120)),
            "description": str(payload.get("description") or "").strip(),
        }
        enabled = bool(payload.get("enabled"))
        secret_configured = bool(payload.get("secret_configured"))
        now = utc_now()
        with self.store.connect() as connection:
            existing = connection.execute(
                "SELECT created_at FROM model_providers WHERE provider_key = ?", (key,)
            ).fetchone()
            created_at = str(existing["created_at"]) if existing is not None else now
            connection.execute(
                """
                INSERT INTO model_providers (
                    provider_key, display_name, provider_type, enabled,
                    config_json, secret_configured, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_key) DO UPDATE SET
                    display_name=excluded.display_name,
                    provider_type=excluded.provider_type,
                    enabled=excluded.enabled,
                    config_json=excluded.config_json,
                    secret_configured=excluded.secret_configured,
                    updated_at=excluded.updated_at
                """,
                (
                    key,
                    str(payload.get("display_name") or key).strip(),
                    provider_type,
                    1 if enabled else 0,
                    _dump(config),
                    1 if secret_configured else 0,
                    created_at,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO model_policies (
                    policy_key, provider_key, task_type, data_policy_json,
                    fallback_json, updated_at
                ) VALUES (?, ?, 'incident_hypothesis', ?, ?, ?)
                """,
                (
                    f"{key}:incident_hypothesis",
                    key,
                    _dump(
                        {
                            "allowed": ["redacted_log_excerpt", "evidence_ids", "knowledge_card_ids"],
                            "blocked_without_explicit_authorization": ["raw_log", "full_sn", "internal_ip"],
                        }
                    ),
                    _dump({"on_failure": "rules_and_local_knowledge", "cross_vendor_fallback": False}),
                    now,
                ),
            )
        result = self.get(key)
        assert result is not None
        return result
