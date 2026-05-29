# Codex + VS Code Workstation Transfer

Use this when you want to move signal-loom to another computer as a local Codex
plugin and editable VS Code project without copying local secrets or generated
content.

## Build The Package

From this repo:

```bash
uv run python scripts/make_transfer_package.py
```

The archive is written to `dist/signal-loom-codex-vscode-<timestamp>.tar.gz`.
It is a Codex local marketplace root with the project nested at
`plugins/signal-loom/`.

The package includes source, tests, plugin manifests, Codex skills, Claude
skills, hooks, example config, docs, `pyproject.toml`, and `uv.lock`.

The package excludes `.git`, `.venv`, caches, `.env`, generated `content/`,
`index.json`, `failed-enrichments.jsonl`, and non-example `config/*.yaml`
files. That keeps personal feeds, local paths, generated notes, and credentials
off the transfer artifact.

## Install On Another Machine

Prerequisites:

- Codex CLI/app, already signed in.
- VS Code.
- Python 3.12 or newer.
- `uv` on `PATH`: <https://docs.astral.sh/uv/>.

Unpack somewhere durable:

```bash
mkdir -p ~/Projects
tar -xzf signal-loom-codex-vscode-<timestamp>.tar.gz -C ~/Projects
cd ~/Projects/signal-loom-codex-vscode-<timestamp>
```

Open the editable source folder:

```bash
cd plugins/signal-loom
code .
uv sync --extra dev
uv run pytest -q
```

Add the package root as a local Codex marketplace and install the plugin:

```bash
cd ~/Projects/signal-loom-codex-vscode-<timestamp>
codex plugin marketplace add "$PWD"
codex plugin add signal-loom@signal-loom-transfer
```

Start a new Codex thread with plugins enabled, then use:

```text
$pipeline refresh my sources
```

For a guarded CLI smoke run:

```bash
ZDOTDIR="$(mktemp -d)" \
env -u OPENAI_API_KEY -u CODEX_API_KEY -u ANTHROPIC_API_KEY \
codex exec \
  --enable plugins \
  --enable hooks \
  -c 'forced_login_method="chatgpt"' \
  -c 'allow_login_shell=false' \
  -c 'shell_environment_policy.experimental_use_profile=false' \
  -c 'shell_environment_policy.exclude=["OPENAI_API_KEY","CODEX_API_KEY","ANTHROPIC_API_KEY"]' \
  '$pipeline refresh my sources'
```

## Configure Sources

First run creates editable config files from `config/*.example.yaml`. You can
also create them directly:

```bash
cd ~/Projects/signal-loom-codex-vscode-<timestamp>/plugins/signal-loom
uv run python hooks/scripts/bootstrap.py
```

Then edit:

- `config/sources.yaml`
- `config/topics.yaml`
- `config/entity-aliases.yaml`
- `config/signal-loom.yaml`

The Codex-native path does not need `OPENAI_API_KEY`, `CODEX_API_KEY`, or
`ANTHROPIC_API_KEY`; model work happens in the active Codex session. The
headless Python enrichment path still requires `ANTHROPIC_API_KEY`.

## Updating Another Machine Later

Build a new transfer archive and repeat the marketplace add/install commands.
The package builder stamps the staged `.codex-plugin/plugin.json` version with a
`+codex.transfer-<timestamp>` suffix so Codex installs a fresh cache entry.
