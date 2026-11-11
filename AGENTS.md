# AGENTS.md — ScreenPainter handoff

Read [`docs/AGENT_GUIDE.md`](docs/AGENT_GUIDE.md) before changing code. It contains the detailed environment facts, real bugs encountered, exact tool paths and test procedure. This file is the quick contract.

## What this project is

A Windows-only, config-driven overlay app. It locates a target window (default: WeChat/Weixin main window) via TOML configuration and draws solid rectangles on it using a native owned layered window. Rectangles are grouped per display resolution, follow moves/resizes, and the overlay is click-through.

Version 0.3.0 adds to the 0.2.0 toolbar/hot-reload/groups:

1. **Event-driven loop**: `SetWinEventHook` watches the target window; the loop sleeps until something happens (measured 0% idle CPU, no 60 Hz polling). `[app] verify_ms` replaces `tick_ms`. The overlay window is fully hidden when nothing is painted, and no screen DC is held. This fixed the user's "typing in WeChat lags / the IME candidate box is slow" report - do not reintroduce periodic polling.
2. **`编辑` (edit) and `移动` (move) modes**: click a rectangle to select it (thick outline + corner handles), drag to move, drag an edge/corner to resize (edit mode only), drag empty space to create (edit mode), `Delete` deletes, `Ctrl+Z`/`Ctrl+Shift+Z` undo/redo, arrow keys nudge by 1 px (`Shift` 10 px), `Tab` cycles the selection, `Esc`/`Enter` ends the mode. A self-painted properties panel shows the selected rectangle's hex colour, position, size and opacity.
3. **Configurable shortcuts**: `[keys]` holds every editing key; parsed by `core/keymap.py` and captured by a `WH_KEYBOARD_LL` hook installed only while a mode is active (so focus is never stolen).
4. **Settings window**: `python run.py --settings` (or the toolbar's `设置` button) edits every config value through tkinter; saving validates with `load_config` before writing.
5. **Debug mode + resolution groups** (0.2.0, unchanged): `[app] debug` hot reload; `[layout] mode/match/group`, `[groups.<name>]`; CLI `--list-monitors`, `--list-groups`, `--monitor N`, `--apply-group`, `--new-group`, `--layout`, `--shapes`, `--toolbar`, `--buttons`, `--debug`.

- Entry: `python run.py` or `run.bat`
- Config: `config/config.toml` (schema version 3)
- Docs: `README.md`, `docs/CONFIG.md`, `docs/ARCHITECTURE.md`, `docs/AGENT_GUIDE.md`
- Tests: `tests/` (230 unittest tests) and `tools/smoke_test.py` (synthetic target window, includes toolbar/edit/resize/delete/undo/hot-reload phases)
- Runtime deps: none beyond Python 3.11+ on Windows (TOML writing is hand-rolled in `screenpainter/toml_edit.py`)

## This machine (verified)

- Workspace: `D:\AI\DSH\ScreenPainter` (**not a git repository** — back up before big edits)
- Python 3.11.7: `D:\MiniPrograms\Python3117\python.exe`
- Python launcher: `C:\Windows\py.exe`
- OS: Windows 11 build 26200 (x64)
- WeChat: `C:\Program Files\Tencent\Weixin\Weixin.exe`, version 4.1.15.13, process `Weixin.exe`
- Notepad: `C:\Windows\System32\notepad.exe`
- Displays (2026-10): one monitor `\\.\DISPLAY2`, 3120×2080, DPI 144. Re-check with `python run.py --list-monitors` — the display setup changes.
- Node/npm/git are installed but not needed by this project.
- No external smoke-test tool was downloaded. The smoke test creates its own Win32 target window.

## Required checks after edits

```powershell
$env:PYTHONUTF8 = "1"
python -m unittest discover -s tests -v
python tools\smoke_test.py
python run.py --detect   # only if WeChat is running
```

Smoke test intentionally creates a window, takes foreground/topmost briefly, moves the real mouse, clicks and drags. Do not run it while the user is presenting or using the mouse.

## Hard rules

1. Window identity, rectangles, colors, timing, display resolution groups, toolbar buttons and logging are config values. Never hardcode WeChat, HWNDs, titles, coordinates or monitor indices in Python.
2. New drawable functionality belongs in a new package under `screenpainter/features/<name>/`, registered with `@register_feature`. Do not grow `app.py` with feature-specific logic.
3. Keep the production overlay:
   - created with `WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE`;
   - owned by the target HWND (no global topmost);
   - shown with `SW_SHOWNOACTIVATE`, moved with `SWP_NOACTIVATE`;
   - click-through via `WM_NCHITTEST -> HTTRANSPARENT`.
   The toolbar panel and the drawing input window are separate topmost windows and must keep `WS_EX_NOACTIVATE`.
4. Set per-monitor DPI awareness before creating any window.
5. Use DWM extended frame bounds for normal geometry; treat minimized-window geometry specially.
6. The drawing input window (`ScreenCapture`) must keep a non-zero alpha in its layered bitmap — Windows 11 hit-tests layered windows per pixel and alpha 0 receives no mouse input.
7. Do not run the smoke test against real WeChat; use the synthetic smoke test. A brief manual `--detect`/visual check against WeChat is acceptable only when it does not disturb the user's session, and must not move/restore the real window unless necessary. Do not leave windows/processes behind.
8. If you change config schema, update `config/config.toml`, `config/examples/notepad.toml`, `docs/CONFIG.md`, tests and README together.
9. Config writes must go through `screenpainter/toml_edit.py` and must call `notify_config_saved()` so hot reload does not treat the app's own write as an external edit.
10. Validate before applying a hot reload: build/verify the new features first, then swap state. A broken config must never tear down a working overlay.
11. **Never reintroduce periodic polling of the target window.** With no events the loop must sleep (`MsgWaitForMultipleObjectsEx` on the message queue) - this is the fix for the input-method lag report. The low level keyboard hook may only be installed while an editing mode is active.
12. `Config(...)` must be constructed with `keys=keys`;
13. `WindowsWindowService.wait_for_events()` must return the real "input was waiting" result, otherwise the activity window never expires and the loop spins at 60 Hz again.

## Known local gotchas

- `rg` is not installed; use the DSH grep tool or PowerShell `Select-String`.
- `Get-CimInstance Win32_OperatingSystem` may be access-denied in the sandbox; use `[Environment]::OSVersion`.
- PowerShell here-strings piped to `python -` work; don't use `Select-Object -First` on a program's stdout (broken pipe can yield exit -1).
- Set `$env:PYTHONUTF8="1"` for redirected output with Chinese titles.
- Don't rewrite Python sources with `Set-Content -Encoding UTF8` (PowerShell 5.1 adds a BOM); use `[System.IO.File]::WriteAllText` with `UTF8Encoding($false)`.
- `ctypes` prototypes matter on x64; `GetPixel` is in gdi32, not user32.
- `LoadCursorW` needs `c_void_p` for the resource id.
- Forgetting `WS_EX_LAYERED` makes `UpdateLayeredWindow` fail with Win32 error 87.
- WeChat 4+ can have a larger visible “Photos and Videos” window from the same process; the main-window profile uses a title filter.
- Do not cache an overlay HWND across minimize/restore in tests; the app may recreate it. Re-enumerate by child PID + overlay class.
- App single-threaded; features must not block. Use `tick()` for window input polling and `keep_alive()` to keep the fast loop without a target.
- Panel button control ids are 1..N in configured order (`GetDlgItem`); the smoke test relies on that.
- Moving the panel persists `[features.toolbar] auto_position/x/y`; `--debug` writes `[app] debug` (not `[layout]`).
- Feed the `black_rectangles` feature from `LayoutStore`, never from its own options.

## Current limitations / good next tasks

- Tray icon, global exit/hide hotkey, exe packaging.
- Panel styling (currently native classic buttons), custom colors in the toolbar UI.
- Image/GIF rendering and animation (surface blit + feature `request_repaint`).
- Multi-target overlays.
- Preserving comments inside the rewritten `rectangles` array.
- A genuinely interactive desktop pet needs a new non-click-through/desktop overlay backend and input events; do not force it into the current target-owned overlay.
