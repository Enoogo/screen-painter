# ScreenPainter（窗口遮挡画板）

一个配置驱动的 Windows 窗口覆盖工具：在任意被配置命中的窗口上绘制任意数量、任意尺寸的实心
矩形，并在窗口移动、缩放、最小化、恢复时自动跟随。用 Python + Win32（ctypes）实现，
**零第三方运行时依赖**，同时附带一个可视化设置界面（tkinter，标准库）。

默认配置定位 **微信（WeChat / Weixin）主窗口**。所有窗口匹配条件、矩形位置、尺寸、颜色、
分辨率分组、按钮集合、快捷键都写在 TOML 配置里，代码中不硬编码“微信”。

> 版本：0.3.0（配置 schema version 3）
> 平台：Windows 10 / 11（当前开发验证环境为 Windows 11 Build 26200）
> Python：3.11 及以上（配置解析使用标准库 `tomllib`）

## 当前已经实现

- **窗口定位**：读取 `target.window`，在 `[window_profiles.*]` 中按进程名、标题正则、窗口类名、
  最小尺寸匹配窗口；优先使用 DWM 可见外框，最小化/恢复自动处理。
- **遮挡绘制**：按窗口坐标画实心矩形；覆盖层是全透明、鼠标穿透、跟随目标窗口 z 序的原生分层窗口。
- **事件驱动、空闲零开销**：用 WinEvent 钩子监听目标窗口的移动/缩放/显示/隐藏/最小化/还原；
  有变化时立即跟随，没有变化时程序完全休眠（实测空闲 CPU 0%）。不会像固定 60 Hz 轮询那样
  持续唤醒 CPU，因此不影响输入法候选框的弹出速度。
- **悬浮按钮面板**：启动后一直浮在屏幕右侧，可以拖动整组按钮的位置：
  - `编辑`：显示所有矩形的描边；点选矩形（蓝色粗边框 + 四角手柄），拖动内部移动、拖动四条边/角缩放、
    空白处拖动直接画新矩形；选中后弹出属性面板，显示**十六进制颜色码**、位置、大小、不透明度；
  - `移动`：显示描边，点选后拖动移动；
  - `隐藏` / `显示`：隐藏或恢复所有矩形（隐藏时覆盖层窗口完全隐藏，不会盖住目标窗口）；
  - `设置`：打开设置窗口，修改配置文件的**所有**内容。
- **完整的编辑快捷键（全部可配置）**：`[keys]` 里配置删除、撤回、重做、循环选中、方向键逐像素
  移动、10 像素快移、退出/确认；编辑时用低层键盘钩子截获，不会抢走目标程序的键盘焦点。
- **撤回 / 重做**：每个矩形修改都记入历史；连续按方向键合并为一步，松键后写回配置。
- **设置界面**：`python run.py --settings`（或面板的 `设置` 按钮）打开，分区编辑
  `[app]`、`[keys]`、`[logging]`、`[target]`、`[overlay]`、`[layout]`、矩形组/矩形、
  窗口 profile、feature 选项；保存前用正式的配置校验器验证，注释按原样保留。
- **调试模式 / 配置热重载**：`[app] debug = true` 后，运行中修改配置文件会自动重新加载
  （校验失败则继续用旧配置），默认关闭。
- **分辨率 / 矩形组**：矩形按分辨率分组，`[layout] mode = "auto"` 按显示器分辨率自动选组，
  `"manual"` 固定使用某一组；命令行可以列出显示器、列出/应用矩形组、为同一分辨率新建另一套方案。
- **严格校验**：未知字段、非法数值、无效正则都会在启动/保存时报错，不会带着半份配置运行。

## 快速开始

```powershell
cd D:\AI\DSH\ScreenPainter
python run.py
```

或双击 `run.bat`。需要 Python 3.11+（本机：`D:\MiniPrograms\Python3117\python.exe`）。
控制台按 `Ctrl+C` 停止。

1. 打开微信（或 `config/config.toml` 里配置的目标窗口）；
2. 屏幕右侧出现悬浮面板，状态栏显示当前矩形组；
3. 点 `编辑` → 在微信窗口上拖出一个矩形（立刻写入 `config/config.toml`）；
4. 点住矩形选中它：拖动内部移动、拖边缩放、`Delete` 删除、`Ctrl+Z` 撤回、方向键微调；
5. 点 `完成` 退出编辑；点 `隐藏` / `显示` 临时去掉/恢复遮挡；
6. 点 `设置` 修改颜色、快捷键、矩形、目标窗口等所有配置。

## 配置入口

配置文件：`config/config.toml`（schema version 3），完整字段见
[docs/CONFIG.md](docs/CONFIG.md)。所有内容都能用 `python run.py --settings` 修改，
下面是手改时最常用的部分。

### 1. 换目标窗口

```toml
[target]
window = "wechat"          # 必须是下面某个 [window_profiles.*] 的名字

[window_profiles.wechat]
process_names = ["Weixin.exe", "WeChat.exe"]
title_patterns = ["^(Weixin|WeChat|微信)$"]
select = "largest"
```

用 `python run.py --list-windows` 查看当前所有可见窗口的进程名/类名/标题。

### 2. 矩形组（按分辨率）

```toml
[layout]
mode = "auto"              # auto = 按分辨率自动选组；manual = 固定 group
match = "monitor"          # monitor = 显示器分辨率；window = 目标窗口尺寸
hidden = true              # 工具栏"隐藏"按钮会改这里

[groups.wechat_3120x2080]
resolution = "3120x2080"
rectangles = [
  { name = "name", x = 644, y = 60, width = 200, height = 50, color = "#FAFAFA", opacity = 1.0 },
]
```

`python run.py --list-monitors` 打印显示器分辨率；`--new-group NAME --monitor 0` 新建一套。

### 3. 悬浮按钮

```toml
[features.toolbar]
type = "toolbar"
enabled = true
auto_position = false
x = 2616
y = 590
buttons = ["edit", "move", "hide", "settings"]
default_color = "#000000"
default_opacity = 1.0
outline_color = "#FF3B30"
selection_color = "#0A84FF"
```

`buttons` 可选项：`edit`（`draw` 是旧别名）、`move`、`hide`、`settings`。

### 4. 快捷键

```toml
[keys]
cancel = "Escape"
confirm = "Enter"
delete = "Delete"
undo = "Ctrl+Z"
redo = "Ctrl+Shift+Z"
select_next = "Tab"
select_prev = "Shift+Tab"
move_left = "Left"
move_right = "Right"
move_up = "Up"
move_down = "Down"
move_left_fast = "Shift+Left"
move_right_fast = "Shift+Right"
move_up_fast = "Shift+Up"
move_down_fast = "Shift+Down"
```

写法是 `修饰键+按键`，修饰键 `Ctrl`/`Alt`/`Shift`/`Win`，按键可以是字母、数字、`F1`–`F24`、
`Delete`、`Left`、`Escape`、`Tab` 等。`[keys]` 整段省略时使用上面的默认值。

### 5. 调试模式 / 事件驱动

```toml
[app]
verify_ms = 1000           # 没有窗口事件时的兜底复查间隔
idle_tick_ms = 500         # 目标窗口不存在时重新查找的间隔
debug = false              # true = 运行中监视并热重载本文件
debug_interval_ms = 400
```

## 常用命令

### 运行与诊断

```powershell
python run.py                        # 正常运行
python run.py --settings             # 打开设置界面
python run.py --detect               # 只检查目标窗口/分辨率/命中组
python run.py --list-windows         # 所有可见顶层窗口
python run.py --list-monitors        # 显示器编号/设备名/分辨率/DPI
python run.py --duration 5           # 运行 5 秒后自动退出
```

### 显示器与矩形组

```powershell
python run.py --list-groups
python run.py --monitor 0
python run.py --apply-group wechat_3120x2080
python run.py --layout auto
python run.py --new-group office --monitor 0
```

### 开关功能

```powershell
python run.py --buttons edit,move,hide,settings
python run.py --shapes off           # 隐藏所有遮挡（写 [layout] hidden=true）
python run.py --toolbar off
python run.py --debug on
```

## 工作方式与安全边界

- 覆盖层是目标窗口的 **owned window**（`WS_POPUP`，owner = 目标 HWND），带
  `WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE`：
  全透明、鼠标全穿透、不抢焦点、不进 Alt+Tab，只在目标窗口之上；
- **跟踪是事件驱动的**：`SetWinEventHook` 只监听目标窗口线程的移动/缩放/显示/隐藏/最小化事件；
  事件到来时只调用 `SetWindowPos`/重绘一次；没有事件时程序休眠。编辑和拖动窗口期间会短暂进入
  16 ms 快循环，空闲后自动回到休眠；
- 内容没有变化时不重绘；所有矩形都隐藏/为空时覆盖层窗口**直接隐藏**，不在目标窗口上留一层透明窗口；
  程序不长期持有屏幕 DC（只保留一个内存 DC 用于离屏绘制）；
- 悬浮面板是独立的 topmost 小窗口（原生按钮、不抢焦点、不进任务栏），只在目标窗口区域
  临时盖一层透明输入窗口做拖拽，因此不会挡住其它程序；
- 编辑快捷键由低层键盘钩子截获，**只在编辑模式期间安装**，并且只吞掉 `[keys]` 里配置的键，
  其它按键照常传给当前程序；
- 设置界面是独立进程：`run.py --settings` 读取/校验/写回同一个 TOML 文件；从面板打开时，
  关闭设置窗口后主程序会自动重载。

## 目录结构

```text
run.py                          CLI 入口
config/config.toml              主配置（schema v3，默认微信，两个分辨率组）
config/examples/notepad.toml    记事本示例
screenpainter/app.py            主循环（事件驱动）、feature 生命周期、热重载
screenpainter/cli.py            参数解析、诊断命令、配置命令、--settings
screenpainter/config.py         严格 TOML 校验（schema v3，含 [keys]）
screenpainter/core/keymap.py    快捷键解析 / 校验 / 默认值
screenpainter/core/layout.py    矩形组模型 + LayoutStore + 写回 helper
screenpainter/core/feature.py   Feature API（tick/keep_alive/context.keys…）
screenpainter/core/ui.py        ButtonSpec / PanelEvent / PointerEvent / KeyEvent / PanelStatus
screenpainter/platform/windows/win32.py        ctypes 原型与底层 helper
screenpainter/platform/windows/service.py      窗口枚举、匹配、几何、UI 工厂、消息等待
screenpainter/platform/windows/overlay.py      owned layered window
screenpainter/platform/windows/interactive.py  ButtonPanel / ScreenCapture / PropertiesPanel
screenpainter/platform/windows/winevent.py     WinEvent 钩子 + 低层键盘钩子
screenpainter/features/black_rectangles/       画当前矩形组
screenpainter/features/toolbar/                悬浮面板、编辑/移动/隐藏、撤销重做、属性面板
screenpainter/settings/                        设置界面（tkinter，独立进程）
tools/smoke_test.py             端到端冒烟测试（合成目标窗口，含编辑/快捷键/撤销流程）
tests/                          单元测试（230 项）
docs/CONFIG.md                  配置字段参考（v3）
docs/ARCHITECTURE.md            架构和扩展点
docs/AGENT_GUIDE.md             给下一个 AI/开发者的交接指南
logs/screenpainter.log          运行日志（git 忽略）
```

## 测试

```powershell
$env:PYTHONUTF8 = "1"
python -m unittest discover -s tests -v    # 230 项单元测试
python tools\smoke_test.py                 # 端到端冒烟测试（会短暂抢焦点、动鼠标）
```

冒烟测试自己创建一个白色 Win32 目标窗口，启动子进程，验证：

- 覆盖层几何 = 目标 DWM 可见框、像素正确、点击穿透到目标；
- 目标移动/缩放后覆盖层跟随、最小化隐藏、恢复后重新出现；
- 调试模式热重载（改成红色再改回黑色）；
- 悬浮面板存在且是 topmost / 不抢焦点；
- 点 `编辑` → 拖出新矩形 → 配置文件与屏幕像素正确；
- 选中后属性面板出现；
- 拖动右边缘缩放并写回配置；
- `Delete` 键盘删除选中矩形、`Ctrl+Z` 撤回；
- 隐藏/显示、`移动`模式拖动、拖动面板保存位置。

## 故障排查

### 提示 `Target: NOT FOUND` 或一直等待窗口

```powershell
python run.py --detect
python run.py --list-windows
```

确认目标窗口在运行、`[window_profiles.*]` 的条件能命中它。

### 黑块位置不对

矩形坐标是**覆盖层左上角**为原点的物理像素。用 `--detect` 看 `overlay:` 的矩形，或者直接用
`编辑` 模式拖一个新矩形对比。

### 启动后什么都没画

- 检查 `[layout] hidden` 是否为 `true`（面板点 `隐藏` 会写它，`--shapes on` 可恢复）；
- 检查当前显示器分辨率是否命中某个 `[groups.*]`（`--detect` 会打印命中的组）；
- 点 `编辑` 画一个矩形，程序会自动创建匹配当前分辨率的新组。

### 按钮点了没反应

- 编辑/移动需要目标窗口存在；面板状态栏会显示“等待目标窗口”；
- 编辑模式只在目标窗口区域接收拖拽。

### 快捷键没反应

- 快捷键只在 `编辑` / `移动` 模式下生效；先点其中一个按钮；
- 确认 `[keys]` 里的写法合法（启动时会严格校验，两个动作不能共用同一个键）。

### 配置报错

错误信息会指出具体字段。最常见的是 schema 版本（旧配置需要迁移）和字段拼写。
设置界面保存前会做同样的校验，不会把坏配置写进去。

### 日志

`logs/screenpainter.log`（UTF-8）。`python run.py --log-level DEBUG` 可临时打开调试日志。

### 覆盖层不出现

目标窗口最小化时覆盖层会隐藏（恢复后自动出现）。另外，所有矩形都隐藏或为空时覆盖层窗口
会被完全隐藏，这是有意行为。

## 已知限制 / 后续路线

- 一个应用实例同时只跟踪**一个**目标窗口；
- 编辑/移动期间目标窗口区域被输入窗口覆盖（面板仍可用）；
- 系统托盘图标 / 全局开关热键 / 单文件 exe 打包尚未实现；
- 只支持 Windows。

## 文档与交接

- 配置字段参考：[docs/CONFIG.md](docs/CONFIG.md)
- 架构与扩展点：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **下一个 AI/开发者必读**：[docs/AGENT_GUIDE.md](docs/AGENT_GUIDE.md)：本机路径、版本、
  测试方法，以及开发过程中实际踩过并解决的 Win32/ctypes/事件钩子坑。
