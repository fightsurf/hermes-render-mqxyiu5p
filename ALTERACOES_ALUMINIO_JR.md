# Patch Hermes — Alumínio JR / OpenAI

Arquivos alterados/adicionados:

- `scripts/patch-config.py`
  - Corrige `provider: openai` e `provider: openai-api` para `provider: custom`.
  - Configura `base_url: https://api.openai.com/v1`.
  - Usa `api_key: ${OPENAI_API_KEY}`.
  - Usa modelo padrão `gpt-4o-mini`, ou o valor de `ALUMINIO_JR_OPENAI_MODEL`.
  - Adiciona regras da Alumínio JR em `/opt/data/SOUL.md` uma única vez.

- `skills/aluminio-jr-atendimento/SKILL.md`
  - Nova skill de atendimento primário da Alumínio JR.
  - Define limites: não inventar preço, prazo, estoque, pagamento ou status.
  - Orienta triagem: nome, cidade, interesse, itens e quantidade.

- `render.yaml`
  - Adiciona `OPENAI_API_KEY` como variável `sync: false`.
  - Adiciona `ALUMINIO_JR_OPENAI_MODEL=gpt-4o-mini`.
  - Habilita o bootstrap de OpenAI e SOUL.md.

- `.env.example`
  - Documenta as variáveis usadas no teste.

- `README.md`
  - Adiciona seção rápida de uso para Alumínio JR.

## Como aplicar

1. Substitua/envie estes arquivos no repositório GitHub do Hermes.
2. Na Render, confirme em `Environment`:
   - `OPENAI_API_KEY` preenchida com sua chave OpenAI API.
   - `ALUMINIO_JR_OPENAI_MODEL=gpt-4o-mini`.
3. Faça deploy/restart do serviço Hermes.
4. Não entre em `MODELS` antes do primeiro teste.
5. Teste em `CHAT`:

```text
responda apenas ok
```

Se quiser testar pelo Shell da Render:

```bash
/opt/hermes/.venv/bin/hermes chat -q "responda apenas ok"
```

## Importante

Não coloque ainda Z-API, banco, Firebird, PostgreSQL ou tokens do sistema Alumínio JR.
Primeiro confirme que o Hermes responde usando a chave OpenAI.
