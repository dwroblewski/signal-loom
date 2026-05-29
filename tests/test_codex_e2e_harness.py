import importlib.util
import json
from pathlib import Path

import frontmatter


ROOT = Path(__file__).resolve().parents[1]
HARNESS_PATH = ROOT / "scripts" / "codex_plugin_e2e.py"

spec = importlib.util.spec_from_file_location("codex_plugin_e2e", HARNESS_PATH)
codex_plugin_e2e = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(codex_plugin_e2e)


def test_scrub_env_removes_api_key_variables():
    env = {key: "dummy-value" for key in codex_plugin_e2e.FORBIDDEN_ENV_KEYS}
    env["PATH"] = "/bin"

    cleaned = codex_plugin_e2e.scrub_env(env)

    assert "PATH" in cleaned
    for key in codex_plugin_e2e.FORBIDDEN_ENV_KEYS:
        assert key not in cleaned


def test_clean_codex_env_sets_zdotdir_and_removes_keys(tmp_path):
    env = codex_plugin_e2e.clean_codex_env(tmp_path)

    assert env["ZDOTDIR"] == str(tmp_path)
    for key in codex_plugin_e2e.FORBIDDEN_ENV_KEYS:
        assert key not in env


def test_marketplace_payload_uses_local_relative_plugin_path():
    payload = codex_plugin_e2e.marketplace_payload("signal-loom-test")

    assert payload["name"] == "signal-loom-test"
    assert payload["interface"]["displayName"] == "Signal Loom Real E2E"
    assert payload["plugins"][0]["name"] == "signal-loom"
    assert payload["plugins"][0]["source"] == {
        "source": "local",
        "path": "./plugins/signal-loom",
    }
    assert payload["plugins"][0]["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_USE",
    }
    assert payload["plugins"][0]["category"] == "Productivity"


def test_codex_exec_args_enable_plugins_hooks_and_exclude_keys(tmp_path):
    args = codex_plugin_e2e.codex_exec_args(
        tmp_path / "out.txt",
        ROOT,
        "prompt",
    )

    assert args[:2] == ["codex", "exec"]
    assert ["--enable", "plugins"] == args[args.index("--enable") : args.index("--enable") + 2]
    assert "hooks" in args
    assert "--dangerously-bypass-hook-trust" in args
    assert 'forced_login_method="chatgpt"' in args
    assert "allow_login_shell=false" in args
    assert "shell_environment_policy.experimental_use_profile=false" in args
    policy = args[args.index("shell_environment_policy.exclude=[\"OPENAI_API_KEY\",\"CODEX_API_KEY\",\"ANTHROPIC_API_KEY\"]")]
    for key in codex_plugin_e2e.FORBIDDEN_ENV_KEYS:
        assert key in policy


def test_build_enrich_prompt_invokes_skill_and_guarded_shell(tmp_path):
    install_root = tmp_path / "install"
    article = install_root / codex_plugin_e2e.E2E_ARTICLE_REL
    raw_file = tmp_path / "raw.yaml"

    prompt = codex_plugin_e2e.build_enrich_prompt(install_root, article, raw_file)

    assert "$enrich" in prompt
    assert "signal-loom plugin" in prompt
    assert "not the Claude-oriented" in prompt
    assert "env -u OPENAI_API_KEY -u CODEX_API_KEY -u ANTHROPIC_API_KEY" in prompt
    assert "core.enrichment_packets emit --max-files 1" in prompt
    assert "core.enrichment_writeback apply" in prompt
    assert "core.enrich.ApiEnricher" in prompt


def test_verify_install_outputs_accepts_enriched_article_and_index(tmp_path):
    install_root = tmp_path / "install"
    article = install_root / codex_plugin_e2e.E2E_ARTICLE_REL
    article.parent.mkdir(parents=True)
    post = frontmatter.Post(
        "body",
        title="Real Codex E2E Signal Loom Enrichment Test",
        enriched=True,
    )
    article.write_text(frontmatter.dumps(post))
    index = {
        "entries": [
            {
                "path": codex_plugin_e2e.E2E_INDEX_REL,
                "enriched": True,
                "topics": {"primary": ["ai agents"]},
            }
        ]
    }
    (install_root / "index.json").write_text(json.dumps(index))

    result = codex_plugin_e2e.verify_install_outputs(install_root)

    assert result["frontmatter_enriched"] is True
    assert result["index_matches"] == 1
    assert result["index_primary_topics"] == ["ai agents"]


def test_verify_codex_final_requires_all_checkpoints(tmp_path):
    output = tmp_path / "final.txt"
    output.write_text(
        "\n".join(
            [
                "skill_used: enrich",
                "guarded_env: absent",
                "writeback: ok",
                "index_entry: ok",
            ]
        )
    )

    result = codex_plugin_e2e.verify_codex_final(output)

    assert result["codex_final_checkpoints"] == [
        "guarded_env",
        "index_entry",
        "skill_used",
        "writeback",
    ]
