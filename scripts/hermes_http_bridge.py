#!/opt/hermes/.venv/bin/python
"""HTTP bridge for n8n/Z-API -> Alumínio JR assistant on Render.

This bridge is intentionally simple and stable for WhatsApp testing.
It exposes:

- GET  /healthz
- GET  /api/status
- POST /chat
- POST /v1/chat/completions  (minimal compatibility wrapper)

Authentication: Authorization: Bearer <HERMES_HTTP_BRIDGE_KEY>

Default engine is "direct": deterministic replies + Alumínio JR product API +
OpenAI fallback. Product queries now also return structured product cards so
n8n can send the same image+caption format used by the @preco flow.
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
MAX_PRODUCT_CARDS = int(os.environ.get("HERMES_MAX_PRODUCT_CARDS", "5"))

CLI_LOCK = threading.Lock()
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

PRODUTO_RE = re.compile(
    r"\b(pre[cç]o|valor|custa|quanto|or[cç]amento|tabela|produto|panela|press[aã]o|cafeteira|tampa|cabo|al[cç]a|v[áa]lvula|kit|caixa|jogo)\b",
    re.IGNORECASE,
)
SAUDACOES = [
    (re.compile(r"^\s*bom\s+dia[!.\s]*$", re.IGNORECASE), "Bom dia. Em que posso te ajudar?"),
    (re.compile(r"^\s*boa\s+tarde[!.\s]*$", re.IGNORECASE), "Boa tarde. Em que posso te ajudar?"),
    (re.compile(r"^\s*boa\s+noite[!.\s]*$", re.IGNORECASE), "Boa noite. Em que posso te ajudar?"),
    (re.compile(r"^\s*(oi|ol[áa]|opa)[!.\s]*$", re.IGNORECASE), "Olá. Em que posso te ajudar?"),
]

STOPWORDS_BUSCA = {
    "a", "o", "os", "as", "um", "uma", "de", "da", "do", "das", "dos", "para", "pra", "por", "com", "sem",
    "quanto", "custa", "custo", "valor", "preco", "preco", "preço", "tem", "qual", "quais", "me", "diga",
    "quero", "queria", "preciso", "comprar", "manda", "mande", "orcamento", "orçamento", "produto", "produtos",
}


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


def _norm(texto: Any) -> str:
    text = str(texto or "")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.lower()
    text = text.replace("º", "").replace("°", "").replace("ª", "")
    text = re.sub(r"c\s*/\s*caixa", "c caixa", text)
    text = re.sub(r"s\s*/\s*caixa", "s caixa", text)
    text = re.sub(r"[^a-z0-9,.]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


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


def _extract_quantidade(texto: str) -> int | None:
    texto = texto or ""
    patterns = [
        r"\b(?:quero|preciso|comprar|mande|manda|me\s+v[êe]|orc?amento\s+de|or[cç]amento\s+de)\s+(\d{1,5})\b",
        r"\b(\d{1,5})\s*(?:un|unid|unidade|unidades|pe[cç]as|panelas|tampas|kits)\b",
    ]
    for pat in patterns:
        m = re.search(pat, texto, flags=re.IGNORECASE)
        if m:
            try:
                qtd = int(m.group(1))
                if 0 < qtd <= 100000:
                    return qtd
            except Exception:
                pass
    return None


def _is_product_question(texto: str) -> bool:
    return bool(PRODUTO_RE.search(texto or ""))


def _preparar_termo_produto(texto: str) -> str:
    normal = _norm(texto)
    tokens = [t for t in normal.split() if t and t not in STOPWORDS_BUSCA]
    # Mantém a frase original curta quando sobrar pouco token.
    if not tokens:
        return (texto or "").strip()[:300]
    return " ".join(tokens)[:300]


def _atributos_pergunta(texto: str) -> dict[str, Any]:
    n = _norm(texto)
    wants_sem_caixa = bool(re.search(r"\b(sem caixa|s caixa)\b", n))
    wants_com_caixa = bool(re.search(r"\b(com caixa|c caixa)\b", n)) and not wants_sem_caixa

    tipo = None
    if "panela" in n and ("pressao" in n or "pressa" in n):
        tipo = "panela_pressao"
    elif "cafeteira" in n:
        tipo = "cafeteira"
    elif "jogo" in n and "panela" in n:
        tipo = "jogo_panela"

    cores = []
    for cor in ["preta", "preto", "polida", "polido", "craqueada", "craq", "vermelha", "vermelho"]:
        if re.search(rf"\b{cor}\b", n):
            cores.append(cor)

    capacidades: list[str] = []
    # Captura capacidades com decimal ou com L/litro explícito. Evita quantidade solta como "10 panelas".
    for m in re.finditer(r"\b(\d{1,2})(?:[,.](\d))?\s*(l|lt|litro|litros)\b", n):
        if m.group(2):
            capacidades.append(f"{int(m.group(1))}.{m.group(2)}")
        else:
            capacidades.append(str(int(m.group(1))))
    for m in re.finditer(r"\b(\d{1,2})[,.](\d)\b", n):
        capacidades.append(f"{int(m.group(1))}.{m.group(2)}")
    # Padrão comum: "pressao 45" ou "4 5" pode aparecer já normalizado de 4.5.
    if re.search(r"\b4\s+5\b", n) and "4.5" not in capacidades:
        capacidades.append("4.5")

    return {
        "norm": n,
        "tipo": tipo,
        "com_caixa": wants_com_caixa,
        "sem_caixa": wants_sem_caixa,
        "cores": list(dict.fromkeys(cores)),
        "capacidades": list(dict.fromkeys(capacidades)),
    }


def _produto_tem_capacidade(nome_norm: str, capacidade: str) -> bool:
    cap = capacidade.replace(",", ".")
    if "." in cap:
        inteiro, dec = cap.split(".", 1)
        patterns = [
            rf"\b{re.escape(inteiro)}[,. ]?{re.escape(dec)}\s*l\b",
            rf"\b{re.escape(inteiro)}\s+{re.escape(dec)}\s*l\b",
            rf"\b{re.escape(inteiro)}[,. ]?{re.escape(dec)}\b",
        ]
    else:
        patterns = [rf"\b{re.escape(cap)}\s*l\b", rf"\b{re.escape(cap)}\b"]
    return any(re.search(pat, nome_norm) for pat in patterns)


def _score_produto(produto: dict[str, Any], attrs: dict[str, Any], posicao_api: int) -> int:
    nome = str(produto.get("nome") or "")
    categoria = str(produto.get("categoria") or "")
    nome_norm = _norm(f"{nome} {categoria}")
    score = max(0, 200 - posicao_api)  # respeita o ranking da API, mas permite corrigir por atributo

    tipo = attrs.get("tipo")
    if tipo == "panela_pressao":
        if "panela" in nome_norm and "pressao" in nome_norm:
            score += 180
        else:
            score -= 180
    elif tipo == "cafeteira":
        if "cafeteira" in nome_norm:
            score += 180
        else:
            score -= 180
    elif tipo == "jogo_panela":
        if "jogo" in nome_norm and "panela" in nome_norm:
            score += 180
        else:
            score -= 120

    if attrs.get("com_caixa"):
        if re.search(r"\bc caixa\b", nome_norm) or re.search(r"\bcom caixa\b", nome_norm):
            score += 220
        if re.search(r"\bs caixa\b", nome_norm) or re.search(r"\bsem caixa\b", nome_norm):
            score -= 260
    if attrs.get("sem_caixa"):
        if re.search(r"\bs caixa\b", nome_norm) or re.search(r"\bsem caixa\b", nome_norm):
            score += 220
        if re.search(r"\bc caixa\b", nome_norm) or re.search(r"\bcom caixa\b", nome_norm):
            score -= 260

    for cap in attrs.get("capacidades") or []:
        if _produto_tem_capacidade(nome_norm, cap):
            score += 220
        else:
            score -= 40

    for cor in attrs.get("cores") or []:
        if cor in {"preto", "preta"}:
            if "preta" in nome_norm or "preto" in nome_norm:
                score += 80
        elif cor in {"craqueada", "craq"}:
            if "craq" in nome_norm or "craqueada" in nome_norm:
                score += 50
        elif cor in nome_norm:
            score += 50

    return score


def _produto_caption(produto: dict[str, Any]) -> str:
    nome = str(produto.get("nome") or "Produto").strip()
    preco = produto.get("precoFormatado") or ""
    if not preco and produto.get("preco") is not None:
        try:
            preco = f"R$ {float(produto['preco']):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except Exception:
            preco = ""
    return f"{nome} - {preco}" if preco else nome


def _preparar_produtos_whatsapp(produtos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for produto in produtos[:MAX_PRODUCT_CARDS]:
        if not isinstance(produto, dict):
            continue
        cards.append({
            "id": produto.get("id"),
            "nome": produto.get("nome"),
            "preco": produto.get("preco"),
            "precoFormatado": produto.get("precoFormatado"),
            "foto": produto.get("foto"),
            "caption": _produto_caption(produto),
            "capacidadeCaixa": produto.get("capacidadeCaixa"),
            "itemLegado": produto.get("itemLegado"),
        })
    return cards


def _selecionar_produtos(texto: str, produtos_api: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    attrs = _atributos_pergunta(texto)
    scored = []
    for idx, produto in enumerate(produtos_api):
        if not isinstance(produto, dict):
            continue
        scored.append((_score_produto(produto, attrs, idx), idx, produto))
    scored.sort(key=lambda x: (-x[0], x[1]))
    if not scored:
        return [], False

    top_score = scored[0][0]
    second_score = scored[1][0] if len(scored) > 1 else -9999
    strong_attrs = bool(attrs.get("tipo") or attrs.get("com_caixa") or attrs.get("sem_caixa") or attrs.get("capacidades") or attrs.get("cores"))

    # Se os atributos deixam um vencedor claro, responda direto com 1 produto.
    if strong_attrs and top_score >= 350 and (top_score - second_score >= 120 or len(scored) == 1):
        return [scored[0][2]], True

    # Caso contrário, mostre opções, mas já reordenadas pelos atributos.
    selected = [p for _score, _idx, p in scored[:MAX_PRODUCT_CARDS]]
    return selected, False


def _montar_resposta_produtos(produtos: list[dict[str, Any]], total_encontrados: int, quantidade: int | None, unico: bool) -> str:
    if not produtos:
        return "Não encontrei esse produto. Me diga o modelo ou tamanho."

    if unico or len(produtos) == 1:
        produto = produtos[0]
        nome = produto.get("nome") or "Produto"
        preco = produto.get("precoFormatado") or "preço não cadastrado"
        total = produto.get("totalFormatado") or ""
        if quantidade is not None and total:
            return f"{nome}: {preco}. {quantidade} unidade(s): {total}."
        if preco and preco != "preço não cadastrado":
            return f"{nome}: {preco}. Qual quantidade?"
        return f"{nome}: preço não cadastrado. Vou confirmar."

    linhas = []
    for idx, produto in enumerate(produtos[:3], 1):
        nome = produto.get("nome") or "Produto"
        preco = produto.get("precoFormatado") or ""
        linhas.append(f"{idx}. {nome}" + (f" - {preco}" if preco else ""))
    if total_encontrados > len(produtos):
        return f"Encontrei {total_encontrados} opções. Principais: " + "; ".join(linhas) + ". Qual dessas?"
    return "Encontrei essas opções: " + "; ".join(linhas) + ". Qual dessas?"


def _product_api(texto: str) -> tuple[bool, str, str, dict[str, Any]]:
    if not ALUMINIO_BASE_URL or not ASSISTENTE_API_TOKEN:
        return False, FALLBACK_TEXT, "product api env missing", {}

    termo_produto = _preparar_termo_produto(texto)
    payload: dict[str, Any] = {"termo": termo_produto}
    qtd = _extract_quantidade(texto)
    if qtd is not None:
        payload["quantidade"] = qtd

    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{ALUMINIO_BASE_URL}/api/assistente/produtos/consultar",
        data=raw,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ASSISTENTE_API_TOKEN}",
            "User-Agent": "hermes-http-bridge-aluminio-jr/1.1",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[-500:]
        return False, FALLBACK_TEXT, f"product api http {exc.code}: {detail}", {}
    except Exception as exc:
        return False, FALLBACK_TEXT, f"product api error: {exc}", {}

    produtos_api = data.get("produtos") if isinstance(data, dict) else []
    if not isinstance(produtos_api, list):
        produtos_api = []

    produtos, unico = _selecionar_produtos(texto, produtos_api)
    total_encontrados = int(data.get("encontrados") or len(produtos_api) or len(produtos)) if isinstance(data, dict) else len(produtos)
    resposta = _montar_resposta_produtos(produtos, total_encontrados, qtd, unico)
    cards = _preparar_produtos_whatsapp(produtos)

    extra = {
        "tipoResposta": "produtos" if cards else "texto",
        "enviarComoImagem": bool(cards),
        "termoProduto": termo_produto,
        "totalEncontrados": total_encontrados,
        "produtoUnico": bool(unico or len(produtos) == 1),
        "produtos": cards,
    }
    return bool(produtos), resposta, "product api structured", extra


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
        return False, FALLBACK_TEXT, "OPENAI_API_KEY missing", {}

    system = (
        "Você é o assistente de atendimento da Alumínio JR no WhatsApp. "
        "Responda em português do Brasil, de forma curta, direta e natural. "
        "Use no máximo 2 frases. Faça no máximo 1 pergunta. "
        "Não peça nome no começo. Não use 'O que você precisa?'. "
        "Prefira 'Em que posso te ajudar?'. "
        "Não invente preço, prazo, estoque, pagamento, pedido ou desconto. "
        "Se não souber algo do sistema, diga: 'Vou confirmar.'"
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
            return True, text, "openai responses", {}
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
            return True, text, "openai chat.completions", {}
    except Exception as exc:
        return False, FALLBACK_TEXT, f"openai error: responses={first_error}; chat={exc}", {}

    return False, FALLBACK_TEXT, "openai empty output", {}


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
        "Responda curto e direto.\n\n"
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
        return False, FALLBACK_TEXT, f"cli timeout after {TIMEOUT_SECONDS}s", {}
    except Exception as exc:
        return False, FALLBACK_TEXT, f"cli exception: {exc}", {}
    out = _clean_cli_output(proc.stdout or "")
    elapsed = time.time() - started
    if proc.returncode != 0:
        return False, out or FALLBACK_TEXT, f"cli rc={proc.returncode}; elapsed={elapsed:.1f}s", {}
    if not out or ("Available Tools" in out and len(out) > 800):
        return False, FALLBACK_TEXT, f"cli startup/empty; elapsed={elapsed:.1f}s", {}
    return True, out, f"cli elapsed={elapsed:.1f}s", {}


def _answer(texto: str, telefone: str = "", nome: str = "") -> tuple[bool, str, str, dict[str, Any]]:
    texto = (texto or "").strip()
    if not texto:
        return False, FALLBACK_TEXT, "empty message", {}

    if ENGINE == "cli":
        return _cli_call(texto, telefone, nome)

    for regex, resposta in SAUDACOES:
        if regex.match(texto):
            return True, resposta, "direct greeting", {"tipoResposta": "texto"}

    if _is_product_question(texto):
        return _product_api(texto)

    ok, resposta, detail, extra = _openai_call(texto, telefone, nome)
    extra.setdefault("tipoResposta", "texto")
    return ok, resposta, detail, extra


class Handler(BaseHTTPRequestHandler):
    server_version = "HermesHTTPBridge/2.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[hermes-http-bridge] {self.address_string()} - {fmt % args}", flush=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self.send_response(200)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        if self.path in {"/", "/healthz", "/api/status"}:
            _json_response(self, 200, {"ok": True, "service": "hermes-http-bridge", "engine": ENGINE, "version": "2.1"})
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
    print(f"[hermes-http-bridge] listening on {HOST}:{PORT} engine={ENGINE} version=2.1", flush=True)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
