---
name: aluminio-jr-produtos
description: Use when the user/customer asks about Alumínio JR products, product models, prices, quantities, catalog items, orçamento, valores, tabela, kit, panela, tampa, alça, cabo, válvula, or any product-price question.
version: 1.0.0
author: Alumínio JR / ChatGPT
license: MIT
metadata:
  hermes:
    tags: [Aluminio JR, Produtos, Precos, Catalogo, Atendimento]
---

# Alumínio JR — Consulta segura de produtos

Use this skill whenever the customer asks about:

- preço
- valor
- produto
- modelo
- quantidade
- catálogo/tabela
- orçamento
- item específico da Alumínio JR

## Hard rule

Never invent product prices.

When the customer asks price/model/product information, consult the Alumínio JR API helper first.

## Helper command

Use the terminal tool and run:

```bash
python3 /opt/render-tools/skills-local/aluminio-jr-produtos/consultar_produto.py --termo "TERMO DO CLIENTE"
```

If the customer clearly provides quantity, include:

```bash
python3 /opt/render-tools/skills-local/aluminio-jr-produtos/consultar_produto.py --termo "panela 4" --quantidade 10
```

The helper reads:

- `ALUMINIO_JR_API_BASE_URL`
- `ASSISTENTE_API_TOKEN`

and calls:

```text
POST /api/assistente/produtos/consultar
```

## Response style

After calling the helper, answer short.

Use the API field `mensagem_curta` as the main answer.

Do not expose JSON unless the user asks for technical output.

## Examples

Customer:

> Quanto custa a panela 4?

Action:

```bash
python3 /opt/render-tools/skills-local/aluminio-jr-produtos/consultar_produto.py --termo "panela 4"
```

Answer:

> Panela nº 4: R$ 35,00. Qual quantidade?

Customer:

> Quero 10 panelas 4

Action:

```bash
python3 /opt/render-tools/skills-local/aluminio-jr-produtos/consultar_produto.py --termo "panela 4" --quantidade 10
```

Answer:

> Panela nº 4: R$ 35,00. 10 unidade(s): R$ 350,00.

Customer:

> Tem panela barata?

If the API returns multiple products:

> Encontrei algumas opções. Qual modelo ou tamanho?

## If the API fails

Do not guess.

Say:

> Não consegui consultar agora. Vou encaminhar para confirmar.

## Safety

Do not:

- reveal API tokens
- reveal URLs with credentials
- show stack traces
- modify products
- change prices
- promise discount
- promise stock or delivery
