# 架构与扩展指南

本文说明 ScreenPainter 0.3.0 的分层、主循环、Win32 实现细节，以及如何在不破坏现有结构的前提下增加功能。

0.3.0 在 0.2.0（悬浮工具栏 / 热重载 / 分辨率分组）之上增加了：

1. **事件驱动主循环**：`SetWinEventHook` 监听目标窗口，空闲时完全不唤醒 CPU；不再有 60 Hz 轮询；
2. **矩形编辑**：`编辑` 模式支持选中、拖动、拖边缩放、`Delete` 删除、`Ctrl+Z`/`Ctrl+Shift+Z`
   撤回重做、方向键逐像素移动，全部快捷键可在 `[keys]` 配置；选中后显示属性面板（十六进制颜色码）；
3. **低层键盘钩子**：编辑模式下截获配置的快捷键，但不抢目标程序的键盘焦点；
4. **设置界面**（`screenpainter/settings/`，tkinter，独立进程）：编辑配置文件的所有内容。

关键设计决定：工具栏的所有修改都**写回配置文件**，配置文件始终是唯一事实来源；同时内存里维护一份 `LayoutStore`，所以即使没开调试模式，改动也会立刻显示在屏幕上。所有窗口交互仍然只发生在主线程。

---

## 设计目标

1. **配置优先**：目标窗口、矩形、颜色、时序、日志、分辨率组、按钮集合都必须能从配置修改；代码里不能出现“微信”这个业务常量。
2. **功能隔离**：每个绘制/交互功能是一个独立包，有自己的配置解析；新增功能不改主循环。
3. **平台隔离**：核心层只认识 `WindowProfile`、`WindowTarget`、`PixelSurface`、`MonitorInfo`、`ButtonSpec`、`PointerEvent` 这些抽象；Win32 只存在于 `platform/windows/`。
4. **绘制与覆盖分离**：feature 只向 surface 画像素；overlay 负责把 surface 上传到屏幕、跟随目标。
5. **回写不破坏文件**：写配置只改动对应的值/数组，注释与其它 section 保持不变。
6. **可测试**：纯逻辑可单元测试；端到端可用合成目标窗口验证，不依赖真实微信。

非目标（0.2.0 仍不实现）：系统托盘、全局热键、图片/GIF 渲染、多目标同时绘制、exe 打包、桌面级可交互宠物。

---

## 分层图

```text
┌──────────────────────────────────────────────────────────────────────┐
│ CLI (screenpainter/cli.py)                                          │
│  运行: --detect / --list-windows / --duration / --config / --log-level│
│  配置命令: --list-monitors / --list-groups / --monitor N /           │
│            --apply-group / --new-group / --layout / --shapes /       │
│            --toolbar / --buttons / --debug                           │
└───────────────┬──────────────────────────────────────────────────────┘
                │ Config（严格校验后的 dataclass）+ LayoutStore
┌───────────────▼──────────────────────────────────────────────────────┐
│ App (screenpainter/app.py)                                          │
│  主循环 · 目标生命周期 · 显示器/分辨率签名 · feature 构建/重建        │
│  调试模式：配置指纹比较 + 热重载                                     │
└──────┬───────────────────────┬───────────────────────────────────────┘
       │                       │
┌──────▼──────────┐   ┌────────▼──────────────────────────────────────┐
│ core/           │   │ features/<name>/                              │
│ geometry        │   │  black_rectangles  画 LayoutStore 的矩形       │
│ surface         │   │  toolbar           编辑/移动/隐藏/设置 + 撤销  │
│ display         │   │  （通过 FeatureContext 拿到 store + service）  │
│ layout (store)  │   │   toolbar/editor.py  选中/拖动/缩放/历史       │
│ feature API     │   └───────────────────────────────────────────────┘
│ ui (输入模型)    │
│ keymap          │   ┌───────────────────────────────────────────────┐
│ tracker         │   │ screenpainter/settings/  设置界面（独立进程）  │
│ window models   │   └───────────────────────────────────────────────┘
└──────┬──────────┘
       │ WindowService / Overlay / ButtonPanel / ScreenCapture /
       │ PropertiesPanel / KeyboardHook / TargetListener 协议
┌──────▼──────────────────────────────────────────────────────────────┐
│ platform/windows/                                                   │
│  win32.py        ctypes 原型、显示器枚举、光标/捕获、消息等待        │
│  service.py      EnumWindows、匹配、几何、显示器、UI 工厂            │
│  overlay.py      目标窗口 owned layered window（只读、全穿透）       │
│  interactive.py  ButtonPanel / ScreenCapture / PropertiesPanel       │
│  winevent.py     TargetEventListener（WinEvent）/ KeyboardHook（LL） │
│  windows_keys.py 快捷键 -> 虚拟键码                                 │
└─────────────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────────────┐
│ screenpainter/toml_edit.py   保留注释的 TOML 定点写入（原子替换）     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 启动流程

1. `run.py` → `screenpainter.cli.main()`；
2. 只读命令（`--list-windows`、`--list-monitors`）直接执行并退出；
3. 读取 TOML（`tomllib`），做严格结构和类型校验，构造 `Config`（包含 `[layout]` 和所有 `[groups.*]`）；
4. 如果有配置命令（`--apply-group` 等），修改配置文件后退出，不启动覆盖层；
5. 配置日志（控制台 + 滚动文件）；
6. 创建 `WindowsWindowService`（同时调用 `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)`，必须在创建任何窗口之前）；
7. 创建 `ScreenPainterApp`：
   - `LayoutStore`：内存中的矩形组 + 当前激活组 + 预览；
   - `TargetTracker`；
   - 记录配置文件指纹（调试模式用）；
8. `_prepare_features()`：
   - `load_builtin_features()` 自动导入 `screenpainter/features/` 下的每个子包；
   - 依配置顺序实例化启用的 feature，注入 `store` / `service` / `notify_config_saved` /
     `request_repaint` / `request_config_reload` / `keys`（解析好的 `KeyMap`）；
   - 调用 `feature.start()`。任何后期特征启动失败时，会停止已经启动的实例并退出；
9. 进入主循环。

---

## 主循环（0.3.0：事件驱动）

```python
listener = service.create_target_listener()      # WinEvent，无则退回轮询
while not stop:
    timeout = wait_timeout_ms(now)
    # MsgWaitForMultipleObjectsEx：消息、WinEvent 回调、低层键盘钩子都会唤醒它
    # 超时后返回 False；有输入时返回 True 并把 "activity window" 续期
    if service.wait_for_events(timeout):
        activity_until = now + 0.4
    tick_config_watch(now)       # 调试模式：按 debug_interval_ms 检查文件指纹
    tick_features(now)           # 取走按钮/鼠标/键盘事件（工具栏）
    apply_reload_request()       # 设置窗口关闭后请求的重载
    target = poll_target(now)    # 缓存 HWND 时做廉价校验；无目标时按 idle_tick_ms 搜索
    handle_target(target, now):
        events = listener.take_events()          # location/show/hide/destroy/...
        if target is None: notify(None); destroy overlay; unwatch
        else:
            on target hwnd change: destroy overlay; watch; notify; force_repaint
            refresh_signature(bounds)            # 显示器分辨率 -> 矩形组
            if minimized: overlay.hide(); return
            ensure overlay
            if moved: overlay.move()
            if resized: recreate surface; force_repaint = True
            update_features(target, now)
            if nothing to paint:           # 全部隐藏 / 空组
                overlay.hide()             # 不在目标窗口上留透明窗口
                return
            if force_repaint or request_repaint:
                surface.clear(); draw_features(); overlay.render(); overlay.show()
```

`wait_timeout_ms()` 的选择：

| 状态 | 等待时长 |
|---|---|
| 刚刚有输入/事件（activity window 内 0.4 s） | 16 ms（保证拖动窗口、拖拽矩形时的顺滑） |
| 有目标窗口 | `verify_ms`（默认 1000 ms）——窗口事件本身会立即唤醒 |
| 目标窗口不存在 | `idle_tick_ms`（默认 500 ms） |
| 有 feature 需要快循环（编辑模式） | `min(..., 100 ms)` |
| 调试模式 | `min(..., debug_interval_ms)` |
| WinEvent 钩子不可用 | 16 ms（退回轮询） |

要点：

- **没有事件时程序真正休眠**：实测空闲 CPU 为 0.000 s/8 s（0%），一次多余的窗口查询都没有。
  这是 0.3.0 解决“打字时输入法候选框变慢”的核心：不再有 60 Hz 轮询持续唤醒 CPU。
- WinEvent 事件的唤醒延迟实测约 30–50 ms；事件到达后进入 16 ms 的 activity window，
  因此拖动目标窗口时覆盖层仍然紧跟（冒烟测试的 movement/resize 阶段验证）。
- 目标移动时**不重绘**，只移动已有窗口；只有尺寸变化、feature 请求重绘、矩形组切换、启动/恢复时才重绘。
- `feature.tick()` 每次唤醒都调用（含没有目标窗口时）；`feature.update()` 只在有目标时调用。
  工具栏在 `tick` 里取走按钮/鼠标/键盘事件，所以目标窗口没开也能点按钮（会提示“等待目标窗口”）。
- `feature.keep_alive()` 为真时把等待时长压到 100 ms（编辑模式的兜底心跳；输入事件本身即时唤醒）。
- 所有工作都在主线程，feature 不能阻塞。输入事件由 Win32 消息队列 / 钩子进入平台对象，
  feature 在 `tick` 里取走，没有线程和锁。
- 平台没有 `create_target_listener`/`wait_for_events` 时（例如单元测试的假 service），
  主循环退化为 `pump_events()` + 短暂 sleep，行为仍然正确。

---

## 核心模块

### `core/display.py`

- `MonitorInfo`：编号、设备名、bounds、work area、是否主屏、DPI；`resolution` 返回 `宽x高`。
- `DisplayInfo`：显示器集合 + 虚拟桌面；`monitor_for(rect)` 取与矩形重叠面积最大的显示器（跨屏窗口按“用户看到的大部分”归属），完全在屏幕外时回退主屏。
- `normalize_resolution()`：接受 `1920x1080` / `1920 X 1080` / `1920×1080`，统一成小写 `x`。
- `resolve_signature(match, bounds, displays)`：`match = "monitor"` 用显示器分辨率，`"window"` 用覆盖层尺寸。

### `core/layout.py`

`Shape` / `LayoutGroup` / `LayoutSettings` 三个不可变模型，加上运行期 `LayoutStore`：

- `groups`、`settings`（mode/match/group/hidden）、`signature`、`_active_name`；
- `update_signature()`：分辨率变化后重算激活组，返回是否变化；
- `draw_shapes()`：`hidden` 时省略已保存矩形；叠加 `preview`（绘制预览/拖动高亮）；`replace_base` 时用预览整体替换；
- 修改类方法（都会写回配置文件）：`add_shapes()`、`replace_shapes()`、`set_hidden()`、`set_active_group()`、`set_mode()`、`ensure_group_for_drawing()`；
- `apply_config()`：热重载时原地替换数据，**保持对象身份**，这样所有 feature 持有的引用继续有效。

写回失败（文件只读等）只记录错误日志，内存状态仍然生效，界面不会因为磁盘问题卡死。

### `core/feature.py`

```python
class Feature:
    type_name: ClassVar[str]
    def __init__(self, context: FeatureContext, options: Mapping[str, Any])
    def start(self) -> None
    def stop(self) -> None
    def on_target_changed(self, target: WindowTarget | None) -> None
    def tick(self, now: float) -> None                       # 每帧，含无目标时
    def update(self, target: WindowTarget, now: float) -> None
    def draw(self, surface: PixelSurface, target: WindowTarget) -> None
    def request_repaint(self) -> None
    def keep_alive(self) -> bool                              # 默认 False
```

`on_target_changed(None)` 的含义是“目标暂时不可用”：窗口被关闭，或用户最小化了窗口（此时覆盖层隐藏）。目标恢复可用时会再用同一个 target 回调一次。工具栏据此放弃正在进行的绘制/移动，避免透明输入窗口继续盖住桌面。

`FeatureContext` 提供：

| 字段/方法 | 用途 |
|---|---|
| `instance_name` / `type_name` | 配置实例名和类型 |
| `project_root` / `config_path` / `config_dir` | 路径上下文 |
| `assets_dir` / `asset_path(rel)` | 访问 `assets/` 下素材，防目录穿越 |
| `logger` | 带实例名的 logger |
| `request_repaint()` | 请求下一帧重绘 |
| `store` | 运行期矩形组（读写） |
| `service` | 平台窗口服务（显示器、按钮面板、输入窗口） |
| `notify_config_saved()` | 自己写了配置文件后通知 App，避免触发多余热重载 |

- 构造时**校验自己的 options**，未知字段直接抛 `FeatureError`；
- 生命周期由 App 调用；任何 feature 的 tick/update/draw 异常会被记录并禁用该 feature，其它 feature 和主循环继续运行；
- 构造/启动阶段的异常是致命的（配置应该修正后再启动）；热重载时构造失败会保留旧配置。

### `core/ui.py`

平台无关的输入模型：`ButtonSpec`（按钮 key + 文案 + 是否可用）、`PanelEvent`（`button` / `moved`）、`PointerEvent`（`down`/`move`/`up`/`cancel` + 客户区坐标）。feature 只见到这些，不接触 Win32 消息。

### `core/tracker.py`

`TargetTracker` 缓存当前 HWND：有缓存时每帧低成本校验，失效才全量 `EnumWindows`；保持稳定的目标，避免在多个候选窗口之间跳。

### `screenpainter/toml_edit.py`

`tomllib` 只能读。为了在**不破坏注释和格式**的前提下写配置，这个模块按行做定点编辑：

- 用一个小状态机跳过字符串/注释/括号，识别“顶层的 section 头”和“顶层的 `key = value`”；
- 找到后整段替换（支持跨行数组），没找到就插入到 section 末尾，section 不存在就创建；
- 字符串正确转义；只支持本程序实际写入的值类型；
- `write()` 先比较新旧文本（忽略 CRLF 差异），一样就不写；写入用临时文件 + `os.replace`，断电/崩溃不会留下半个文件。

---

## Win32 后端

### 窗口发现与显示器（`platform/windows/service.py`）

- `EnumWindows` 枚举所有顶层窗口，过滤自身进程和覆盖层类名；
- `IsWindowVisible` / `IsWindow` / `IsIconic` / `DwmGetWindowAttribute(DWMWA_CLOAKED)`；
- `GetWindowThreadProcessId` + `QueryFullProcessImageNameW` 取进程路径（缓存 PID→路径）；
- 几何：正常窗口用 `DwmGetWindowAttribute(DWMWA_EXTENDED_FRAME_BOUNDS)`；最小化窗口用 `GetWindowPlacement().rcNormalPosition`（DWM 此时返回 -32000 附近的伪坐标）；client 空间用 `GetClientRect` + `ClientToScreen`；
- `list_monitors()`：`EnumDisplayMonitors` + `GetMonitorInfoW(MONITORINFOEXW)` + `GetDpiForMonitor`（shcore，失败回退 96）；
- `virtual_desktop()`：`GetSystemMetrics(SM_XVIRTUALSCREEN...)`。

### 覆盖层（`platform/windows/overlay.py`，0.1.0 起不变）

```text
style:    WS_POPUP
exstyle:  WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
owner:    target HWND
```

- **WS_EX_LAYERED 必须显式加在 CreateWindowEx 中**，否则 `UpdateLayeredWindow` 报 Win32 error 87；
- owner 是目标 HWND：覆盖层只在目标之上，目标被别的窗口盖住时黑块不会浮出来（生产代码**不设置 topmost**）；
- `WS_EX_TRANSPARENT` + `WM_NCHITTEST -> HTTRANSPARENT` 实现鼠标穿透；
- 内容通过 `UpdateLayeredWindow` 上传 32 位 top-down 预乘 BGRA DIB；
- 移动用 `SetWindowPos(SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)`。

### 交互窗口（`platform/windows/interactive.py`，0.2.0 新增）

**`ButtonPanel`**：`WS_POPUP | WS_CLIPCHILDREN`，`WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE`，类背景刷为系统按钮面色；子控件是真正的 Win32 `BUTTON` / `STATIC`（所以中文文案由系统渲染，高 DPI 清晰）。

- 按钮 id 按配置顺序从 1 开始，点击通过 `WM_COMMAND` 进入事件队列；
- 面板没有标题栏：父窗口收到 `WM_LBUTTONDOWN`（按钮/静态文本之外的区域）后 `SetCapture` + 跟踪光标移动窗口，松手发 `moved` 事件，工具栏据此把新位置写回 `[features.toolbar]`；
- `WS_EX_NOACTIVATE` 下按钮**仍然可以点击**（实测），但不会把焦点从微信抢走；
- `raise_above()` 把面板提到 topmost 带的最前，保证绘制期间按钮仍可点。

**`ScreenCapture`**：`WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST`，位置/尺寸等于目标覆盖区域，`WM_NCHITTEST -> HTCLIENT`、按下时 `SetCapture`，因此拖出目标窗口也能收到坐标（可能为负）。它自己不画预览——预览由 `black_rectangles` 通过 `LayoutStore.preview` 画到主覆盖层上，60 Hz 路径的开销和原来一样。

> **本机实测的坑：Windows 11 对 layered window 做逐像素命中测试，alpha = 0 的像素不接收鼠标。** 因此输入窗口的 DIB 用 `alpha = 1`（几乎不可见的黑色）整片填充，否则整块区域点击穿透，绘制完全没反应。主覆盖层保持全透明 + 全穿透，不受影响。

其余只读帮助：`EnumDisplayMonitors`、`GetDpiForMonitor`、`GetCursorPos`/`SetCursorPos`、`LoadCursorW`/`SetCursor`、`SetCapture`/`ReleaseCapture`、`CreateFontW`。

### DPI

进程必须在创建任何 HWND 之前调用：

```python
SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
```

本机实测主屏 3120×2080 @ 150%（dpi=144）。按钮面板的字号和尺寸按所在显示器的 `dpi/96` 缩放，否则 150% 下会又小又难点。

---

## 调试模式 / 热重载

调试模式由 `[app] debug` 控制（命令行 `--debug on` 也能写）。App 每 `debug_interval_ms` 做一次 **stat 比较**（mtime_ns + size），只有指纹变化才重新 `load_config`：

1. 解析/校验失败 → 记录一次警告，**继续使用旧配置**；同一份坏文件只报一次（记录被拒绝的指纹）；
2. 解析成功 → `_plan_features()` **先构造**新配置需要的 feature（这一步完成 options 校验），失败则整体放弃，旧配置和旧窗口不受影响；
3. 计算目标绑定是否变化（profile / coordinate_space / 窗口规则）→ 变化则停止跟踪、销毁覆盖层、重建 tracker；
4. `store.apply_config()`：原地更新矩形组与 layout（对象身份不变）；
5. `_activate_features()`：停止不再需要的 feature、启动新构造的 feature；**配置没变的 feature 复用原实例**（悬浮按钮不会闪、绘制状态不会丢）；
6. 强制重绘、重算分辨率签名。

工具栏/命令行的写入都通过 `notify_config_saved()` → App 更新自己的文件指纹，所以“自己写文件”不会被当成外部修改再重载一次。

---

## 扩展菜谱

### A. 新增绘制 feature

1. 建包：

```text
screenpainter/features/image_badge/
├─ __init__.py
└─ feature.py
```

2. `__init__.py` 只负责导入 feature 类，触发注册：

```python
from .feature import ImageBadgeFeature

__all__ = ["ImageBadgeFeature"]
```

3. `feature.py`：

```python
from screenpainter.core.feature import Feature, register_feature
from screenpainter.core.surface import Color, PixelSurface
from screenpainter.core.window import WindowTarget


@register_feature("image_badge")
class ImageBadgeFeature(Feature):
    def __init__(self, context, options):
        super().__init__(context, options)
        self._interval = float(options.get("blink_interval", 0.5))
        self._phase = 0.0

    def update(self, target: WindowTarget, now: float) -> None:
        self._phase = now
        self.request_repaint()

    def draw(self, surface: PixelSurface, target: WindowTarget) -> None:
        if int(self._phase * 2) % 2 == 0:
            surface.fill_rect(20, 20, 40, 40, Color.from_hex("#FF3B30"))
```

4. 配置：

```toml
[features.badge]
type = "image_badge"
enabled = true
blink_interval = 0.5
```

无需修改 `features/__init__.py`。自动发现只导入**子包**，跳过下划线开头的包；导入失败会记录日志并跳过该功能。

### B. 新增一个可交互面板

参考 `toolbar`：在 `platform/base.py` 里加协议（或复用 `ButtonPanel`/`ScreenCapture`），在 `platform/windows/` 实现窗口，feature 只通过协议和 `core/ui.py` 的事件模型交互。窗口消息由 `service.pump_events()` 分发，事件在 feature 的 `tick()` 中消费——不要在窗口过程里直接改应用状态。

### C. 增加窗口 profile

复制一份 `[window_profiles.*]`，改 `process_names` / `title_patterns` / `class_names`，然后设置 `target.window`。不要改 Python 代码。

### D. 新增平台后端

实现 `platform/base.py` 里的 `WindowService`、`Overlay`、`ButtonPanel`、`ScreenCapture` 协议，并在 `screenpainter/platform/__init__.py` 的 `create_window_service()` 中按 `sys.platform` 选择。核心层和 feature 不需要改动。

### E. 新的配置写入

用 `screenpainter.toml_edit.TomlDocument`（或 `core/layout.py` 里的 `save_*` helper），不要 `open(..., "w")` 整份重写，也不要引入 `tomli_w`（保持零运行时依赖）。写完调用 `context.notify_config_saved()`。

---

## 错误处理与不变量

- 配置错误：启动前抛出 `ConfigError`，CLI 返回 2；不会带着半份配置运行。
- 热重载配置错误：只警告，继续运行旧配置。
- feature 构建/启动错误：抛出 `FeatureError`，已经启动的 feature 会被停止。
- 运行时 overlay 创建/更新失败：记录日志、销毁并延迟重建；主循环不崩。
- 运行时 feature tick/update/draw 异常：记录堆栈、停用该 feature、继续其它 feature。
- 配置写回失败：记录错误，内存状态保留。
- 不要移除 `WS_EX_LAYERED`、DPI awareness、owner 关系和 `SWP_NOACTIVATE`；这是渲染和不打扰用户的基础。
- 不要把目标窗口名、坐标、尺寸、显示器编号写进 Python。配置是唯一业务入口。

---

## 测试设计

- `tests/test_geometry.py`、`test_surface.py`、`test_window.py`、`test_config.py`、`test_tracker.py`、`test_features.py`：纯逻辑/无窗口；
- `tests/test_display.py`：显示器模型、分辨率签名、跨屏归属；
- `tests/test_layout.py`：矩形组匹配、预览合成、隐藏、写回配置；
- `tests/test_toml_edit.py`：定点编辑、注释保留、原子写、只读时不写；
- `tests/test_toolbar.py`：用 **假面板/假输入窗口** 驱动完整状态机（绘制、确定、隐藏、移动、面板拖动、选项校验）；
- `tests/test_app_reload.py`：假窗口服务驱动的热重载（忽略/生效/拒绝坏配置/目标 profile 变化/自有写入不重载）；
- `tools/smoke_test.py`：真实 Win32 端到端：
  - 覆盖层几何、像素、点击穿透、跟随移动/缩放、最小化隐藏/恢复；
  - **调试模式热重载**（把矩形改成红色再改回来，读屏幕像素）；
  - **悬浮按钮**（类名、topmost/noactivate/toolwindow 样式、按钮存在）；
  - **绘制**（BM_CLICK 绘制 → 真实拖拽 → BM_CLICK 确定 → 读配置 + 读像素）；
  - **隐藏/显示**（配置 `hidden` 与像素）；
  - **移动**（真实拖拽后配置与像素）；
  - **拖动面板**（配置 `auto_position/x/y` 与窗口位置一致）。
- 冒烟测试会创建合成窗口、短暂抢前台/鼠标；不要用它去测试真实微信，也不要在会议中运行。

---

## 已知限制

- Windows-only；覆盖层无法覆盖某些受保护/独占全屏内容。
- 同一时间只跟踪一个目标窗口；绘制/移动也只作用于该窗口区域。
- 绘制模式期间目标窗口区域不接受点击（这是交互所需）；面板本身仍可点。
- 面板用系统经典按钮样式（无清单/comctl32 v6），外观朴素但清晰、支持中文与高 DPI。
- 工具栏新建矩形使用默认颜色/不透明度，不能在面板里改颜色（改配置即可）。
- 热重载不会重建日志 handler；日志等级改动需要重启。
- 目标窗口被移动/缩放时矩形仍按窗口外框坐标固定，不跟随窗口内部滚动。
- 如果目标以更高权限运行，进程名可能查不到。
