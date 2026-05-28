import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_codex_manifest_points_to_codex_only_components():
    manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())

    assert manifest["name"] == "signal-loom"
    assert manifest["skills"] == "./codex/skills/"
    assert manifest["hooks"] == "./hooks/codex-hooks.json"
    assert manifest["interface"]["defaultPrompt"] == [
        "$signal-loom-pipeline refresh my sources"
    ]
    assert (ROOT / manifest["skills"]).is_dir()
    assert (ROOT / manifest["hooks"]).is_file()


def test_codex_plugin_directory_contains_only_manifest():
    files = [p.name for p in (ROOT / ".codex-plugin").iterdir()]
    assert files == ["plugin.json"]


def test_codex_hook_uses_plugin_root_and_claude_hook_stays_claude_specific():
    codex_hooks = (ROOT / "hooks" / "codex-hooks.json").read_text()
    claude_hooks = (ROOT / "hooks" / "hooks.json").read_text()

    assert "${PLUGIN_ROOT}" in codex_hooks
    assert '"matcher": "*"' in codex_hooks
    assert "${CLAUDE_PLUGIN_ROOT}" not in codex_hooks
    assert "${CLAUDE_PLUGIN_ROOT}" in claude_hooks
