#!/opt/hermes/.venv/bin/python
"""HTTP bridge for n8n/Z-API -> Alumínio JR assistant.

Base consistente para o chatbot:
- O banco/sistema JR decide se produto existe.
- O Hermes apenas conversa e repassa dados estruturados ao n8n.
- O n8n apenas entrega texto, imagem ou texto + imagens.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

HOST = os.environ.get("HERMES_HTTP_BRIDGE_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT") or os.environ.get("HERMES_HTTP_BRIDGE_PORT", "10000"))
AUTH_KEY = os.environ.get("HERMES_HTTP_BRIDGE_KEY") or os.environ.get("API_SERVER_KEY") or ""
ENGINE = os.environ.get("HERMES_HTTP_BRIDGE_ENGINE", "direct").strip().lower()
TIMEOUT_SECONDS = int(os.environ.get("HERMES_HTTP_BRIDGE_TIMEOUT_SECONDS", "120"))
MAX_CHARS = int(os.environ.get("HERMES_HTTP_BRIDGE_MAX_CHARS", "4000"))
FALLBACK_TEXT = os.environ.get("HERMES_HTTP_BRIDGE_FALLBACK", "Não consegui consultar agora. Vou confirmar.")
HERMES_BIN = os.environ.get("HERMES_BIN", "/opt/hermes/.venv/bin/hermes")
OPENAI_MODEL = os.environ.get("ALUMINIO_JR_OPENAI_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-5-mini"
OPENAI_BASE_URL = (os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
ALUMINIO_BASE_URL = (os.environ.get("ALUMINIO_JR_API_BASE_URL") or "").rstrip("/")
ASSISTENTE_API_TOKEN = os.environ.get("ASSISTENTE_API_TOKEN") or ""
CATALOGO_RESOLVER_PATH = os.environ.get("ALUMINIO_JR_CATALOGO_RESOLVER_PATH", "/api/assistente/catalogo/resolver")
CONVERSA_TTL_SECONDS = int(os.environ.get("HERMES_CONVERSA_TTL_SECONDS", "1800"))

CLI_LOCK = threading.Lock()
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
CONVERSAS: dict[str, dict[str, Any]] = {}

SAUDACOES = [
    (re.compile(r"^\s*bom\s+dia[!.\s]*$", re.IGNORECASE), "Bom dia. Em que posso te ajudar?"),
    (re.compile(r"^\s*boa\s+tarde[!.\s]*$", re.IGNORECASE), "Boa tarde. Em que posso te ajudar?"),
    (re.compile(r"^\s*boa\s+noite[!.\s]*$", re.IGNORECASE), "Boa noite. Em que posso te ajudar?"),
    (re.compile(r"^\s*(oi|ol[áa]|opa)[!.\s]*$", re.IGNORECASE), "Olá. Em que posso te ajudar?"),
]


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
        return False
    auth = handler.headers.get("Authorization") or ""
    return auth.strip() == f"Bearer {AUTH_KEY}"


def _telefone_key(telefone: str) -> str:
    raw = str(telefone or "").strip()
    digits = re.sub(r"\D", "", raw)
    return digits or raw


def _get_contexto(telefone: str) -> dict[str, Any]:
    key = _telefone_key(telefone)
    if not key:
        return {}
    ctx = CONVERSAS.get(key)
    if not ctx:
        return {}
    if time.time() - float(ctx.get("ts", 0)) > CONVERSA_TTL_SECONDS:
        CONVERSAS.pop(key, None)
        return {}
    data = ctx.get("data")
    return data if isinstance(data, dict) else {}


def _salvar_contexto(telefone: str, contexto: dict[str, Any] | None) -> None:
    key = _telefone_key(telefone)
    if not key or not isinstance(contexto, dict):
        return
    CONVERSAS[key] = {"ts": time.time(), "data": contexto}


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
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        parts.append(part["text"])
                return "\n".join(parts).strip()
    return ""


def _norm(texto: Any) -> str:
    text = str(texto or "")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _catalogo_api(texto: str, telefone: str = "") -> tuple[bool, str, str, dict[str, Any]]:
    if not ALUMINIO_BASE_URL or not ASSISTENTE_API_TOKEN:
        return False, FALLBACK_TEXT, "catalog api env missing", {"tipoResposta": "texto"}

    payload = {
        "mensagem": texto,
        "telefone": telefone,
        "contexto": _get_contexto(telefone),
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{ALUMINIO_BASE_URL}{CATALOGO_RESOLVER_PATH}",
        data=raw,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ASSISTENTE_API_TOKEN}",
            "User-Agent": "hermes-http-bridge-aluminio-jr/3.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-500:]
        return False, FALLBACK_TEXT, f"catalog api http {exc.code}: {detail}", {"tipoResposta": "texto"}
    except Exception as exc:
        return False, FALLBACK_TEXT, f"catalog api error: {exc}", {"tipoResposta": "texto"}

    if not isinstance(data, dict):
        return False, FALLBACK_TEXT, "catalog api invalid json", {"tipoResposta": "texto"}

    if data.get("contextoAtualizado"):
        _salvar_contexto(telefone, data.get("contextoAtualizado"))

    if data.get("tratado") is not True:
        return False, "", "catalog ignored", {"tipoResposta": "texto", "catalogoIgnorado": True}

    resposta = str(data.get("resposta") or data.get("mensagem") or "").strip() or FALLBACK_TEXT
    produtos = data.get("produtos") if isinstance(data.get("produtos"), list) else []
    produtos_com_foto = [p for p in produtos if isinstance(p, dict) and p.get("foto")]

    extra = {
        "tipoResposta": "produtos" if produtos_com_foto else "texto",
        "enviarComoImagem": bool(produtos_com_foto and data.get("enviarComoImagem") is not False),
        "enviarTextoAntesProdutos": bool(data.get("enviarTextoAntesProdutos") is True and str(data.get("textoAntesProdutos") or "").strip()),
        "textoAntesProdutos": str(data.get("textoAntesProdutos") or "").strip(),
        "produtos": produtos,
        "matchExatoProduto": data.get("matchExatoProduto"),
        "filtrosFaltantes": data.get("filtrosFaltantes") if isinstance(data.get("filtrosFaltantes"), list) else [],
        "produtoBase": data.get("produtoBase"),
        "produtoBaseNome": data.get("produtoBaseNome"),
        "atributosDetectados": data.get("atributosDetectados") if isinstance(data.get("atributosDetectados"), dict) else {},
    }

    return True, resposta, "catalog resolver", extra


def _openai_extract_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return data["output_text"].strip()
    output = data.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        text = part.get("text") or part.get("output_text")
                        if isinstance(text, str):
                            parts.append(text)
        if parts:
            return "\n".join(parts).strip()
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(msg, dict) and isinstance(msg.get("content"), str):
            return msg["content"].strip()
    return ""


def _openai_call(texto: str, telefone: str = "", nome: str = "") -> tuple[bool, str, str, dict[str, Any]]:
    api_key = os.environ.get("OPENAI_API_KEY") or ""
    if not api_key:
        return False, FALLBACK_TEXT, "OPENAI_API_KEY missing", {"tipoResposta": "texto"}

    system = (
        "Você é o assistente de atendimento da Alumínio JR no WhatsApp. "
        "Responda em português do Brasil, de forma curta, direta e natural. "
        "Use no máximo 2 frases. Faça no máximo 1 pergunta. "
        "Não invente preço, prazo, estoque, pagamento, pedido ou desconto. "
        "Se o assunto for catálogo, produto, preço ou disponibilidade, diga que vai consultar."
    )
    user = (texto or "").strip()[:MAX_CHARS]

    responses_payload = {
        "model": OPENAI_MODEL,
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_output_tokens": 180,
    }
    first_error = ""
    try:
        req = urllib.request.Request(
            f"{OPENAI_BASE_URL}/responses",
            data=json.dumps(responses_payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = _openai_extract_text(data)
        if text:
            return True, text, "openai responses", {"tipoResposta": "texto"}
    except Exception as exc:
        first_error = str(exc)

    chat_payload = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_completion_tokens": 180,
    }
    try:
        req = urllib.request.Request(
            f"{OPENAI_BASE_URL}/chat/completions",
            data=json.dumps(chat_payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        text = _openai_extract_text(data)
        if text:
            return True, text, "openai chat.completions", {"tipoResposta": "texto"}
    except Exception as exc:
        return False, FALLBACK_TEXT, f"openai error: responses={first_error}; chat={exc}", {"tipoResposta": "texto"}

    return False, FALLBACK_TEXT, "openai empty output", {"tipoResposta": "texto"}


def _clean_cli_output(stdout: str) -> str:
    text = ANSI_RE.sub("", stdout or "")
    lines = [ln.strip() for ln in text.splitlines()]
    useful: list[str] = []
    for line in lines:
        if not line:
            continue
        if line.startswith(("Query:", "Initializing agent", "Resume this session", "Session:", "Duration:", "Messages:")):
            continue
        if "Available Tools" in line or "MCP Servers" in line:
            continue
        useful.append(line)
    return "\n".join(useful).strip()


def _cli_call(texto: str, telefone: str = "", nome: str = "") -> tuple[bool, str, str, dict[str, Any]]:
    prompt = (
        "Você está respondendo um cliente da Alumínio JR pelo WhatsApp. "
        "Responda curto e direto. Não invente dados de catálogo.\n\n"
        f"Telefone: {telefone}\n"
        f"Mensagem do cliente: {texto}"
    )[:MAX_CHARS]
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
                stdin=subprocess.DEVNULL,
                timeout=TIMEOUT_SECONDS,
                env=env,
            )
    except subprocess.TimeoutExpired:
        return False, FALLBACK_TEXT, f"cli timeout after {TIMEOUT_SECONDS}s", {"tipoResposta": "texto"}
    except Exception as exc:
        return False, FALLBACK_TEXT, f"cli exception: {exc}", {"tipoResposta": "texto"}
    out = _clean_cli_output(proc.stdout or "")
    elapsed = time.time() - started
    if proc.returncode != 0:
        return False, out or FALLBACK_TEXT, f"cli rc={proc.returncode}; elapsed={elapsed:.1f}s", {"tipoResposta": "texto"}
    if not out or ("Available Tools" in out and len(out) > 800):
        return False, FALLBACK_TEXT, f"cli startup/empty; elapsed={elapsed:.1f}s", {"tipoResposta": "texto"}
    return True, out, f"cli elapsed={elapsed:.1f}s", {"tipoResposta": "texto"}


def _answer(texto: str, telefone: str = "", nome: str = "") -> tuple[bool, str, str, dict[str, Any]]:
    texto = (texto or "").strip()
    if not texto:
        return False, FALLBACK_TEXT, "empty message", {"tipoResposta": "texto"}

    if ENGINE == "cli":
        return _cli_call(texto, telefone, nome)

    for regex, resposta in SAUDACOES:
        if regex.match(texto):
            return True, resposta, "direct greeting", {"tipoResposta": "texto"}

    # Primeiro consulta o resolvedor do sistema JR. Ele decide, com base no banco,
    # se a frase é de catálogo e se o produto existe. Se não for catálogo, ignora.
    ok_catalogo, resposta_catalogo, detail_catalogo, extra_catalogo = _catalogo_api(texto, telefone=telefone)
    if extra_catalogo.get("catalogoIgnorado") is not True:
        return ok_catalogo, resposta_catalogo, detail_catalogo, extra_catalogo

    ok, resposta, detail, extra = _openai_call(texto, telefone, nome)
    extra.setdefault("tipoResposta", "texto")
    return ok, resposta, detail, extra


class Handler(BaseHTTPRequestHandler):
    server_version = "HermesHTTPBridge/3.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[hermes-http-bridge] {self.address_string()} - {fmt % args}", flush=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self.send_response(200)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/healthz", "/api/status"}:
            _json_response(self, 200, {"ok": True, "service": "hermes-http-bridge", "engine": ENGINE, "version": "3.0"})
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

        started = time.time()
        ok, resposta, detail, extra = _answer(mensagem, telefone=telefone, nome=nome)
        detail = f"{detail}; elapsed={time.time() - started:.1f}s"

        if self.path == "/v1/chat/completions":
            _json_response(
                self,
                200,
                {
                    "id": "hermes-http-bridge",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": f"hermes-http-bridge/{ENGINE}",
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

        payload = {"ok": ok, "resposta": resposta, "detail": detail}
        if extra:
            payload.update(extra)
        _json_response(self, 200, payload)


def main() -> int:
    if not AUTH_KEY:
        print("[hermes-http-bridge] refusing to start: set HERMES_HTTP_BRIDGE_KEY", file=sys.stderr, flush=True)
        return 2
    print(f"[hermes-http-bridge] listening on {HOST}:{PORT} engine={ENGINE} version=3.0", flush=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
