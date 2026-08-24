"""Zero-dependency HTTP API and static web server."""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from .auth import is_ai_admin, normalize_role
from .demo_cases import DEMO_CASES, list_demos
from .service import IncidentService
from .store import IncidentStore


LOG = logging.getLogger("idcops")
MAX_BODY_BYTES = 2 * 1024 * 1024


class APIError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class AppHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: Tuple[str, int],
        handler_class: Any,
        service: IncidentService,
        web_dir: Path,
    ) -> None:
        super().__init__(address, handler_class)
        self.service = service
        self.web_dir = web_dir.resolve()


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "IDCAIOps/0.4"

    @property
    def app(self) -> AppHTTPServer:
        return self.server  # type: ignore[return-value]

    def log_message(self, fmt: str, *args: Any) -> None:
        LOG.info("%s - %s", self.address_string(), fmt % args)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self._common_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers", "Content-Type, X-IDCAI-Role, X-IDCAI-User"
        )
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed_url = urlparse(self.path)
            path = parsed_url.path
            if path.startswith("/api/admin/"):
                self._require_admin()
            if path == "/api/admin/summary":
                self._json(HTTPStatus.OK, self.app.service.admin.summary())
            elif path == "/api/admin/records":
                query = parse_qs(parsed_url.query)
                record_type = query.get("type", ["incidents"])[0]
                search = query.get("q", [""])[0]
                limit = int(query.get("limit", ["100"])[0])
                self._json(
                    HTTPStatus.OK,
                    self.app.service.admin.list_records(record_type, search, limit),
                )
            elif path == "/api/admin/knowledge":
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.assets.list_knowledge()},
                )
            elif path.startswith("/api/admin/knowledge/"):
                card_id = unquote(path.removeprefix("/api/admin/knowledge/"))
                item = self.app.service.assets.get_knowledge(card_id)
                if item is None:
                    raise APIError(HTTPStatus.NOT_FOUND, "知识卡不存在")
                self._json(HTTPStatus.OK, item)
            elif path == "/api/admin/prompts":
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.assets.list_prompts()},
                )
            elif path.startswith("/api/admin/prompts/"):
                prompt_key = unquote(path.removeprefix("/api/admin/prompts/"))
                item = self.app.service.assets.get_prompt(prompt_key)
                if item is None:
                    raise APIError(HTTPStatus.NOT_FOUND, "提示词不存在")
                self._json(HTTPStatus.OK, item)
            elif path == "/api/admin/releases":
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.releases.list()},
                )
            elif path == "/api/admin/rag-runs":
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.rag_traces.list()},
                )
            elif path.startswith("/api/admin/rag-runs/"):
                run_id = unquote(path.removeprefix("/api/admin/rag-runs/"))
                item = self.app.service.rag_traces.get(run_id)
                if item is None:
                    raise APIError(HTTPStatus.NOT_FOUND, "RAG运行记录不存在")
                self._json(HTTPStatus.OK, item)
            elif path == "/api/health":
                source_statuses = self.app.service.source_statuses(check_external=False)
                self._json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "service": "IDC AI 故障调查台",
                        "ai_enabled": self.app.service.ai.enabled,
                        "analysis_mode": "ai_enriched" if self.app.service.ai.enabled else "rules_only",
                        "collectors_connected": [
                            item["id"]
                            for item in source_statuses
                            if item.get("state") == "connected"
                        ],
                        "knowledge": self.app.service.knowledge.summary(),
                    },
                )
            elif path == "/api/sources":
                query = parse_qs(parsed_url.query)
                check_external = query.get("check", ["0"])[0] in {"1", "true", "yes"}
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.source_statuses(check_external=check_external)},
                )
            elif path == "/api/incidents":
                incidents = self.app.service.list_incidents()
                counts = {"new": 0, "processing": 0, "resolved": 0}
                for incident in incidents:
                    counts[incident["status"]] = counts.get(incident["status"], 0) + 1
                self._json(HTTPStatus.OK, {"items": incidents, "counts": counts})
            elif path == "/api/facilities":
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.list_facility_profiles()},
                )
            elif path.startswith("/api/incidents/"):
                incident_id = unquote(path.removeprefix("/api/incidents/"))
                incident = self.app.service.get_incident(incident_id)
                if incident is None:
                    raise APIError(HTTPStatus.NOT_FOUND, "事件不存在")
                self._json(HTTPStatus.OK, incident)
            elif path == "/api/demos":
                self._json(HTTPStatus.OK, {"items": list_demos()})
            elif path.startswith("/api/"):
                raise APIError(HTTPStatus.NOT_FOUND, "接口不存在")
            else:
                self._static(path)
        except APIError as exc:
            self._json(exc.status, {"error": exc.message})
        except Exception as exc:  # noqa: BLE001
            LOG.exception("GET failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"服务器处理失败：{exc}"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            path = urlparse(self.path).path
            payload = self._read_json()
            if path.startswith("/api/admin/"):
                self._require_admin()
            parts = [unquote(item) for item in path.strip("/").split("/")]
            if path == "/api/admin/annotations":
                result = self.app.service.admin.add_annotation(payload, self._actor())
                self._json(HTTPStatus.CREATED, result)
            elif len(parts) == 5 and parts[:3] == ["api", "admin", "prompts"] and parts[4] == "versions":
                result = self.app.service.assets.create_prompt_version(
                    parts[3], payload, self._actor()
                )
                self._json(HTTPStatus.CREATED, result)
            elif len(parts) == 5 and parts[:3] == ["api", "admin", "prompts"] and parts[4] == "preview":
                result = self.app.service.assets.preview_prompt(
                    parts[3],
                    str(payload.get("version") or ""),
                    payload.get("variables", {}) if isinstance(payload.get("variables"), dict) else {},
                )
                self._json(HTTPStatus.OK, result)
            elif len(parts) == 5 and parts[:3] == ["api", "admin", "knowledge"] and parts[4] == "versions":
                result = self.app.service.assets.create_knowledge_version(
                    parts[3], payload, self._actor()
                )
                self._json(HTTPStatus.CREATED, result)
            elif path == "/api/admin/releases/test":
                result = self.app.service.releases.test_asset(payload, self._actor())
                self._json(HTTPStatus.CREATED, result)
            elif len(parts) == 5 and parts[:3] == ["api", "admin", "releases"] and parts[4] == "prepare":
                self._json(HTTPStatus.OK, self.app.service.releases.prepare(parts[3]))
            elif len(parts) == 5 and parts[:3] == ["api", "admin", "releases"] and parts[4] == "publish":
                self._json(
                    HTTPStatus.OK,
                    self.app.service.releases.publish(
                        parts[3], bool(payload.get("confirmed_online")), self._actor()
                    ),
                )
            elif len(parts) == 5 and parts[:3] == ["api", "admin", "releases"] and parts[4] == "rollback":
                self._json(
                    HTTPStatus.OK,
                    self.app.service.releases.rollback(parts[3], self._actor()),
                )
            elif path == "/api/ingest/alert":
                incident = self.app.service.ingest("monitor", payload)
                self._json(HTTPStatus.CREATED, incident)
            elif path == "/api/ingest/signoz-alert":
                incidents = self.app.service.ingest_signoz_alert(payload)
                self._json(HTTPStatus.CREATED, {"incidents": incidents})
            elif path == "/api/ingest/log":
                incident = self.app.service.ingest("log", payload)
                self._json(HTTPStatus.CREATED, incident)
            elif path == "/api/ingest/onsite":
                incident = self.app.service.ingest("onsite", payload)
                self._json(HTTPStatus.CREATED, incident)
            elif path.startswith("/api/incidents/") and path.endswith("/status"):
                incident_id = unquote(path.removeprefix("/api/incidents/").removesuffix("/status"))
                incident = self.app.service.update_status(incident_id, str(payload.get("status", "")))
                if incident is None:
                    raise APIError(HTTPStatus.NOT_FOUND, "事件不存在")
                self._json(HTTPStatus.OK, incident)
            elif path.startswith("/api/incidents/") and path.endswith("/investigate"):
                incident_id = unquote(
                    path.removeprefix("/api/incidents/").removesuffix("/investigate")
                )
                incident = self.app.service.investigate_external(incident_id)
                if incident is None:
                    raise APIError(HTTPStatus.NOT_FOUND, "事件不存在")
                self._json(HTTPStatus.OK, incident)
            elif path == "/api/sources/check":
                self._json(
                    HTTPStatus.OK,
                    {"items": self.app.service.source_statuses(check_external=True)},
                )
            elif path == "/api/facilities":
                profile = self.app.service.upsert_facility_profile(payload)
                self._json(HTTPStatus.OK, profile)
            elif path.startswith("/api/demos/") and path.endswith("/run"):
                demo_id = unquote(path.removeprefix("/api/demos/").removesuffix("/run"))
                case = DEMO_CASES.get(demo_id)
                if case is None:
                    raise APIError(HTTPStatus.NOT_FOUND, "演练场景不存在")
                results = []
                for item in case["inputs"]:
                    demo_payload = dict(item)
                    source = str(demo_payload.pop("source"))
                    demo_payload["demo_id"] = demo_id
                    demo_payload["is_demo"] = True
                    results.append(self.app.service.ingest(source, demo_payload))
                unique = {item["id"]: item for item in results}
                self._json(
                    HTTPStatus.CREATED,
                    {"demo": demo_id, "incidents": list(unique.values())},
                )
            else:
                raise APIError(HTTPStatus.NOT_FOUND, "接口不存在")
        except APIError as exc:
            self._json(exc.status, {"error": exc.message})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            LOG.exception("POST failed")
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"服务器处理失败：{exc}"})

    def _read_json(self) -> Dict[str, Any]:
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise APIError(HTTPStatus.LENGTH_REQUIRED, "缺少 Content-Length")
        try:
            length = int(content_length)
        except ValueError as exc:
            raise APIError(HTTPStatus.BAD_REQUEST, "Content-Length 无效") from exc
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise APIError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "输入超过 2MB 限制")
        body = self.rfile.read(length)
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise APIError(HTTPStatus.BAD_REQUEST, "请求必须是有效 UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise APIError(HTTPStatus.BAD_REQUEST, "请求 JSON 必须是对象")
        return value

    def _static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else unquote(path).lstrip("/")
        target = (self.app.web_dir / relative).resolve()
        if self.app.web_dir not in target.parents and target != self.app.web_dir:
            raise APIError(HTTPStatus.FORBIDDEN, "禁止访问该路径")
        if not target.is_file():
            target = self.app.web_dir / "index.html"
        if not target.is_file():
            raise APIError(HTTPStatus.NOT_FOUND, "页面资源不存在")
        content = target.read_bytes()
        content_type, _encoding = mimetypes.guess_type(str(target))
        self.send_response(HTTPStatus.OK)
        self._common_headers()
        self.send_header("Content-Type", (content_type or "application/octet-stream") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _json(self, status: int, payload: Any) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._common_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _common_headers(self) -> None:
        if os.getenv("IDCAI_ALLOW_CORS", "0") == "1":
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")

    def _role(self) -> str:
        return normalize_role(
            self.headers.get("X-IDCAI-Role", os.getenv("IDCAI_DEFAULT_ROLE", "ai_admin"))
        )

    def _actor(self) -> str:
        return str(self.headers.get("X-IDCAI-User", "local-admin")).strip() or "local-admin"

    def _require_admin(self) -> None:
        if not is_ai_admin(self._role()):
            raise APIError(HTTPStatus.FORBIDDEN, "当前角色没有 AI 资产管理权限")


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    database: Optional[str] = None,
    web_dir: Optional[str] = None,
) -> AppHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"} and os.getenv("IDCAI_ALLOW_LAN", "0") != "1":
        raise ValueError("非本机监听需要显式设置 IDCAI_ALLOW_LAN=1")
    root = Path(__file__).resolve().parent.parent
    database_path = database or os.getenv("IDCAI_DATABASE", str(root / "data" / "incidents.db"))
    static_path = Path(web_dir) if web_dir else root / "web"
    service = IncidentService(IncidentStore(database_path))
    return AppHTTPServer((host, port), RequestHandler, service, static_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="IDC AI 故障调查与现场协同工作台")
    parser.add_argument("--host", default=os.getenv("IDCAI_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("IDCAI_PORT", "8765")))
    parser.add_argument("--database", default=os.getenv("IDCAI_DATABASE", ""))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = create_server(args.host, args.port, args.database or None)
    address, port = server.server_address[:2]
    print(f"IDC AI 故障调查台已启动：http://{address}:{port}")
    print("按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止…")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
