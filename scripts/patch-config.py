#!/opt/hermes/.venv/bin/python
"""Idempotent patcher for Hermes' ~/.hermes/config.yaml on Render.

Adds Render MCP tooling, external skill directories, and a conservative
Aluminio JR bootstrap for OpenAI-based testing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

# Listed in precedence order: skills-local wins on name collision with
# skills-upstream, which lets our overlay shadow a same-named upstream skill.
RENDER_SKILL_DIRS = (
    "/opt/render-tools/skills-local",
    "/opt/render-tools/skills-upstream",
)
RENDER_MCP_URL = "https://mcp.render.com/mcp"
RENDER_MCP_AUTH = "Bearer ${RENDER_MCP_API_KEY}"

OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-5-mini"
ALUMINIO_SOUL_MARKER_START = "<!-- ALUMINIO_JR_ASSISTENTE_START -->"
ALUMINIO_SOUL_MARKER_END = "<!-- ALUMINIO_JR_ASSISTENTE_END -->"


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[render-tools] cannot read {path}: {exc}", file=sys.stderr)
        return {}
    if not raw.strip():
        return {}
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        print(
            f"[render-tools] {path} is not valid YAML ({exc}); refusing to patch",
            file=sys.stderr,
        )
        sys.exit(0)
    return data if isinstance(data, dict) else {}


def _truthy_env(name: str, default: str = "1") -> bool:
    value = os.environ.get(name, default).strip().lower()
    return value not in {"0", "false", "no", "off", ""}


def _render_entry() -> dict:
    return {
        "url": RENDER_MCP_URL,
        "headers": {"Authorization": RENDER_MCP_AUTH},
    }


def ensure_render_mcp(config: dict) -> bool:
    """Insert mcp_servers.render if missing. Returns True if changed."""
    mcp_servers = config.get("mcp_servers")
    if mcp_servers is None:
        config["mcp_servers"] = {"render": _render_entry()}
        return True
    if not isinstance(mcp_servers, dict):
        print(
            "[render-tools] mcp_servers is not a mapping; skipping render entry",
            file=sys.stderr,
        )
        return False
    if "render" in mcp_servers:
        return False
    mcp_servers["render"] = _render_entry()
    return True


def ensure_external_skill_dirs(config: dict) -> list[str]:
    """Append the render-tools skill dirs to skills.external_dirs if missing.

    Returns the list of paths that were actually added.
    """
    skills = config.setdefault("skills", {})
    if not isinstance(skills, dict):
        print(
            "[render-tools] skills is not a mapping; skipping external_dirs",
            file=sys.stderr,
        )
        return []
    existing = skills.get("external_dirs")
    if existing is None:
        skills["external_dirs"] = list(RENDER_SKILL_DIRS)
        return list(RENDER_SKILL_DIRS)
    if not isinstance(existing, list):
        print(
            "[render-tools] skills.external_dirs is not a list; skipping",
            file=sys.stderr,
        )
        return []
    added: list[str] = []
    for path in RENDER_SKILL_DIRS:
        if path not in existing:
            existing.append(path)
            added.append(path)
    return added


def _openai_model_config() -> dict:
    model = os.environ.get("ALUMINIO_JR_OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip()
    if not model:
        model = DEFAULT_OPENAI_MODEL
    return {
        "provider": "custom",
        "default": model,
        "base_url": OPENAI_BASE_URL,
        # Hermes custom providers expect the env var name here.
        # Do not use api_key: ${OPENAI_API_KEY}; some builds treat that
        # as a literal key value and OpenAI rejects it as invalid_api_key.
        "key_env": "OPENAI_API_KEY",
    }


def ensure_openai_custom_model(config: dict) -> bool:
    """Set a working OpenAI-compatible custom model config for this template.

    Hermes image v2026.5.7 does not accept `provider: openai` or
    `provider: openai-api` in the gateway runtime used by this Render image.
    The reliable path for a direct OpenAI key is the OpenAI-compatible
    `custom` provider pointed at https://api.openai.com/v1.

    This function is intentionally conservative:
    - It fixes known-broken providers (`openai`, `openai-api`).
    - It seeds a model block when missing.
    - It can be forced with ALUMINIO_JR_FORCE_OPENAI_CONFIG=1.
    - It does not overwrite an already-custom or other explicitly configured
      provider unless forced.
    """
    if not _truthy_env("ALUMINIO_JR_ENABLE_OPENAI_BOOTSTRAP", "1"):
        return False

    model_cfg = config.get("model")
    force = _truthy_env("ALUMINIO_JR_FORCE_OPENAI_CONFIG", "0")

    if not isinstance(model_cfg, dict):
        config["model"] = _openai_model_config()
        return True

    provider = str(model_cfg.get("provider", "")).strip().lower()
    default = str(model_cfg.get("default", "")).strip()

    should_fix = force or provider in {"", "openai", "openai-api"}
    if provider == "custom" and model_cfg.get("base_url") == OPENAI_BASE_URL:
        # Keep a valid custom OpenAI config. Only fill missing fields.
        # Also remove the old api_key form so the runtime cannot prefer a
        # literal ${OPENAI_API_KEY} value over key_env.
        changed = False
        if "api_key" in model_cfg:
            model_cfg.pop("api_key", None)
            changed = True
        wanted = _openai_model_config()
        for key, value in wanted.items():
            if key not in model_cfg or not str(model_cfg.get(key, "")).strip():
                model_cfg[key] = value
                changed = True
        return changed

    if should_fix:
        config["model"] = _openai_model_config()
        return True

    # Do not override Anthropic/OpenRouter/etc. when deliberately selected.
    return False


def ensure_aluminio_jr_soul(data_dir: Path) -> bool:
    """Append the Alumínio JR operating rules to SOUL.md once."""
    if not _truthy_env("ALUMINIO_JR_ENABLE_SOUL_BOOTSTRAP", "1"):
        return False

    soul_path = data_dir / "SOUL.md"
    existing = ""
    if soul_path.exists():
        try:
            existing = soul_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"[render-tools] cannot read {soul_path}: {exc}", file=sys.stderr)
            return False

    if ALUMINIO_SOUL_MARKER_START in existing:
        return False

    section = f"""

{ALUMINIO_SOUL_MARKER_START}
# Alumínio JR — regras do assistente

Você é o assistente de atendimento primário da Alumínio JR.
Seu papel inicial é triagem comercial, não fechamento automático de pedido.

Regras obrigatórias:
- Atenda em português do Brasil, com frases curtas e objetivas.
- Colete nome, cidade, se já é cliente, interesse, itens e quantidade desejada.
- Não invente preço, prazo, desconto, estoque, condição de pagamento ou status de pedido.
- Não confirme pedido, pagamento, carrada, entrega ou crédito sem ferramenta/API autorizada.
- Quando faltar informação, pergunte apenas o necessário para continuar.
- Quando houver dúvida comercial, encaminhe para atendimento humano.
- Para reclamação, urgência, pagamento, alteração de pedido ou cobrança, encaminhe para humano.
- Antes de qualquer ação que envie mensagem, altere dados ou gere pedido, peça confirmação explícita.

Resposta padrão quando não houver ferramenta de preço conectada:
"Consigo registrar seu interesse. Para confirmar preço, preciso encaminhar para um atendente."
{ALUMINIO_SOUL_MARKER_END}
""".strip()

    new_text = (existing.rstrip() + "\n\n" + section + "\n") if existing.strip() else section + "\n"
    try:
        soul_path.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        print(f"[render-tools] cannot write {soul_path}: {exc}", file=sys.stderr)
        return False
    return True


def save_config(path: Path, config: dict) -> None:
    text = yaml.safe_dump(
        config,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    tmp = path.with_suffix(path.suffix + ".render-tools.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: patch-config.py <path/to/config.yaml>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    path.parent.mkdir(parents=True, exist_ok=True)
    config = load_config(path)
    changed_mcp = ensure_render_mcp(config)
    added_dirs = ensure_external_skill_dirs(config)
    changed_model = ensure_openai_custom_model(config)
    changed_soul = ensure_aluminio_jr_soul(path.parent)
    if changed_mcp or added_dirs or changed_model:
        save_config(path, config)
        parts = []
        if changed_mcp:
            parts.append("mcp_servers.render")
        for dir_path in added_dirs:
            parts.append(f"skills.external_dirs += {dir_path}")
        if changed_model:
            parts.append("model = custom OpenAI-compatible")
        if parts:
            print(f"[render-tools] patched {path}: {', '.join(parts)}")
    else:
        print(f"[render-tools] {path} already has render MCP + skill dirs + model; nothing to do")
    if changed_soul:
        print(f"[render-tools] appended Alumínio JR rules to {path.parent / 'SOUL.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
