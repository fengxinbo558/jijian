"""Immutable OMS snapshots and guarded onsite operation workflow."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Mapping, Optional

from .models import utc_now
from .store import IncidentStore, _dump, _load


FINAL_STATES = {"completed_success", "completed_failed"}


class OperationService:
    """Keep device identity, operation permission and human review as separate gates."""

    def __init__(self, store: IncidentStore) -> None:
        self.store = store

    def import_work_order(self, payload: Mapping[str, Any], actor: str) -> Dict[str, Any]:
        order_no = str(payload.get("order_no") or "").strip()
        target_sn = str(payload.get("target_sn") or payload.get("sn") or "").strip()
        rack_position = str(payload.get("rack_position") or "").strip()
        if not order_no or not target_sn or not rack_position:
            raise ValueError("工单号、完整 SN 和机架位不能为空")
        now = utc_now()
        snapshot_id = "WOS-" + uuid.uuid4().hex[:12].upper()
        operation_id = "OP-" + uuid.uuid4().hex[:12].upper()
        power_policy = str(payload.get("power_policy") or "needs_confirmation").strip()
        if power_policy not in {"allowed", "needs_confirmation", "forbidden"}:
            raise ValueError("操作许可必须是 allowed、needs_confirmation 或 forbidden")
        permission = power_policy
        snapshot = {
            "id": snapshot_id,
            "order_no": order_no,
            "incident_id": str(payload.get("incident_id") or ""),
            "source": str(payload.get("source") or "manual_oms_import"),
            "site": str(payload.get("site") or "").strip().upper(),
            "target_sn": target_sn,
            "rack_position": rack_position,
            "device_name": str(payload.get("device_name") or "").strip(),
            "operation_type": str(payload.get("operation_type") or "inspect").strip(),
            "urgency": str(payload.get("urgency") or "normal").strip(),
            "from_reinstall": str(payload.get("from_reinstall") or "unknown").strip(),
            "power_policy": power_policy,
            "payload": dict(payload),
            "imported_by": actor,
            "created_at": now,
        }
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO work_order_snapshots (
                    id, order_no, incident_id, source, site, target_sn,
                    rack_position, device_name, operation_type, urgency,
                    from_reinstall, power_policy, payload_json, imported_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    snapshot["order_no"],
                    snapshot["incident_id"],
                    snapshot["source"],
                    snapshot["site"],
                    snapshot["target_sn"],
                    snapshot["rack_position"],
                    snapshot["device_name"],
                    snapshot["operation_type"],
                    snapshot["urgency"],
                    snapshot["from_reinstall"],
                    snapshot["power_policy"],
                    _dump(snapshot["payload"]),
                    snapshot["imported_by"],
                    snapshot["created_at"],
                ),
            )
            connection.execute(
                """
                INSERT INTO operation_cases (
                    id, work_order_snapshot_id, incident_id, status, operator,
                    identity_status, permission_status, observed_sn, scan_method,
                    review_status, review_mode, reviewer, result_status,
                    result_reason, result_details, online_sn, offline_sn,
                    timeout_reason, created_at, updated_at
                ) VALUES (?, ?, ?, 'awaiting_identity', '', 'unverified', ?, '', '',
                          'pending', '', '', '', '', '', '', '', '', ?, ?)
                """,
                (operation_id, snapshot_id, snapshot["incident_id"], permission, now, now),
            )
            self._history(
                connection,
                operation_id,
                "work_order_imported",
                actor,
                "",
                "awaiting_identity",
                {"snapshot_id": snapshot_id, "order_no": order_no},
            )
        result = self.get(operation_id)
        assert result is not None
        return result

    def verify_identity(
        self, operation_id: str, payload: Mapping[str, Any], actor: str
    ) -> Dict[str, Any]:
        observed = str(payload.get("observed_sn") or "").strip()
        method = str(payload.get("method") or "manual").strip()
        if not observed:
            raise ValueError("必须扫码、识别或输入完整 SN")
        if method not in {"barcode", "qr", "ocr", "manual"}:
            raise ValueError("不支持的 SN 获取方式")
        current = self._required(operation_id)
        if current["status"] in FINAL_STATES or current["status"] == "operating":
            raise ValueError("当前状态不能重新核对设备身份")
        expected = str(current["work_order"]["target_sn"])
        matched = observed == expected
        identity = "confirmed" if matched else "mismatch"
        if not matched:
            next_status = "blocked_identity"
        elif current["permission_status"] == "forbidden":
            next_status = "blocked_permission"
        elif current["permission_status"] == "allowed":
            next_status = "awaiting_review"
        else:
            next_status = "awaiting_permission"
        with self.store.connect() as connection:
            connection.execute(
                """
                UPDATE operation_cases SET operator = ?, identity_status = ?,
                    observed_sn = ?, scan_method = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (actor, identity, observed, method, next_status, utc_now(), operation_id),
            )
            self._history(
                connection,
                operation_id,
                "identity_checked",
                actor,
                current["status"],
                next_status,
                {"method": method, "matched": matched, "observed_sn": observed},
            )
        return self._required(operation_id)

    def set_permission(
        self, operation_id: str, decision: str, actor: str, reason: str = ""
    ) -> Dict[str, Any]:
        if decision not in {"allowed", "needs_confirmation", "forbidden"}:
            raise ValueError("不支持的操作许可结论")
        current = self._required(operation_id)
        if current["status"] in FINAL_STATES or current["status"] == "operating":
            raise ValueError("当前状态不能修改操作许可")
        if current["identity_status"] == "mismatch":
            next_status = "blocked_identity"
        elif current["identity_status"] != "confirmed":
            next_status = "awaiting_identity"
        elif decision == "allowed":
            next_status = "awaiting_review"
        elif decision == "forbidden":
            next_status = "blocked_permission"
        else:
            next_status = "awaiting_permission"
        now = utc_now()
        with self.store.connect() as connection:
            connection.execute(
                "INSERT INTO operation_permissions (operation_id, decision, decided_by, reason, created_at) VALUES (?, ?, ?, ?, ?)",
                (operation_id, decision, actor, reason, now),
            )
            connection.execute(
                "UPDATE operation_cases SET permission_status = ?, status = ?, updated_at = ? WHERE id = ?",
                (decision, next_status, now, operation_id),
            )
            self._history(
                connection,
                operation_id,
                "permission_decided",
                actor,
                current["status"],
                next_status,
                {"decision": decision, "reason": reason},
            )
        return self._required(operation_id)

    def review(
        self, operation_id: str, payload: Mapping[str, Any], actor: str
    ) -> Dict[str, Any]:
        current = self._required(operation_id)
        decision = str(payload.get("decision") or "").strip()
        review_mode = str(payload.get("review_mode") or "").strip()
        if decision not in {"approved", "rejected"}:
            raise ValueError("复核结论必须是 approved 或 rejected")
        if review_mode not in {"onsite_peer", "remote_authorized"}:
            raise ValueError("复核方式必须是现场同岗或授权远程复核")
        if not actor or actor == current["operator"]:
            raise ValueError("复核人不能与现场操作人相同")
        if current["identity_status"] != "confirmed":
            raise ValueError("设备身份未确认，不能复核通过")
        if current["permission_status"] != "allowed":
            raise ValueError("操作许可未确认，不能复核通过")
        next_status = "ready" if decision == "approved" else "blocked_review"
        now = utc_now()
        work_order = current["work_order"]
        with self.store.connect() as connection:
            connection.execute(
                """
                INSERT INTO operation_reviews (
                    operation_id, reviewer, decision, review_mode, expected_sn,
                    observed_sn, rack_position, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    actor,
                    decision,
                    review_mode,
                    work_order["target_sn"],
                    current["observed_sn"],
                    work_order["rack_position"],
                    str(payload.get("note") or ""),
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE operation_cases SET review_status = ?, review_mode = ?,
                    reviewer = ?, status = ?, updated_at = ? WHERE id = ?
                """,
                (decision, review_mode, actor, next_status, now, operation_id),
            )
            self._history(
                connection,
                operation_id,
                "human_reviewed",
                actor,
                current["status"],
                next_status,
                {"decision": decision, "review_mode": review_mode},
            )
        return self._required(operation_id)

    def start(self, operation_id: str, actor: str) -> Dict[str, Any]:
        current = self._required(operation_id)
        if current["status"] != "ready":
            raise ValueError("身份、操作许可和人工复核全部通过后才能开始操作")
        if current["operator"] and actor != current["operator"]:
            raise ValueError("只能由已核对身份的现场操作人开始操作")
        return self._transition(operation_id, current, "operation_started", actor, "operating", {})

    def complete(
        self, operation_id: str, payload: Mapping[str, Any], actor: str
    ) -> Dict[str, Any]:
        current = self._required(operation_id)
        if current["status"] != "operating":
            raise ValueError("只有操作中的工单才能结束")
        if current["operator"] and actor != current["operator"]:
            raise ValueError("只能由现场操作人提交操作结果")
        result = str(payload.get("result") or "").strip()
        if result not in {"success", "failed"}:
            raise ValueError("操作结果必须选择成功或失败")
        reason = str(payload.get("reason") or "").strip()
        details = str(payload.get("details") or "").strip()
        if not reason or not details:
            raise ValueError("结束操作必须选择原因并填写详细反馈")
        next_status = "completed_success" if result == "success" else "completed_failed"
        now = utc_now()
        with self.store.connect() as connection:
            connection.execute(
                """
                UPDATE operation_cases SET status = ?, result_status = ?,
                    result_reason = ?, result_details = ?, online_sn = ?,
                    offline_sn = ?, timeout_reason = ?, updated_at = ? WHERE id = ?
                """,
                (
                    next_status,
                    result,
                    reason,
                    details,
                    str(payload.get("online_sn") or "").strip(),
                    str(payload.get("offline_sn") or "").strip(),
                    str(payload.get("timeout_reason") or "").strip(),
                    now,
                    operation_id,
                ),
            )
            self._history(
                connection,
                operation_id,
                "operation_completed",
                actor,
                current["status"],
                next_status,
                {"result": result, "reason": reason, "details": details},
            )
        return self._required(operation_id)

    def _transition(
        self,
        operation_id: str,
        current: Mapping[str, Any],
        action: str,
        actor: str,
        next_status: str,
        details: Mapping[str, Any],
    ) -> Dict[str, Any]:
        now = utc_now()
        with self.store.connect() as connection:
            connection.execute(
                "UPDATE operation_cases SET status = ?, updated_at = ? WHERE id = ?",
                (next_status, now, operation_id),
            )
            self._history(
                connection,
                operation_id,
                action,
                actor,
                str(current["status"]),
                next_status,
                details,
            )
        return self._required(operation_id)

    def list(self, limit: int = 100) -> list:
        safe_limit = max(1, min(int(limit), 500))
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM operation_cases ORDER BY updated_at DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        return [self.get(str(row["id"])) for row in rows]

    def get(self, operation_id: str) -> Optional[Dict[str, Any]]:
        with self.store.connect() as connection:
            case = connection.execute(
                "SELECT * FROM operation_cases WHERE id = ?", (operation_id,)
            ).fetchone()
            if case is None:
                return None
            snapshot = connection.execute(
                "SELECT * FROM work_order_snapshots WHERE id = ?",
                (case["work_order_snapshot_id"],),
            ).fetchone()
            permissions = connection.execute(
                "SELECT * FROM operation_permissions WHERE operation_id = ? ORDER BY id",
                (operation_id,),
            ).fetchall()
            reviews = connection.execute(
                "SELECT * FROM operation_reviews WHERE operation_id = ? ORDER BY id",
                (operation_id,),
            ).fetchall()
            history = connection.execute(
                "SELECT * FROM operation_history WHERE operation_id = ? ORDER BY id",
                (operation_id,),
            ).fetchall()
        result = dict(case)
        work_order = dict(snapshot)
        work_order["payload"] = _load(work_order.pop("payload_json"), {})
        result["work_order"] = work_order
        result["permissions"] = [dict(row) for row in permissions]
        result["reviews"] = [dict(row) for row in reviews]
        result["history"] = [
            {**{key: row[key] for key in row.keys() if key != "details_json"}, "details": _load(row["details_json"], {})}
            for row in history
        ]
        result["gates"] = {
            "identity": result["identity_status"] == "confirmed",
            "permission": result["permission_status"] == "allowed",
            "human_review": result["review_status"] == "approved",
        }
        return result

    def _required(self, operation_id: str) -> Dict[str, Any]:
        value = self.get(operation_id)
        if value is None:
            raise ValueError("现场操作单不存在")
        return value

    @staticmethod
    def _history(
        connection: Any,
        operation_id: str,
        action: str,
        actor: str,
        from_status: str,
        to_status: str,
        details: Mapping[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO operation_history (
                operation_id, action, actor, from_status, to_status,
                details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (operation_id, action, actor, from_status, to_status, _dump(dict(details)), utc_now()),
        )
