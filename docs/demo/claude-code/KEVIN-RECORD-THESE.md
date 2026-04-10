# Follow-up for Kevin: Record the 6 Per-Mode Demos

**Filed:** 2026-04-10  
**Filed by:** Cali (CCI-19)  
**Priority:** p2-medium  

## What Cali has done

- Created placeholder `.cast` files for all 6 modes (each contains a text preview so links work now)
- Wrote fully-annotated recording scripts for each mode (see below)
- Linked all 6 recordings in `docs/claude-code-integration.md`

## What Kevin needs to do

Record 5 asciinema demos + 1 screen recording to replace the placeholders:

| Mode | Script | Output file | Tool |
|------|--------|-------------|------|
| CLI | `docs/demo/claude-code/cli.sh` | `docs/demo/claude-code/cli.cast` | asciinema |
| TUI | `docs/demo/claude-code/tui.sh` | `docs/demo/claude-code/tui.cast` | asciinema |
| tmux | `docs/demo/claude-code/tmux.sh` | `docs/demo/claude-code/tmux.cast` | asciinema |
| SDK | `docs/demo/claude-code/sdk.sh` | `docs/demo/claude-code/sdk.cast` | asciinema |
| IDE | `docs/demo/claude-code/ide.sh` | Replace link in docs with YouTube/mp4 URL | OBS / screen recording |
| Cron | `docs/demo/claude-code/cron.sh` | `docs/demo/claude-code/cron.cast` | asciinema |

## Recording instructions

For each asciinema demo:

```bash
# Install asciinema if needed
pip install asciinema

# Record (replace MODE with cli / tui / tmux / sdk / cron)
asciinema rec docs/demo/claude-code/MODE.cast \
  --title "TokenPak MODE mode" \
  --idle-time-limit 2

# Then run the corresponding script interactively (read it first!)
# bash docs/demo/claude-code/MODE.sh
```

Target: **~30 seconds** per recording. Keep typing speed natural; `--idle-time-limit 2` caps long pauses.

## Constraints

- **No sensitive content** — anonymize any real API keys, session IDs, file paths with personal info
- **No fast-forward** — real-time recording only
- **IDE mode**: asciinema cannot capture the IDE UI — use OBS. Suggested layout: VSCode on left, terminal log on right. Replace the `ide.cast` link in `docs/claude-code-integration.md` with the YouTube/mp4 URL.

## Requirements before recording

Read the "REQUIREMENTS BEFORE RECORDING" section at the top of each `.sh` file. In general:

1. `pip install tokenpak` (latest)
2. `ANTHROPIC_API_KEY` set in environment
3. `tokenpak serve --port 8766 &` running before each session
4. `export ANTHROPIC_BASE_URL=http://localhost:8766`

## After recording

Commit the `.cast` files to this repo and open a PR. Tag Cali to review.

```bash
git add docs/demo/claude-code/*.cast
git commit -m "content: add 6 per-mode asciinema recordings for tokenpak Claude Code integration"
git push
```
