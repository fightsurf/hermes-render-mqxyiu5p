from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


def load_patch_config():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "patch-config.py"
    spec = importlib.util.spec_from_file_location("patch_config", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules.setdefault("yaml", types.SimpleNamespace())
    spec.loader.exec_module(module)
    return module


class PatchConfigTests(unittest.TestCase):
    def test_default_render_mcp_entry_does_not_filter_tools(self):
        patch_config = load_patch_config()

        render_entry = patch_config._render_entry()

        self.assertNotIn("tools", render_entry)

    def test_openai_model_config_uses_key_env(self):
        patch_config = load_patch_config()

        model = patch_config._openai_model_config()

        self.assertEqual(model["provider"], "custom")
        self.assertEqual(model["base_url"], "https://api.openai.com/v1")
        self.assertEqual(model["key_env"], "OPENAI_API_KEY")
        self.assertNotIn("api_key", model)

    def test_existing_custom_config_replaces_literal_api_key_with_key_env(self):
        patch_config = load_patch_config()
        config = {
            "model": {
                "provider": "custom",
                "default": "gpt-4o-mini",
                "base_url": "https://api.openai.com/v1",
                "api_key": "${OPENAI_API_KEY}",
            }
        }

        changed = patch_config.ensure_openai_custom_model(config)

        self.assertTrue(changed)
        self.assertNotIn("api_key", config["model"])
        self.assertEqual(config["model"]["key_env"], "OPENAI_API_KEY")

