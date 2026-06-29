#!/opt/hermes/.venv/bin/python
"""HTTP bridge for n8n/Z-API -> Hermes CLI.

Small, dependency-free server used by Alumínio JR to call Hermes from n8n.
It deliberately avoids the upstream dashboard/API-server routing confusion and
exposes two POST endpoints on the Render public port:

- POST /chat
  Body: {"mensagem":"Bom dia", "telefone":"5583..."}
  Response: {"ok": true, "resposta": "..."}

- POST /v1/chat/completions
  Minimal OpenAI-compatible wrapper for n8n HTTP nodes.

Authentication: Authorization: Bearer <HERMES_HTTP_BRIDGE_KEY>
Fallback: API_SERVER_KEY is accepted as the same secret if the bridge-specific
key is not set.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

HERMES_BIN = os.environ.get("HERMES_BIN", "/opt/hermes/.venv/bin/hermes")
HOST = os.environ.get("HERMES_HTTP_BRIDGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT") or os.environ.get("HERMES_HTTP_BRIDGE_PORT", "10000"))
TIMEOUT_SECONDS = int(os.environ.get("HERMES_HTTP_BRIDGE_TIMEOUT_SECONDS", "120"))
MAX_CHARS = int(os.environ.get("HERMES_HTTP_BRIDGE_MAX_CHARS", "4000"))
AUTH_KEY = os.environ.get("HERMES_HTTP_BRIDGE_KEY") or os.environ.get("API_SERVER_KEY") or ""
FALLBACK_TEXT = os.environ.get("HERMES_HTTP_BRIDGE_FALLBACK", "Não consegui consultar agora. Vou confirmar.")

# Serializes CLI calls. The Hermes CLI/session/log files are safer with one
# request at a time during the MVP.
CLI_LOCK = threading.Lock()
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
BOX_CHARS = "─━│┃┊┆┌┐└┘├┤┬┴┼╭╮╰╯═║"


def _json_response(handler: BaseHTTPRequestHandler, status: int, data: dict[str, Any]) -> None:
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _authorized(handler: BaseHTTPRequestHandler) -> bool:
    if not AUTH_KEY:
        # Refuse to expose the bridge publicly without a key.
        return False
    auth = handler.headers.get("Authorization") or ""
    return auth.strip() == f"Bearer {AUTH_KEY}"


def _extract_prompt_from_openai(body: dict[str, Any]) -> str:
    messages = body.get("messages") or []
    if not isinstance(messages, list):
        return ""
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: list[str] = []
                for part in content:
                    if isinstance(part, dict):
                        text = part.get("text")
                        if isinstance(text, str):
                            parts.append(text)
                return "\n".join(parts).strip()
    return ""


def _build_whatsapp_prompt(mensagem: str, telefone: str = "", nome: str = "") -> str:
    mensagem = (mensagem or "").strip()
    telefone = (telefone or "").strip()
    nome = (nome or "").strip()
    context_bits = []
    if telefone:
        context_bits.append(f"Telefone/identificador do WhatsApp: {telefone}")
    if nome:
        context_bits.append(f"Nome exibido no WhatsApp: {nome}")
    context = "\n".join(context_bits)
    return (
        "Você está respondendo um cliente da Alumínio JR pelo WhatsApp.\n"
        "Responda em português do Brasil, de forma curta, direta e natural.\n"
        "Se precisar consultar preço/produto, use as ferramentas/skills disponíveis.\n"
        "Não peça nome ou cidade no início se a pergunta for simples.\n"
        "\n"
        f"{context}\n"
        "Mensagem do cliente:\n"
        f"{mensagem}"
    ).strip()


def _clean_cli_output(stdout: str) -> str:
    text = ANSI_RE.sub("", stdout or "")
    lines = text.splitlines()

    # Prefer the assistant panel if the TUI-like output is present.
    in_panel = False
    panel_lines: list[str] = []
    for raw in lines:
        line = raw.rstrip()
        if "⚕ Hermes" in line or " Hermes " in line and any(ch in line for ch in "─━"):
            in_panel = True
            continue
        if in_panel:
            stripped = line.strip()
            if stripped.startswith("Resume this session") or stripped.startswith("Session:"):
                break
            # Stop at a pure divider after we already collected text.
            no_spaces = stripped.replace(" ", "")
            if panel_lines and no_spaces and all(ch in BOX_CHARS for ch in no_spaces):
                break
            cleaned = stripped.strip(BOX_CHARS).strip()
            if cleaned and not cleaned.startswith("📚 skill") and "skill" not in cleaned.lower():
                panel_lines.append(cleaned)

    candidate = "\n".join(panel_lines).strip()
    if candidate:
        return candidate

    # Fallback: remove common metadata/noise and return useful remaining text.
    useful: list[str] = []
    skip_prefixes = (
        "Query:",
        "Initializing agent",
        "Resume this session",
        "Session:",
        "Duration:",
        "Messages:",
        "root@",
    )
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            continue
        if any(stripped.startswith(prefix) for prefix in skip_prefixes):
            continue
        if stripped.replace(" ", "") and all(ch in BOX_CHARS for ch in stripped.replace(" ", "")):
            continue
        if "📚 skill" in stripped:
            continue
        cleaned = stripped.strip(BOX_CHARS).strip()
        if cleaned:
            useful.append(cleaned)
    return "\n".join(useful).strip()


def _ask_hermes(prompt: str) -> tuple[bool, str, str]:
    if not prompt.strip():
        return False, FALLBACK_TEXT, "empty prompt"
    prompt = prompt[:MAX_CHARS]
    cmd = [HERMES_BIN, "chat", "-q", prompt]
    env = os.environ.copy()
    env.setdefault("HERMES_HOME", "/opt/data")
    started = time.time()
    try:
        with CLI_LOCK:
            proc = subprocess.run(
                cmd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=TIMEOUT_SECONDS,
                env=env,
            )
    except subprocess.TimeoutExpired:
        return False, FALLBACK_TEXT, f"timeout after {TIMEOUT_SECONDS}s"
    except Exception as exc:
        return False, FALLBACK_TEXT, f"exception: {exc}"

    elapsed = time.time() - started
    output = _clean_cli_output(proc.stdout)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[-1200:]
        print(f"[hermes-http-bridge] CLI failed rc={proc.returncode}: {err}", file=sys.stderr, flush=True)
        return False, output or FALLBACK_TEXT, f"cli rc={proc.returncode}; elapsed={elapsed:.1f}s"
    if not output:
        err = (proc.stderr or "").strip()[-1200:]
        print(f"[hermes-http-bridge] empty output: {err}", file=sys.stderr, flush=True)
        return False, FALLBACK_TEXT, f"empty output; elapsed={elapsed:.1f}s"
    return True, output, f"elapsed={elapsed:.1f}s"


class Handler(BaseHTTPRequestHandler):
    server_version = "HermesHTTPBridge/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[hermes-http-bridge] {self.address_string()} - {fmt % args}", flush=True)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/healthz", "/api/status"}:
            _json_response(self, 200, {"ok": True, "service": "hermes-http-bridge"})
            return
        _json_response(self, 404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/chat", "/v1/chat/completions"}:
            _json_response(self, 404, {"ok": False, "error": "not found"})
            return
        if not _authorized(self):
            _json_response(self, 401, {"ok": False, "error": "unauthorized"})
            return

        body = _read_json(self)
        if self.path == "/v1/chat/completions":
            mensagem = _extract_prompt_from_openai(body)
            telefone = ""
            nome = ""
        else:
            mensagem = str(body.get("mensagem") or body.get("message") or body.get("text") or "")
            telefone = str(body.get("telefone") or body.get("phone") or body.get("identificador") or "")
            nome = str(body.get("nome") or body.get("name") or "")

        prompt = _build_whatsapp_prompt(mensagem, telefone=telefone, nome=nome)
        ok, resposta, detail = _ask_hermes(prompt)

        if self.path == "/v1/chat/completions":
            _json_response(
                self,
                200,
                {
                    "id": "hermes-http-bridge",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": "hermes-cli",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": resposta},
                            "finish_reason": "stop",
                        }
                    ],
                    "ok": ok,
                    "detail": detail,
                },
            )
            return

        _json_response(self, 200, {"ok": ok, "resposta": resposta, "detail": detail})


def main() -> int:
    if not AUTH_KEY:
        print(
            "[hermes-http-bridge] refusing to start: set HERMES_HTTP_BRIDGE_KEY or API_SERVER_KEY",
            file=sys.stderr,
            flush=True,
        )
        return 2
    print(f"[hermes-http-bridge] listening on {HOST}:{PORT}", flush=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
