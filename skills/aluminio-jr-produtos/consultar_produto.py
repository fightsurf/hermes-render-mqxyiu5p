#!/usr/bin/env python3
"""Consulta produtos/preços na API segura da Alumínio JR.

Uso:
  python3 consultar_produto.py --termo "panela 4"
  python3 consultar_produto.py --termo "panela 4" --quantidade 10
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def limpar(valor: str | None) -> str:
    return (valor or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Consultar produtos da Alumínio JR")
    parser.add_argument("--termo", required=True, help="Produto/modelo informado pelo cliente")
    parser.add_argument("--quantidade", required=False, help="Quantidade desejada")
    args = parser.parse_args()

    base_url = limpar(os.environ.get("ALUMINIO_JR_API_BASE_URL")).rstrip("/")
    token = limpar(os.environ.get("ASSISTENTE_API_TOKEN"))

    if not base_url:
        print(json.dumps({
            "ok": False,
            "mensagem_curta": "API da Alumínio JR não configurada.",
            "erro": "ALUMINIO_JR_API_BASE_URL ausente"
        }, ensure_ascii=False))
        return 2

    if not token:
        print(json.dumps({
            "ok": False,
            "mensagem_curta": "Token do assistente não configurado.",
            "erro": "ASSISTENTE_API_TOKEN ausente"
        }, ensure_ascii=False))
        return 2

    payload = {
        "termo": args.termo,
    }

    if limpar(args.quantidade):
        payload["quantidade"] = args.quantidade

    body = json.dumps(payload).encode("utf-8")
    url = f"{base_url}/api/assistente/produtos/consultar"

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "hermes-aluminio-jr/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {
                    "ok": False,
                    "mensagem_curta": "API não retornou JSON válido.",
                    "raw": raw[:500],
                }
            print(json.dumps(parsed, ensure_ascii=False, indent=2))
            return 0 if parsed.get("ok") else 1
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"erro": raw[:500]}
        parsed.setdefault("ok", False)
        parsed.setdefault("mensagem_curta", "Não consegui consultar agora. Vou encaminhar para confirmar.")
        parsed["http_status"] = exc.code
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
        return 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({
            "ok": False,
            "mensagem_curta": "Não consegui consultar agora. Vou encaminhar para confirmar.",
            "erro": str(exc)
        }, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
