# 给下一个 AI / 开发者的交接指南

最后更新：ScreenPainter 0.3.0 完成时（配置 schema version 3）。
先读 [`README.md`](../README.md) 和 [`docs/ARCHITECTURE.md`](ARCHITECTURE.md)，但**本文件记录的是本机事实和真实踩坑记录**，动手前请至少看完“关键坑位”和“运行方式”。

---

## 0. TL;DR

- 工作目录：`D:\AI\DSH\ScreenPainter`（**不是 git 仓库**，没有版本控制兜底，改前先自己备份）。
- 平台：Windows 11，x64。
- 语言：Python 3.11.7，运行期无第三方依赖（`tomllib` 是 3.11 标准库，`toml_edit.py` 是自己写的写入器；设置界面用标准库 tkinter）。
- 已实现：
  - 配置驱动窗口定位、原生透明覆盖层、任意数量实心矩形、鼠标穿透、跟随移动/缩放/最小化恢复（0.1.0）；
  - 悬浮按钮面板 + 调试模式热重载 + 分辨率/矩形组（0.2.0）；
  - **事件驱动主循环**（WinEvent 钩子，空闲 0% CPU，不再 60 Hz 轮询）（0.3.0）；
  - **`编辑` / `移动` 模式**：选中、拖动、拖边缩放、`Delete` 删除、`Ctrl+Z`/`Ctrl+Shift+Z` 撤回重做、
    方向键逐像素移动，全部快捷键在 `[keys]` 配置；选中后显示属性面板（十六进制颜色码）（0.3.0）；
  - **设置界面**（`python run.py --settings`，tkinter 独立进程）编辑配置的所有内容（0.3.0）。
- 已通过：
  - `python -m unittest discover -s tests -v`：**230 项通过**（0.2.0 是 164 项）；
  - `python tools\smoke_test.py`：合成窗口端到端冒烟测试全部通过，包括 event-driven 跟随 / hot reload /
    edit-create / properties / edit-resize / edit-delete-undo / hide / move / toolbar drag；
  - `python run.py --detect`：真实微信仍能找到主窗口（本机实测 `Weixin.exe` / `Qt51514QWindowIcon` / `Weixin`）；
  - `python run.py --settings`：设置窗口能打开、能关闭（退出码 0）。
- 修改后最低验证要求：单元测试 + 冒烟测试 + `--detect`（如果本机微信仍在运行）。
- **不要用真实微信跑冒烟测试**；用 `tools/smoke_test.py` 的合成窗口。真实微信只适合手动短暂确认，
  且不要移动/恢复用户窗口。

---

## 1. 本机环境与工具路径（实测）

| 项目 | 值 |
|---|---|
| OS | Microsoft Windows NT 10.0.26200.0（Windows 11） |
| 工作目录 | `D:\AI\DSH\ScreenPainter` |
| Python | `D:\MiniPrograms\Python3117\python.exe`，版本 3.11.7（tkinter 8.6 可用） |
| Python 启动器 | `C:\Windows\py.exe` |
| Node.js | `D:\MiniPrograms\nodejs\node.exe`，v22.22.2（本项目不需要） |
| npm | `D:\MiniPrograms\nodejs\npm.ps1`，10.9.7（本项目不需要） |
| Git | `D:\MiniPrograms\Git\cmd\git.exe`，2.55.0.windows.3（本项目目录不是 git 仓库） |
| Windows PowerShell | 5.1.26100.8875（DSH 工具里命令名是 `pwsh`，但实际是 Windows PowerShell 5.1） |
| 输入法 | Rime / 小狼毫（Weasel 0.17.4，Rime 1.13.1），用户目录 `D:\MiniPrograms\RimeData` |
| 微信 | `C:\Program Files\Tencent\Weixin\Weixin.exe`，4.1.15.13，进程 `Weixin.exe` |
| 微信主窗口类名 | `Qt51514QWindowIcon`（4.1.15.13 实测，随版本可能变化） |
| 微信主窗口标题 | 当前为 `Weixin`；配置同时接受 `WeChat` / `微信` |
| 显示环境（2026-10/11 实测） | **1 个显示器**（设备名 `\\.\DISPLAY1` 或 `DISPLAY2`，会变），3120×2080，DPI 144（150%），主屏 |
| 已安装但项目不依赖 | pytest 9.0.2、Pillow 12.3.0（**只用于我本地截图验证**）、pywin32 |

> ⚠️ 显示器设备名/编号会变。每次都先用 `python run.py --list-monitors` 确认，再决定
> `config/config.toml` 里 `[groups.*] resolution` 该写什么。默认配置同时保留了 1080p 和 3120x2080 两组。

微信进程的坑：`Get-Process -Name Weixin | Select MainWindowHandle` 里只有主进程有窗口句柄，其它几个
`Weixin.exe` 的 `MainWindowHandle` 是 0。寻找微信窗口必须用 `EnumWindows`。

---

## 2. 运行 / 测试命令

### 正常启动

```powershell
cd D:\AI\DSH\ScreenPainter
python run.py
```

或双击 `run.bat`。控制台按 `Ctrl+C` 停止。

### 设置界面

```powershell
python run.py --settings          # 或点悬浮面板的 设置 按钮
```

### 诊断

```powershell
python run.py --detect          # 目标窗口 + 显示器分辨率 + 命中的矩形组
python run.py --list-monitors   # 显示器编号/设备名/分辨率/DPI
python run.py --list-groups     # 全部矩形组 + 当前分辨率命中的组
python run.py --monitor 0       # 只看 0 号显示器分辨率下的矩形组（不修改配置）
python run.py --list-windows    # 所有可见顶层窗口（中文标题建议 PYTHONUTF8=1）
```

### 单元测试

```powershell
$env:PYTHONUTF8 = "1"
python -m unittest discover -s tests -v
```

期望结尾：`Ran 230 tests ... OK`。测试全部无窗口依赖；工具栏测试用假面板/假输入窗口/假键盘钩子，
热重载测试用假窗口服务，设置界面只测试 headless 的 model 层。

### 冒烟测试

```powershell
$env:PYTHONUTF8 = "1"
python tools\smoke_test.py
```

预期输出结尾：

```text
[smoke] click-through PASS
[smoke] movement PASS
[smoke] resize PASS
[smoke] minimize PASS
[smoke] restore PASS
[smoke] hot reload PASS
[smoke] hot reload revert PASS
[smoke] toolbar PASS
[smoke] edit-create PASS
[smoke] properties PASS
[smoke] edit-resize PASS
[smoke] edit-delete/undo PASS
[smoke] hide PASS
[smoke] move PASS
[smoke] toolbar drag PASS
[smoke] all checks passed
```

它会在桌面上创建一个白色合成窗口、短暂抢前台/置顶、模拟真实鼠标点击/拖拽/按键，然后清理。
**不要在会议或用户正在操作时运行。** 它不会碰微信。

### 临时运行（不手动 Ctrl+C）

```powershell
python run.py --duration 3 --log-level DEBUG
```

### 性能实测（0.3.0，本机 3120×2080 @150%，真实微信，隐藏/显示矩形两种情况）

| 场景 | CPU（占单核） |
|---|---|
| 目标静止，矩形可见，程序空闲 9 s | **0.000 s（0%）** |
| 目标静止，矩形隐藏（覆盖层窗口不存在），程序空闲 8 s | **0.000 s（0%）** |
| 0.2.0 对照（60 Hz 轮询，同一台机器） | ≈2.2 % |

输入法候选框延迟（`tools/_ime_probe.py`，合成 Win32 目标 + 真实小狼毫，`nihao` 逐键，单位 ms）：

| 条件 | 0.2.0 中位数 | 0.3.0 中位数 |
|---|---|---|
| 不运行 ScreenPainter | 47.4 | 46.3 |
| 运行中，矩形可见 | 63.0 | 46.3 |
| 运行中，矩形隐藏 | 62.0 | 47.8 |

结论：0.3.0 的程序对候选框弹出速度没有可测量影响；0.2.0 的 60 Hz 轮询会稳定拖慢约 15 ms，
这就是用户报告“打字卡、候选框弹得慢”的直接原因（关掉程序就不卡）。

---

## 3. 项目状态 / 文件地图

```text
run.py                          CLI 入口
config/config.toml              主配置（schema v3，默认微信，两个分辨率组）
config/examples/notepad.toml    记事本示例
screenpainter/app.py            事件驱动主循环、feature 生命周期、显示器/签名、热重载
screenpainter/cli.py            参数解析、诊断命令、配置命令、--settings
screenpainter/config.py         严格 TOML 校验（schema v3，含 [keys]）
screenpainter/toml_edit.py      保留注释的 TOML 写回（原子替换；含 remove_section/remove_key）
screenpainter/core/keymap.py    快捷键解析/校验/默认值（KeyBinding / KeyMap / ACTIONS）
screenpainter/core/layout.py    矩形组模型 + LayoutStore + save_* helper
screenpainter/core/feature.py   Feature API（tick/keep_alive/context.keys/request_config_reload）
screenpainter/core/ui.py        ButtonSpec / PanelEvent / PointerEvent / KeyEvent / PanelStatus
screenpainter/core/geometry.py  Rect/Point/Size
screenpainter/core/surface.py   预乘 BGRA 画布
screenpainter/core/tracker.py   目标 HWND 缓存/重找
screenpainter/core/window.py    Profile/Target/Info 模型
screenpainter/core/display.py   显示器模型、分辨率签名
screenpainter/platform/base.py  窗口/覆盖层/面板/输入窗口/属性面板/键盘钩子/事件源协议
screenpainter/platform/windows/win32.py        ctypes 原型与底层 helper
screenpainter/platform/windows/service.py      窗口枚举、匹配、几何、显示器、UI 工厂、消息等待
screenpainter/platform/windows/overlay.py      owned layered window（0.1.0 起基本未改）
screenpainter/platform/windows/interactive.py  ButtonPanel / ScreenCapture / PropertiesPanel
screenpainter/platform/windows/winevent.py     TargetEventListener + KeyboardHook
screenpainter/platform/windows/windows_keys.py 快捷键 -> 虚拟键码 / 匹配签名
screenpainter/features/black_rectangles/       画当前矩形组
screenpainter/features/toolbar/feature.py      悬浮面板、模式切换、键盘/指针分发、设置按钮
screenpainter/features/toolbar/editor.py       选中/拖动/缩放/创建/撤销重做/预览几何（纯逻辑）
screenpainter/settings/model.py                设置界面的 headless 模型（校验+保存）
screenpainter/settings/app.py                  设置界面（tkinter/ttk，7 个页签）
tools/smoke_test.py             端到端冒烟测试（合成目标窗口，含编辑/快捷键/撤销流程）
tools/_ime_probe.py             输入法候选框延迟测量脚本（临时诊断工具，保留作证据）
tests/                          223 项 unittest
docs/CONFIG.md                  配置字段参考（v3）
docs/ARCHITECTURE.md            架构和扩展点
docs/AGENT_GUIDE.md             本文件
logs/screenpainter.log          运行日志（git 忽略）
```

当前功能边界：

- 一个应用实例同时只跟踪**一个**目标窗口；编辑也只作用于该窗口区域。
- 覆盖层全窗透明、鼠标全穿透；悬浮面板、输入窗口、属性面板是另外的窗口（见坑位 4.12）。
- 配置默认不热重载；`[app] debug = true` 后按 `debug_interval_ms` 检查。从面板打开的设置窗口
  保存后，主程序会主动重载（`request_config_reload`）。
- 当前只支持 Windows；没有托盘、全局热键、exe 打包。

---

## 4. 关键坑位（按踩坑顺序，重要）

### 4.1 ctypes 必须声明 argtypes/restype

Windows x64 上 ctypes 默认把返回值当 32 位 `int`，HWND/HFONT/消息参数会被截断。本项目在
`win32.py` 里集中声明所有函数。

实际踩过/避开的点：

- `GetPixel` 在 **gdi32**，不在 user32；`FillRect` 在 **user32**；`TextOutW`/`SetTextColor`/`SetBkMode`/
  `CreateSolidBrush` 在 gdi32。
- `ctypes.wintypes` 没有 `PAINTSTRUCT`、`KBDLLHOOKSTRUCT`，必须自己定义结构体。
- WndProc 的 `WPARAM` 用 `c_size_t`，`LPARAM`/`LRESULT` 用 `c_ssize_t`；`DefWindowProcW` 也要声明 argtypes。
- `LoadCursorW` 的第二个参数是资源 ID：argtype 用 `ctypes.c_void_p` 传 `IDC_CROSS` 等。
- 子控件 `CreateWindowExW` 的 `hMenu` 参数可以传整数控制 ID（`wt.HMENU(1)`），`GetDlgItem(parent, 1)`
  就能拿到按钮 HWND（冒烟测试就靠这个）。
- `CreateFontW` 有 14 个参数；本机用 `-13*scale` 高度的 "Microsoft YaHei UI" 渲染中文，清晰。
  **`FW_BOLD = 700` 曾经漏定义**，属性面板创建时抛 `AttributeError`，被 `_update_properties` 的
  `except Exception` 兜住（面板禁用而不是整个 feature 崩掉）——新增 GDI 常量后记得补全。

### 4.2 `WS_EX_LAYERED` 漏掉 → UpdateLayeredWindow error 87

Layered window 必须在 `CreateWindowEx` 时显式指定 `WS_EX_LAYERED`，否则 `UpdateLayeredWindow`
返回 0，`GetLastError=87`。`overlay.py` 和 `interactive.py::ScreenCapture` 都有这条。

### 4.3 覆盖层不要抢焦点

- 创建：`WS_EX_NOACTIVATE`；显示：`ShowWindow(SW_SHOWNOACTIVATE)`；移动：`SWP_NOACTIVATE`；
  owner 设为目标窗口，`WS_POPUP`；`WM_MOUSEACTIVATE -> MA_NOACTIVATE`。
- 生产覆盖层故意**不**设置 topmost：owner 关系让覆盖层只在目标之上。

### 4.4 窗口矩形要用 DWM 可见外框

`GetWindowRect` 含不可见缩放边框；用 `DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS=9, ...)`。
本机微信实测两者差几像素。

### 4.5 最小化的窗口几何是假的

最小化后 DWM 返回 `(-31993,-32000,...)` 之类的坐标；检测 `IsIconic`，App 在最小化时直接
`overlay.hide()`，恢复时强制重绘再 `show()`。需要候选尺寸时用 `GetWindowPlacement().rcNormalPosition`。

### 4.6 微信 4.x 有多个“可见顶层窗口”

同一个 `Weixin.exe` 可能同时有 `Photos and Videos`（图片查看器）和 `Weixin`（主窗口）。默认 profile 用
`title_patterns = ["^(Weixin|WeChat|微信)$"]` 收窄。

### 4.7 鼠标穿透：样式 + 命中测试，两者都要

覆盖层使用 `WS_EX_TRANSPARENT` + `WM_NCHITTEST -> HTTRANSPARENT` + `WS_EX_LAYERED`。
冒烟测试用真实 `mouse_event` 点击黑色矩形，确认下层目标收到 `WM_LBUTTONDOWN`。

### 4.8 冒烟测试的可见性/焦点问题

- 激活目标用 `AttachThreadInput` + `SetForegroundWindow`；截图前用 `HWND_TOPMOST` 置顶（仅测试），
  结束 `HWND_NOTOPMOST`；
- 不要长期缓存 overlay HWND（最小化/恢复可能重建），每次按“子进程 PID + 类名”重新枚举；
- 测试结束必须 `proc.terminate()` + 销毁合成窗口 + 删除临时 TOML，不要留下任何窗口或进程；
- 冒烟测试会把真实鼠标移动到指定点并按下/松开（拖拽测试）和发送真实按键（Delete / Ctrl+Z）；
  它会恢复光标位置，但仍然**不要在用户操作鼠标/键盘时运行**；
- **必须把子进程的 stdout 后台读走**（`_drain_child_output`）：子进程用 `--log-level DEBUG` 且
  `stdout=PIPE`，如果不读，管道 4 KB 满了以后子进程会卡在写日志上，表现为“点几下就没反应了”。
  0.3.0 调试这个 bug 花了很久。

### 4.9 UTF-8 / 中文

- 控制台重定向时 Python 默认编码可能不是 UTF-8；先 `$env:PYTHONUTF8 = "1"`。`run.bat` 已设置。
- 配置读取用 `utf-8-sig`（容忍 BOM）；`TomlDocument.write()` 写回时**不带 BOM**。
- 日志文件固定 UTF-8。
- 面板/属性面板中文依赖系统字体（"Microsoft YaHei UI"）。

### 4.10 PowerShell / DSH 工具环境

- 当前是 Windows PowerShell 5.1；`rg` 没安装，用 DSH 的 grep 工具或 `Select-String`。
- `Get-CimInstance Win32_OperatingSystem` 可能被沙箱拒绝；用 `[Environment]::OSVersion`。
- 把程序输出管道给 `Select-Object -First N` 会提前关闭管道、Python 可能以 -1 退出。
- PowerShell 里 here-string / `python -c @'...'@` 容易丢引号；**复杂补丁请写成临时 .py 文件再执行**
  （本次 0.3.0 的文档/测试批量修改都是这么做的，改完删掉脚本）。
- **不要用 `Set-Content -Encoding UTF8` 重写 Python 源文件**：Windows PowerShell 5.1 会写 BOM。
  用 `[System.IO.File]::WriteAllText(path, text, (New-Object System.Text.UTF8Encoding($false)))`。
- 不要把中文引号/反引号拼进 PowerShell 单行命令，容易解析失败。

### 4.11 其他

- **Python 3.11+**：`tomllib` 是标准库；不要降级。
- 不要用 `Get-Process.MainWindowHandle` 找微信。
- 生产代码刻意只用 ctypes，不要引入 pywin32 作为运行依赖。
- 修改 overlay 的 GDI 资源时要先 `SelectObject` 恢复旧 bitmap，再 `DeleteObject`，最后 `DeleteDC`/`ReleaseDC`。
- App 单线程；feature 不要做阻塞 IO。

### 4.12 交互窗口（0.2.0 新增，最容易被破坏的地方）

1. **Windows 11 对 layered window 做逐像素命中测试：alpha = 0 的像素不接收鼠标。**
   输入窗口的 DIB 必须整片填 `B,G,R,A = 0,0,0,1`（不可见但可命中）。主覆盖层保持全透明 + 全穿透。
2. 输入窗口 (`ScreenCapture`) 只盖**目标窗口区域**，不盖全屏。
3. `WS_EX_NOACTIVATE` 的面板/按钮**仍然可以点击**（`BM_CLICK` 与真实鼠标都有效），但不会抢焦点。
4. 面板拖动是**自己实现**的（父窗口 `WM_LBUTTONDOWN` → `SetCapture` → 跟踪光标 → `SetWindowPos`），
   松手发 `PanelEvent(kind="moved")`。**不要在 `update()` 里重新 dock 面板**：0.3.0 一开始在
   `feature.update()` 里按 `auto_position` 重新摆放，结果用户拖面板时每次 tick 都把它拽回去，
   看起来“拖不动”。只在 `on_target_changed` / 启动时定位。
5. 输入窗口是 topmost，创建后会把面板压下去 → 每次创建/移动输入窗口后都要 `panel.raise_above()`。
6. 输入窗口的客户区坐标 = 覆盖层坐标（窗口位置/尺寸等于 `target.bounds`）。
7. `ScreenCapture` 的 `WM_SETCURSOR` 返回 TRUE 并 `SetCursor`；光标名：
   `cross` / `move` / `arrow` / `sizewe` / `sizens` / `sizenwse` / `sizenesw`。
8. 属性面板（`PropertiesPanel`）是自绘窗口，尺寸常量在 `interactive.py` 里；feature 里有一份
   `_PROPERTIES_WIDTH/_PROPERTIES_HEIGHT` 估计值，用于**创建之前**算位置（创建后会再用真实宽度纠正一次）。
   改面板布局时两处一起改。
9. 改动交互窗口后必须跑 `tools/smoke_test.py`（真实拖拽 + 点击 + 按键）。

### 4.13 配置写回（0.2.0 新增）

- `tomllib` 只能读，`tomli_w` 会破坏注释也不允许引入 → `screenpainter/toml_edit.py` 按行定点编辑。
- 写回时 `rectangles` 数组会被重新格式化为每行一个 inline table，**数组内部注释会丢**（其它地方注释保留）。
- **任何写配置的代码路径都必须调用 `context.notify_config_saved()`**：App 用它更新文件指纹，
  否则调试模式会把“自己写文件”当成外部修改再热重载一次。
- `--debug` 写 `[app] debug`，不是 `[layout] debug`。
- CLI 每个配置命令写完都会重新 `load_config()` 校验一次。
- **`Config(...)` 构造必须传 `keys=keys`**（0.3.0 一度漏了，导致 `load_config().keys` 永远是默认值，
  但非法快捷键仍然会被拒绝——很难察觉）。`tests/test_config.py` 现在有覆盖。

### 4.14 热重载（0.2.0 新增）

- 只在 `[app] debug = true`（或 `--debug on`）时生效；用 `stat` 的 `(mtime_ns, size)` 指纹。
- 顺序：**先 `_plan_features()` 构造并校验**新配置的 feature，再改 `self.config` / store / tracker / 窗口。
- 配置没变的 feature 实例会被复用（`name + type + options` 全等）。复用实例的 `context.keys`
  会在 `_apply_config()` 里更新为新配置的 `KeyMap`。
- 坏配置只报一次警告（记录被拒绝的指纹），改好后再改一次才会重新尝试。
- 目标 profile / coordinate_space / window_profiles 变化会重置 tracker 并重建覆盖层。
- 设置窗口（独立进程）保存后，工具栏在 `tick()` 里轮询子进程，退出码 0 时调用
  `context.request_config_reload()`，App 在下一轮从磁盘重新加载（同样走校验/保留旧配置的逻辑）。

### 4.15 事件驱动循环（0.3.0 新增，坑最多）

1. **`MsgWaitForMultipleObjectsEx` 传 `nCount = 0` 会立即返回**（实测），根本不等待。
   必须传至少一个对象：`win32._wait_event()` 惰性创建一个**永不 set** 的事件，`wait_for_messages()`
   用 `nCount = 1` 等它。
2. 有 1 个对象时，返回 `WAIT_OBJECT_0 + 1`（=1）表示“队列里有消息”，`WAIT_TIMEOUT`（258）才是超时。
   `win32.wait_for_messages()` 返回 `result in (0, 1)`。
3. `WindowsWindowService.wait_for_events()` 必须把上面这个真实结果返回给 App。0.3.0 一度
   `return True`（永远“有输入”），导致 activity window 永不过期、主循环又变成 60 Hz 空转，
   等于白改。**改这里要重新测空闲 CPU。**
4. WinEvent 的出队回调在 `PeekMessage`/`DispatchMessage` 时被调用；`MsgWait` 会被它们唤醒
   （实测唤醒延迟 30–50 ms）。因此事件到达后 App 会进入 0.4 s 的 16 ms activity window，
   拖动窗口期间覆盖层才跟得紧。
5. 低层键盘钩子 (`WH_KEYBOARD_LL`) 的回调**在该线程 pump 消息时被调用**，所以编辑模式下等待
   时长压到 100 ms（`keep_alive()`）+ activity 窗口 16 ms，回调延迟很小。回调里只做 4 次
   `GetAsyncKeyState` + 一次字典查找，绝不能做窗口查询/IO——超过系统 `LowLevelHooksTimeout`
   会被静默摘钩。
6. 钩子只在 `_begin_mode()` 安装、`_end_mode()` 卸载；安装时传 `{动作名: KeyBinding}`，
   只有命中的按键才 `return 1` 吞掉，其它键原样传给目标程序。
7. `KeyEvent.action` 由平台层算好（`windows_keys.binding_signature`），feature 只按动作名分支；
   键盘布局相关的虚拟键码映射集中在 `windows_keys.py`。
8. WinEvent 钩子必须在**目标窗口的线程**上安装（`SetWinEventHook` 的 `idThread`），并且过滤
   `idObject == 0 && idChild == 0 && hwnd == target`；否则输入法/无障碍事件会把队列刷爆。
9. `SetWinEventHook` 返回 0 时 App 自动退回 16 ms 轮询（`_wait_timeout_ms` 里的判断），
   所以功能不会因为钩子失败而坏掉。
10. 退出时一定要 `listener.release()`（`_unwatch_target`），否则钩子回调会打到已经销毁的对象上。

### 4.16 性能 / 干扰排查工具

- `tools/_ime_probe.py`：合成 Win32 目标 + 真实输入法，测量候选框出现/更新延迟；
  支持 `--rounds N --verbose`。它会短暂抢焦点并真的按键，跑之前确认用户不在打字。
  0.3.0 用它证明了程序对候选框延迟没有影响（数据见第 2 节）。
- 空闲 CPU：`Start-Process` 起 `run.py --duration 15`，采样 `(Get-Process -Id ...).CPU` 两次求差。
- `tools/_ime_target.py` 是上面两个探针用的合成窗口（一个 EDIT 控件），可以复用。

### 4.17 设置界面（0.3.0 新增）

- `screenpainter/settings/model.py` 是 headless 的：读原始 TOML → 暂存修改 → diff → 只写变化的键 →
  写同目录临时文件 → `load_config(temp)` 校验 → 通过才写真正的文件。**不要在 GUI 里直接写文件。**
- 设置界面是独立进程（tkinter 主循环），不占主程序资源；`--settings` 在 `cli.py` 里直接
  `from screenpainter.settings.app import main`。
- 从面板的 `设置` 按钮打开时用 `subprocess.Popen(..., creationflags=CREATE_NO_WINDOW)`，
  避免弹一个控制台窗口；退出码 0 → 主程序重载配置。
- Win+按键组合在 Tk 里拿不到 Win 修饰键状态，只能在 TOML 里手写。
- 重命名 group/profile/feature 会丢该表内部注释（`TomlDocument` 没有移动原语）。

---

## 5. 冒烟测试覆盖了什么（0.3.0）

`tools/smoke_test.py` 自己创建合成白色窗口，启动子进程 `run.py`，验证：

1. 覆盖层几何 = 目标 DWM 可见框；矩形处黑、透明处白；ex-style 正确；点击穿透到目标；
2. 目标移动/缩放后覆盖层跟随，旧位置恢复成白色；
3. 最小化隐藏、恢复后重新出现；
4. **调试模式热重载**：临时配置改成红色 → 屏幕变红 → 改回黑色 → 变黑；
5. **工具栏**：面板窗口存在、`WS_EX_TOPMOST|TOOLWINDOW|NOACTIVATE`、三个按钮都有；
6. **编辑-创建**：`BM_CLICK` 点 `编辑` → 出现 `ScreenPainterCaptureWindow` → 真实鼠标拖拽 →
   读临时 TOML 校验新矩形坐标 → 读屏幕像素校验已渲染；
7. **属性面板**：选中后出现 `ScreenPainterPropertiesPanel`（topmost / noactivate / 有尺寸）；
8. **编辑-缩放**：拖动选中矩形的右边缘 → 配置里宽度变化、高度不变；
9. **编辑-删除/撤回**：发送真实 `Delete` → 配置少一个矩形、像素变白 → 发送 `Ctrl+Z` → 矩形回来；
10. **隐藏/显示**：点 `隐藏` → 配置 `hidden=true` + 像素变白 → 点 `显示` → 恢复；
11. **移动**：点 `移动` → 拖拽第一个矩形 → 点 `完成` → 配置坐标更新 + 新旧位置像素正确；
12. **拖动面板**：真实拖拽面板标题区 → 配置里 `auto_position=false` 且 `x/y` 与窗口位置一致。

控制 ID 约定：面板按钮按 `buttons` 配置顺序从 1 开始编号（`GetDlgItem(panel, 1)` 是第一个）。

---

## 6. 不要破坏的不变量

1. **目标窗口只能在 TOML 配置里定义**；Python 中不能出现硬编码的 WeChat、坐标、矩形、显示器编号。
2. **每个 feature 独立成包**，通过 `@register_feature` 注册；不要往 `app.py` 塞业务绘制。
3. 生产覆盖层必须：有 `WS_EX_LAYERED`；是目标窗口的 owned window；有 `WS_EX_NOACTIVATE` +
   `WS_EX_TRANSPARENT`；显示用 `SW_SHOWNOACTIVATE`；移动用 `SWP_NOACTIVATE`；**不要** topmost。
4. 进程必须在任何 HWND 创建前设置 per-monitor DPI awareness。
5. 目标矩形优先 DWM visible bounds；最小化时不要使用 DWM/GetWindowRect 的伪坐标。
6. 冒烟测试不要碰真实微信，不要留下测试窗口或子进程。
7. 修改配置 schema 时同步更新：`config.py`、`docs/CONFIG.md`、`config/config.toml` 及示例、
   相关单元测试、设置界面的字段（`settings/app.py` 的对应页签）。
8. 修改 feature API 时同步更新 `docs/ARCHITECTURE.md` 和示例。
9. **写配置必须经过 `toml_edit`**；不要整份重写、不要丢注释、写完要通知 App（`notify_config_saved`）。
10. 输入窗口的 DIB 必须保持 alpha ≥ 1。
11. **不要让主循环回到固定高频轮询**：没有事件时必须真的休眠。任何“每 tick 查询窗口”的代码
    都要先想清楚它会不会把用户的输入法/前台程序拖慢（0.3.0 的全部性能工作就是为这个）。
12. 属性面板/输入窗口这类 topmost 窗口的创建/移动要跟着 DPI 缩放，位置要夹在目标窗口范围内。

---

## 7. 建议的下一步（按优先级）

1. 系统托盘 + 全局停止/隐藏快捷键。
2. 打包成单文件 exe（把 `config/`、`assets/` 作为外部可编辑目录；tkinter 注意打包）。
3. 面板/属性面板美化（自绘按钮、深色主题、图标）。
4. 属性面板可交互：直接改颜色/坐标/大小（走 `LayoutStore.replace_shapes` + 撤销栈）。
5. 图片 / GIF feature：扩展 `PixelSurface` 的 blit API；动画用 `update + request_repaint`。
6. 多目标/多个 overlay：把 App 中的 overlay 生命周期抽成 `TargetSession`。
7. 设置界面支持 Win+ 组合录制、矩形内联编辑、导入/导出配置片段。

---

## 8. 交接自检清单

- [ ] `python --version` >= 3.11。
- [ ] `python -m unittest discover -s tests -v` 通过（230 项）。
- [ ] `$env:PYTHONUTF8="1"; python tools\smoke_test.py` 通过（含 edit/properties/delete-undo）。
- [ ] 本机微信仍运行时，`python run.py --detect` 能找到主窗口并显示命中组。
- [ ] `python run.py --settings` 能打开设置窗口（`TkTopLevel`，标题 `ScreenPainter 设置`）并正常关闭。
- [ ] `python run.py --list-monitors` / `--list-groups` 输出正常。
- [ ] `config/config.toml` 里的 `[groups.*] resolution` 与当前显示器一致（或至少有一组匹配）。
- [ ] 空闲 CPU：起 `run.py --duration 15`，两次 `Get-Process.CPU` 之差 ≈ 0。
- [ ] 没有遗留 `python run.py` 子进程、合成窗口、悬浮面板、输入窗口、属性面板、临时 TOML。
- [ ] 修改过的 schema/行为已同步 README/config/architecture/CONFIG 文档和设置界面。
- [ ] 没有把真实微信窗口句柄、标题、坐标、显示器编号硬编码进 Python。
- [ ] `logs/screenpainter.log` 里没有反复出现的热重载拒绝/窗口创建失败。
