# 30-second demo script

A recording plan for the README GIF / asciinema cast. Goal: in 30 seconds show
that tg-recall archives a chosen Telegram chat locally, finds messages with
`tg://` citations, and lets an AI agent answer from that archive over MCP.

## Privacy first (do this before recording)

The archive and session are private. Record only a throwaway demo profile that
contains one **public** channel.

- Use a separate portable home and profile so no real archive is visible:

  ```powershell
  $env:TG_RECALL_HOME = "D:\tg-recall-demo"
  $env:TG_RECALL_PROFILE = "demo"
  tg-recall setup
  tg-recall telegram auth
  ```

- Pick one public channel and note its numeric ID (`-100...`). Find it off
  camera: never record `tg-recall chats`, `config show`, `telegram auth`,
  or `doctor` because they reveal chat titles, phone number, or local paths.
- Allow only that channel for agents:

  ```powershell
  tg-recall config set ai_access.enabled true
  tg-recall config set ai_access.allowed_chat_ids "-100CHANNELID"
  ```

- Warm the archive once off camera (the `sync` below) so the on-camera run
  is fast and incremental.
- Register the MCP server in Claude Code with the same environment:
  `claude mcp add --scope user tg-recall -e TG_RECALL_HOME=D:\tg-recall-demo -e TG_RECALL_PROFILE=demo -- tg-recall-mcp`.
- Terminal: 100x28, large font, dark theme, short prompt (`function prompt { "> " }`
  in PowerShell or `PS1='> '` in bash), cleared scrollback, notifications off.

Replace `-100CHANNELID` and the search words below with values that give 3-5
clear hits in your chosen channel. Rehearse until the whole run fits in 30 s.

## Timeline

| Time | On screen | Say / caption |
| --- | --- | --- |
| 0-3 s | Title card or first prompt | "tg-recall: your Telegram, searchable by you and your AI agents - locally." |
| 3-10 s | `tg-recall sync -100CHANNELID --since 2026-09-01` | "Only the chats you choose are synced into local SQLite (FTS5). Incremental, FloodWait-safe." |
| 10-16 s | `tg-recall search "release"` | "Instant full-text search. Every hit has a `tg://` citation back to the original message." |
| 16-28 s | `claude -p "What did this channel announce about releases this month? Cite messages." --allowedTools "mcp__tg-recall"` | "Claude Code reads the same archive over a read-only MCP server and answers with citations." |
| 28-30 s | Hold on the cited answer, then end card | "Read-only MCP, chat allowlist, no cloud. github.com/Rerowros/tg-recall" |

Cut or speed up any pause longer than 1 s in editing; the agent answer usually
needs the most time, so trim the wait before the text appears.

## Recording options

### asciinema (Linux, macOS, or WSL)

```bash
asciinema rec tg-recall-demo.cast --idle-time-limit 1 --cols 100 --rows 28
# run the timeline commands, then Ctrl+D
agg --font-size 20 tg-recall-demo.cast docs/assets/demo.gif
```

`agg` converts the cast to a GIF. Upload the `.cast` to asciinema.org only if
the recording contains nothing private.

### VHS (scripted, reproducible GIF)

Save as `demo.tape` next to your prepared environment and run `vhs demo.tape`:

```text
Output docs/assets/demo.gif
Set FontSize 20
Set Width 1200
Set Height 700
Set TypingSpeed 40ms
Type "tg-recall sync -100CHANNELID --since 2026-09-01"
Enter
Sleep 4s
Type "tg-recall search release"
Enter
Sleep 4s
Type "claude -p 'What did this channel announce about releases this month? Cite messages.' --allowedTools mcp__tg-recall"
Enter
Sleep 12s
```

### Windows only

Use ScreenToGif or ShareX on a Windows Terminal window with the same timeline.

## Before publishing the GIF

- Watch it frame by frame: no private chat names, phone number, user names
  from private chats, file paths with your user name, or API keys.
- Keep it under ~5 MB, put it in `docs/assets/demo.gif`, and add it right under
  the badges in `README.md` and `README.ru.md`.
