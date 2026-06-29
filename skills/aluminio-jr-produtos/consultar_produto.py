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
from typing import Any


def limpar(valor: str | None) -> str:
    return (valor or "").strip()


def produto_linha(produto: dict[str, Any], quantidade: Any = None) -> str:
    nome = limpar(str(produto.get("nome") or ""))
    preco = limpar(str(produto.get("precoFormatado") or ""))
    total = limpar(str(produto.get("totalFormatado") or ""))

    partes = [nome]
    if preco:
        partes.append(preco)
    if quantidade is not None and total:
        partes.append(f"{quantidade} unidade(s): {total}")

    return " - ".join(partes)


def montar_resposta_cliente(dados: dict[str, Any]) -> str:
    mensagem_curta = limpar(str(dados.get("mensagem_curta") or ""))

    if not dados.get("ok"):
        return mensagem_curta or "Não consegui consultar agora. Vou confirmar."

    produtos = dados.get("produtos")
    if not isinstance(produtos, list) or not produtos:
        return mensagem_curta or "Não encontrei esse produto. Me diga o modelo ou tamanho."

    quantidade = dados.get("quantidade")
    encontrados = dados.get("encontrados")

    if len(produtos) == 1:
        linha = produto_linha(produtos[0], quantidade)
        if quantidade is not None and produtos[0].get("totalFormatado"):
            return f"{linha}."
        return f"{linha}. Qual quantidade?"

    linhas = [produto_linha(produto, quantidade) for produto in produtos[:3]]
    linhas = [linha for linha in linhas if linha]

    if not linhas:
        return mensagem_curta or "Encontrei opções. Qual modelo?"

    cabecalho = "Encontrei essas opções:"
    if isinstance(encontrados, int) and encontrados > len(produtos):
        cabecalho = f"Encontrei {encontrados} opções. Principais:"

    opcoes = "\n".join(f"{indice + 1}. {linha}" for indice, linha in enumerate(linhas))
    return f"{cabecalho}\n{opcoes}\nQual dessas?"


def enriquecer_saida(dados: dict[str, Any]) -> dict[str, Any]:
    resposta = montar_resposta_cliente(dados)
    dados["resposta_cliente"] = resposta
    dados["instrucoes_para_hermes"] = (
        "Se ok=true, responda ao cliente usando resposta_cliente. "
        "Não peça nome, cidade ou cadastro apenas para informar preço. "
        "Não encaminhe para atendente quando a API retornou preço."
    )

    produtos = dados.get("produtos")
    if isinstance(produtos, list):
        dados["produtos_resumidos"] = [
            {
                "nome": produto.get("nome"),
                "preco": produto.get("precoFormatado"),
                "quantidade": produto.get("quantidade"),
                "total": produto.get("totalFormatado"),
            }
            for produto in produtos[:5]
            if isinstance(produto, dict)
        ]

    return dados


def main() -> int:
    parser = argparse.ArgumentParser(description="Consultar produtos da Alumínio JR")
    parser.add_argument("--termo", required=True, help="Produto/modelo informado pelo cliente")
    parser.add_argument("--quantidade", required=False, help="Quantidade desejada")
    args = parser.parse_args()

    base_url = limpar(os.environ.get("ALUMINIO_JR_API_BASE_URL")).rstrip("/")
    token = limpar(os.environ.get("ASSISTENTE_API_TOKEN"))

    if not base_url:
        print(json.dumps(enriquecer_saida({
            "ok": False,
            "mensagem_curta": "API da Alumínio JR não configurada.",
            "erro": "ALUMINIO_JR_API_BASE_URL ausente"
        }), ensure_ascii=False, indent=2))
        return 2

    if not token:
        print(json.dumps(enriquecer_saida({
            "ok": False,
            "mensagem_curta": "Token do assistente não configurado.",
            "erro": "ASSISTENTE_API_TOKEN ausente"
        }), ensure_ascii=False, indent=2))
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
            print(json.dumps(enriquecer_saida(parsed), ensure_ascii=False, indent=2))
            return 0 if parsed.get("ok") else 1
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {"erro": raw[:500]}
        parsed.setdefault("ok", False)
        parsed.setdefault("mensagem_curta", "Não consegui consultar agora. Vou confirmar.")
        parsed["http_status"] = exc.code
        print(json.dumps(enriquecer_saida(parsed), ensure_ascii=False, indent=2))
        return 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps(enriquecer_saida({
            "ok": False,
            "mensagem_curta": "Não consegui consultar agora. Vou confirmar.",
            "erro": str(exc)
        }), ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
