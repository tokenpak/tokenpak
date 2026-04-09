# tokenpak-claude-code plugin

This directory is the source tree for the `tokenpak-claude-code` Claude Code plugin.

The plugin extends Claude Code with token-aware context packing, semantic search, and structured field extraction backed by the [tokenpak](https://github.com/tokenpak/tokenpak) open-source library. Refer to the main repository for installation instructions, usage documentation, and contribution guidelines.

## Per-mode behavior

The plugin behaves differently depending on how Claude Code is invoked (TUI, CLI `-p`, `--bare`, TMUX, IDE, cron, SDK). See [MODES.md](./MODES.md) for the full per-mode behavior matrix, unsupported modes, and the `--bare` future-default warning.

## Configuration

Claude Code prompts for these values when the plugin is enabled. All keys are optional; defaults are applied when left blank.

| Key | Default | Sensitive | Description |
|-----|---------|-----------|-------------|
| `tokenpak_proxy_url` | `http://localhost:8766` | No | URL of the tokenpak proxy server. Leave default if running the proxy locally. |
| `vault_root` | *(empty)* | No | Absolute path to your vault root directory. Leave empty to skip vault-aware features. |
| `enable_telemetry_hook` | `true` | No | Enable the telemetry hook to log tool usage locally. If proxy URL is set, events are also POSTed there. Opt-out at any time. |
| `enable_validation_hook` | `false` | No | Enable the post-edit validation hook. Opt-in feature that validates edits against schema constraints. Adds latency. |
| `license_key` | *(empty)* | **Yes** (keychain) | tokenpak Pro license key. Required for Pro-tier features. Contact tokenpak for a key. |

Non-sensitive values are stored in `settings.json` under `pluginConfigs`. The `license_key` is stored in the system keychain (or `~/.claude/.credentials.json` where the keychain is unavailable) and never written to `settings.json`.
