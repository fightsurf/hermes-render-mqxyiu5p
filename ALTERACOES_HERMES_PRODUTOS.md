# Alteração — Hermes consultando produtos da Alumínio JR

## Objetivo

Permitir que o Hermes consulte produtos e preços reais pela API segura do sistema Alumínio JR.

## Novas variáveis no Render do Hermes

```text
ALUMINIO_JR_API_BASE_URL=https://catalogo-aluminio-jr.onrender.com
ASSISTENTE_API_TOKEN=<mesmo token configurado no sistema Alumínio JR>
```

## Nova skill

```text
skills/aluminio-jr-produtos/SKILL.md
```

## Novo helper

```text
skills/aluminio-jr-produtos/consultar_produto.py
```

Teste:

```bash
python3 /opt/render-tools/skills-local/aluminio-jr-produtos/consultar_produto.py --termo "panela 4" --quantidade 10
```

## Regra comercial

O Hermes não deve inventar preço.
Ele só deve responder preço vindo da API da Alumínio JR.
