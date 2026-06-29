---
name: aluminio-jr-produtos
description: Use when the user/customer asks about Alumínio JR products, product models, prices, quantities, catalog items, orçamento, valores, tabela, kit, panela, tampa, alça, cabo, válvula, or any product-price question.
version: 1.0.1
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

## Hard rules

Never invent product prices.
Always consult the Alumínio JR API helper before answering product/price/model questions.
If the helper returns `ok: true`, you MUST answer using the returned product/price information.
If the helper returns `resposta_cliente`, use it as the main final answer.
Do not say "preciso encaminhar para um atendente" when the helper returned `ok: true`.
Do not ask for Nome, Cidade, or "já é cliente" just to answer a price question.
Ask only one question at the end when clarification is needed.

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

## How to answer after the helper

### If helper returns `ok: true`

Answer with the returned `resposta_cliente` or `mensagem_curta`.
Keep it short.
Do not expose JSON.
Do not request customer registration data.

### If helper returns many options

List up to 3 options with price and ask which one.

Example:

> Encontrei algumas opções:
> 1. Panela de pressão 4.5L preta com caixa — R$ 36,70
> 2. Panela de pressão 4.5L preta sem caixa — R$ 35,70
> Qual dessas?

### If helper returns 1 product

Answer the product and price.

Example:

> Panela de pressão 4.5L preta com caixa: R$ 36,70. Qual quantidade?

### If quantity was provided

Include the total if returned.

Example:

> Panela de pressão 4.5L preta com caixa: R$ 36,70. 10 unidade(s): R$ 367,00.

### If helper returns `ok: false`

Do not guess.
Say:

> Não encontrei esse produto. Me diga o modelo ou tamanho.

### If the API fails

Do not guess.
Say:

> Não consegui consultar agora. Vou confirmar.

## Never do this when API returned `ok: true`

Do not answer:

> Consigo registrar seu interesse. Para confirmar preço, preciso encaminhar para um atendente.

That is only allowed when the API/helper failed.

## Safety

Do not:

- reveal API tokens
- reveal URLs with credentials
- show stack traces
- modify products
- change prices
- promise discount
- promise stock or delivery
