# Patch Hermes — Alumínio JR / OpenAI

Arquivos alterados/adicionados:

- `scripts/patch-config.py`
  - Corrige `provider: openai` e `provider: openai-api` para `provider: custom`.
  - Configura `base_url: https://api.openai.com/v1`.
  - Usa `key_env: OPENAI_API_KEY` para o Hermes buscar a chave no Environment da Render.
  - Usa modelo padrão `gpt-4o-mini`, ou o valor de `ALUMINIO_JR_OPENAI_MODEL`.
  - Remove a forma antiga `api_key: ${OPENAI_API_KEY}` quando ela existir no `config.yaml`.
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


## Bloco esperado no config.yaml

Após o deploy/restart, o início de `/opt/data/config.yaml` deve ficar assim:

```yaml
model:
  provider: custom
  default: gpt-4o-mini
  base_url: https://api.openai.com/v1
  key_env: OPENAI_API_KEY
providers: {}
fallback_providers: []
```

Não use `api_key: ${OPENAI_API_KEY}` neste template. O Hermes pode interpretar isso como texto literal e a OpenAI retorna `invalid_api_key`.

## Patch adicional — chave OpenAI antiga no disco persistente

Este patch também altera `scripts/bootstrap.sh`.

Problema resolvido:

- O Hermes pode manter uma chave antiga em `/opt/data/.env`, salva pelo dashboard.
- Mesmo após atualizar `OPENAI_API_KEY` na Render, alguns caminhos do Hermes podem continuar lendo a chave antiga do disco.
- O boot agora trata a variável da Render como fonte de verdade.

Novo comportamento no boot:

1. Se `OPENAI_API_KEY` existir no Environment da Render, o script remove qualquer linha antiga `OPENAI_API_KEY=` de `/opt/data/.env`.
2. Em seguida grava a chave atual da Render em `/opt/data/.env`.
3. Não imprime a chave nos logs.
4. Depois roda `patch-config.py` normalmente.

Variável de controle:

```text
ALUMINIO_JR_SYNC_OPENAI_ENV_TO_DISK=1
```

Deixe como `1` para este teste.

Depois do deploy, nos logs deve aparecer:

```text
[render-tools] synced OPENAI_API_KEY from Render Environment into /opt/data/.env
```

Se continuar aparecendo erro `Incorrect API key provided ...EZoA`, então a própria variável `OPENAI_API_KEY` da Render ainda está com a chave antiga.
