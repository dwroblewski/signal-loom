import importlib.util
import json
import sys
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "make_transfer_package.py"

spec = importlib.util.spec_from_file_location("make_transfer_package", SCRIPT)
make_transfer_package = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = make_transfer_package
spec.loader.exec_module(make_transfer_package)


def _tar_text(tar: tarfile.TarFile, name: str) -> str:
    member = tar.extractfile(name)
    assert member is not None
    return member.read().decode("utf-8")


def test_transfer_package_is_local_codex_marketplace(tmp_path):
    result = make_transfer_package.build_package(
        tmp_path,
        stamp="20260529T120000Z",
    )
    base_version = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())[
        "version"
    ].split("+", 1)[0]

    assert result.archive_path.exists()
    with tarfile.open(result.archive_path, "r:gz") as tar:
        names = set(tar.getnames())
        root = "signal-loom-codex-vscode-20260529T120000Z"

        assert f"{root}/.agents/plugins/marketplace.json" in names
        assert f"{root}/plugins/signal-loom/.codex-plugin/plugin.json" in names
        assert f"{root}/plugins/signal-loom/codex/skills/pipeline/SKILL.md" in names
        assert f"{root}/plugins/signal-loom/.vscode/tasks.json" in names
        assert f"{root}/README-transfer.md" in names

        marketplace = json.loads(
            _tar_text(tar, f"{root}/.agents/plugins/marketplace.json")
        )
        assert marketplace["name"] == "signal-loom-transfer"
        assert marketplace["plugins"][0]["source"] == {
            "source": "local",
            "path": "./plugins/signal-loom",
        }
        assert marketplace["plugins"][0]["policy"] == {
            "installation": "AVAILABLE",
            "authentication": "ON_USE",
        }

        manifest = json.loads(
            _tar_text(tar, f"{root}/plugins/signal-loom/.codex-plugin/plugin.json")
        )
        assert manifest["version"] == f"{base_version}+codex.transfer-20260529T120000Z"


def test_transfer_package_excludes_local_state_and_real_config(tmp_path):
    scratch = tmp_path / "out"
    result = make_transfer_package.build_package(
        scratch,
        stamp="20260529T120001Z",
    )

    with tarfile.open(result.archive_path, "r:gz") as tar:
        names = set(tar.getnames())
        assert not any("/.git/" in name for name in names)
        assert not any("/.venv/" in name for name in names)
        assert not any("/__pycache__/" in name for name in names)
        assert not any(name.endswith("/failed-enrichments.jsonl") for name in names)
        assert not any(name.endswith("/config/sources.yaml") for name in names)
        assert any(name.endswith("/config/sources.example.yaml") for name in names)
