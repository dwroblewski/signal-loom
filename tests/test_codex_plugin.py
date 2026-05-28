import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_codex_manifest_points_to_codex_only_components():
    manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())

    assert manifest["name"] == "signal-loom"
    assert manifest["skills"] == "./codex/skills/"
    assert manifest["hooks"] == "./hooks/codex-hooks.json"
    assert manifest["interface"]["defaultPrompt"] == [
        "$pipeline refresh my sources"
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


def test_codex_skill_names_align_with_claude_commands():
    expected = {
        "brief": "brief",
        "enrich": "enrich",
        "pipeline": "pipeline",
    }

    for folder, name in expected.items():
        codex_skill = (ROOT / "codex" / "skills" / folder / "SKILL.md").read_text()
        claude_skill = (ROOT / "skills" / folder / "SKILL.md").read_text()

        assert f"name: {name}\n" in codex_skill
        assert f"name: {name}\n" in claude_skill
