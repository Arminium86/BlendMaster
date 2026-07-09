import argparse
import json
import os
import threading
import uuid
from datetime import datetime

from flask import Flask, jsonify, request


PROTOCOL_VERSION = "2025-06-18"


class BridgeStore:
    def __init__(self, store_file):
        self.store_file = store_file
        self.lock = threading.Lock()
        self.state = {
            "requests": {},
            "latest_request_id": None,
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.load()

    def load(self):
        if not self.store_file or not os.path.exists(self.store_file):
            return
        try:
            with open(self.store_file, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                self.state.update(loaded)
        except Exception:
            pass

    def save(self):
        if not self.store_file:
            return
        os.makedirs(os.path.dirname(self.store_file), exist_ok=True)
        tmp_file = f"{self.store_file}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as handle:
            json.dump(self.state, handle, indent=2, default=str)
        os.replace(tmp_file, self.store_file)

    def create_request(self, payload):
        with self.lock:
            request_id = str(uuid.uuid4())
            item = {
                "request_id": request_id,
                "status": "pending",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "updated_at": datetime.now().isoformat(timespec="seconds"),
                "story": payload.get("story", ""),
                "instructions": payload.get("instructions", ""),
                "context": payload.get("context", {}),
                "trace": [
                    "Request created by BlendMaster.",
                    "Waiting for Codex to collect it through the BlendMaster MCP bridge.",
                ],
                "result": None,
            }
            self.state["requests"][request_id] = item
            self.state["latest_request_id"] = request_id
            self.save()
            return item

    def latest_request(self, only_pending=False):
        with self.lock:
            request_id = self.state.get("latest_request_id")
            if not request_id:
                return None
            item = self.state.get("requests", {}).get(request_id)
            if only_pending and item and item.get("status") != "pending":
                pending = [
                    req
                    for req in self.state.get("requests", {}).values()
                    if req.get("status") == "pending"
                ]
                pending.sort(key=lambda req: req.get("created_at", ""))
                item = pending[-1] if pending else None
            return item

    def get_request(self, request_id):
        with self.lock:
            return self.state.get("requests", {}).get(request_id)

    def append_trace(self, request_id, message):
        with self.lock:
            item = self.state.get("requests", {}).get(request_id)
            if not item:
                return None
            item.setdefault("trace", []).append(str(message))
            item["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self.save()
            return item

    def submit_result(self, request_id, result, status="completed"):
        with self.lock:
            item = self.state.get("requests", {}).get(request_id)
            if not item:
                return None
            item["status"] = status
            item["result"] = result
            item["updated_at"] = datetime.now().isoformat(timespec="seconds")
            item.setdefault("trace", []).append(f"Codex submitted result with status '{status}'.")
            self.save()
            return item

    def status(self):
        with self.lock:
            requests = self.state.get("requests", {})
            pending_count = sum(1 for item in requests.values() if item.get("status") == "pending")
            return {
                "started_at": self.state.get("started_at"),
                "latest_request_id": self.state.get("latest_request_id"),
                "request_count": len(requests),
                "pending_count": pending_count,
            }


def json_rpc_result(message_id, result):
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def json_rpc_error(message_id, code, message):
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def text_tool_result(value, is_error=False):
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, indent=2, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": bool(is_error)}


def create_app(store):
    app = Flask(__name__)

    tools = [
        {
            "name": "get_pending_blendmaster_agent_request",
            "title": "Get Pending BlendMaster Request",
            "description": (
                "Return the latest pending BlendMaster blending request. Use this to read the user's "
                "Blending Story, Run Instructions, and current app context before proposing constraints."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "submit_blendmaster_agent_result",
            "title": "Submit BlendMaster Agent Result",
            "description": (
                "Submit proposed BlendMaster constraints and run commentary for a request. For guided UI "
                "application, return broad workflow sections such as site_configuration, selected_stockpiles, "
                "selected_amt_stockpiles, amt_chunking, solver_config, and calendar_rates. If any selected "
                "stockpile is AMT, hex_sequence_table is required so the app can load the chunked AMT map "
                "and submit the chunks. Project_file/project_state remains supported for complete restore."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": ["completed", "failed"],
                        "default": "completed",
                    },
                    "result": {
                        "type": "object",
                        "description": (
                            "Preferred guided UI result: {'summary': str, 'messages': [str], "
                            "'site_configuration': {...}, 'selected_stockpiles': [...], "
                            "'selected_amt_stockpiles': [...], 'amt_chunking': {...}, "
                            "'hex_sequence_table': [{...chunk rows...}], 'solver_config': {...}, "
                            "'calendar_rates': {...}}. hex_sequence_table is mandatory when AMT stockpiles "
                            "are selected. AMT chunk rows must include footprint, sequence, hex chunk id, "
                            "balance/tonnes, weighted grades, hex_count, chunk_size, and member_hexes; do not "
                            "return placeholder rows with only footprint/sequence/chunk_id. If the full table is "
                            "large, return hex_sequence_table_file pointing to a JSON file with complete rows. "
                            "Complete restore result: {'project_file': 'C:/.../run.prj'} or "
                            "{'project_state': {...saved .prj fields...}}. Fallback: "
                            "{'proposed_constraints': [{'target': str, 'value': any, 'rationale': str}]}."
                        ),
                    },
                },
                "required": ["request_id", "result"],
                "additionalProperties": False,
            },
        },
        {
            "name": "append_blendmaster_agent_trace",
            "title": "Append BlendMaster Agent Trace",
            "description": "Append a short status message to the BlendMaster Agent Instructions console.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string"},
                    "message": {"type": "string"},
                },
                "required": ["request_id", "message"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_blendmaster_bridge_status",
            "title": "Get BlendMaster Bridge Status",
            "description": "Return bridge health and request counts.",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    ]

    @app.get("/")
    def index():
        status = store.status()
        return (
            "<!doctype html>"
            "<html><head><title>BlendMaster Agent Bridge</title>"
            "<style>"
            "body{font-family:Segoe UI,Arial,sans-serif;margin:32px;color:#172033;background:#f8fafc;}"
            ".card{max-width:860px;background:white;border:1px solid #d8e0ea;border-radius:8px;padding:22px;}"
            "code{background:#eef2f7;border-radius:4px;padding:2px 5px;}"
            ".ok{color:#047857;font-weight:700;}"
            "</style></head><body><div class='card'>"
            "<h1>BlendMaster Agent Bridge</h1>"
            "<p class='ok'>Bridge is running.</p>"
            "<p>This is the local bridge launched by BlendMaster. The MCP endpoint is "
            "<code>/mcp</code>, but it is not a normal browser page. Connect Codex to it "
            "as a <b>Streamable HTTP</b> MCP server.</p>"
            "<p>Use <code>/health</code> for a browser-readable status check.</p>"
            f"<p>Latest request: <code>{status.get('latest_request_id') or 'None'}</code></p>"
            f"<p>Pending requests: <code>{status.get('pending_count')}</code></p>"
            "</div></body></html>"
        )

    @app.get("/health")
    def health():
        return jsonify({"ok": True, "status": store.status()})

    @app.post("/app/request")
    def app_create_request():
        payload = request.get_json(silent=True) or {}
        item = store.create_request(payload)
        return jsonify({"ok": True, "request": item})

    @app.get("/app/request/<request_id>")
    def app_get_request(request_id):
        item = store.get_request(request_id)
        if not item:
            return jsonify({"ok": False, "error": "request not found"}), 404
        return jsonify({"ok": True, "request": item})

    @app.get("/app/latest")
    def app_latest_request():
        item = store.latest_request()
        return jsonify({"ok": True, "request": item})

    @app.post("/app/cancel/<request_id>")
    def app_cancel_request(request_id):
        result = {"summary": "Request cancelled by BlendMaster.", "messages": []}
        item = store.submit_result(request_id, result, status="failed")
        if not item:
            return jsonify({"ok": False, "error": "request not found"}), 404
        return jsonify({"ok": True, "request": item})

    @app.post("/mcp")
    def mcp_post():
        message = request.get_json(silent=True)
        if not isinstance(message, dict):
            return jsonify(json_rpc_error(None, -32700, "Invalid JSON-RPC message")), 400

        method = message.get("method")
        message_id = message.get("id")
        params = message.get("params") or {}

        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "blendmaster-agent-bridge",
                    "title": "BlendMaster Agent Bridge",
                    "version": "0.1.0",
                },
                "instructions": (
                    "You are connected to BlendMaster. First call "
                    "get_pending_blendmaster_agent_request. Then reason over the story, "
                    "instructions, and context, and submit JSON proposals with "
                    "submit_blendmaster_agent_result."
                ),
            }
            response = jsonify(json_rpc_result(message_id, result))
            response.headers["Mcp-Session-Id"] = str(uuid.uuid4())
            return response

        if method == "notifications/initialized":
            return ("", 202)

        if method == "ping":
            return jsonify(json_rpc_result(message_id, {}))

        if method == "tools/list":
            return jsonify(json_rpc_result(message_id, {"tools": tools}))

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments") or {}

            if tool_name == "get_pending_blendmaster_agent_request":
                item = store.latest_request(only_pending=True)
                if not item:
                    return jsonify(json_rpc_result(message_id, text_tool_result({"pending": False})))
                store.append_trace(item["request_id"], "Codex collected the pending request.")
                return jsonify(json_rpc_result(message_id, text_tool_result({"pending": True, "request": item})))

            if tool_name == "submit_blendmaster_agent_result":
                request_id = arguments.get("request_id")
                result = arguments.get("result")
                status = arguments.get("status", "completed")
                if not request_id or not isinstance(result, dict):
                    return jsonify(json_rpc_result(message_id, text_tool_result("request_id and result are required.", True)))
                item = store.submit_result(request_id, result, status=status)
                if not item:
                    return jsonify(json_rpc_result(message_id, text_tool_result("request_id was not found.", True)))
                return jsonify(json_rpc_result(message_id, text_tool_result({"ok": True, "request_id": request_id})))

            if tool_name == "append_blendmaster_agent_trace":
                request_id = arguments.get("request_id")
                message_text = arguments.get("message", "")
                item = store.append_trace(request_id, message_text)
                if not item:
                    return jsonify(json_rpc_result(message_id, text_tool_result("request_id was not found.", True)))
                return jsonify(json_rpc_result(message_id, text_tool_result({"ok": True})))

            if tool_name == "get_blendmaster_bridge_status":
                return jsonify(json_rpc_result(message_id, text_tool_result(store.status())))

            return jsonify(json_rpc_error(message_id, -32602, f"Unknown tool: {tool_name}"))

        if message_id is None:
            return ("", 202)

        return jsonify(json_rpc_error(message_id, -32601, f"Unsupported method: {method}"))

    @app.get("/mcp")
    def mcp_get():
        accept_header = request.headers.get("Accept", "")
        if "text/event-stream" not in accept_header:
            status = store.status()
            return (
                "<!doctype html>"
                "<html><head><title>BlendMaster MCP Endpoint</title>"
                "<style>"
                "body{font-family:Segoe UI,Arial,sans-serif;margin:32px;color:#172033;background:#f8fafc;}"
                ".card{max-width:860px;background:white;border:1px solid #d8e0ea;border-radius:8px;padding:22px;}"
                "code{background:#eef2f7;border-radius:4px;padding:2px 5px;}"
                ".ok{color:#047857;font-weight:700;}"
                "</style></head><body><div class='card'>"
                "<h1>BlendMaster MCP Endpoint</h1>"
                "<p class='ok'>Bridge is running.</p>"
                "<p>This endpoint expects MCP JSON-RPC <code>POST</code> requests from Codex. "
                "Opening it in a browser only confirms that the bridge exists.</p>"
                "<p>In Codex settings, create a custom MCP server using "
                "<b>Streamable HTTP</b> and this URL.</p>"
                f"<p>Latest request: <code>{status.get('latest_request_id') or 'None'}</code></p>"
                f"<p>Pending requests: <code>{status.get('pending_count')}</code></p>"
                "</div></body></html>"
            )
        return ("", 405)

    return app


def main():
    parser = argparse.ArgumentParser(description="BlendMaster local MCP bridge")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--store-file", required=True)
    args = parser.parse_args()

    store = BridgeStore(args.store_file)
    app = create_app(store)
    app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
