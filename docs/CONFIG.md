# 配置参考（schema version 3）

主配置文件：`config/config.toml`。程序按 UTF-8（可带 BOM）读取 TOML。
所有内容都可以用设置界面修改：`python run.py --settings`。

版本 3 相比 0.2.0（版本 2）的变化：

- 新增 `[keys]`：**所有**编辑快捷键都在这里配置（删除、撤回、重做、选中、方向键微移……）；
- `[app] tick_ms` 改成 `[app] verify_ms`：跟踪改为事件驱动（WinEvent 钩子），不再 60 Hz 轮询，
  空闲时完全不占 CPU，也不会打扰输入法/微信。`verify_ms` 只是兜底自检周期；
- `toolbar` 的 `绘制` 按钮改为 `编辑` 按钮：选中矩形后可以拖动、拖边缘缩放、按 Delete 删除、
  Ctrl+Z 撤回，并显示带十六进制颜色码的属性面板；新增 `设置` 按钮。

版本 1 → 2 的迁移：矩形从 `[features.<name>] rectangles` 移到 `[groups.<name>]`，新增 `[layout]`。

修改配置后：默认需要重启程序；把 `[app] debug` 设为 `true` 后，运行中的程序会自动重新加载
（见 [调试模式](#调试模式配置热重载)）。从悬浮面板点 `设置` 修改并保存后，运行中的程序会自动重载。

---

## 顶层结构

```toml
version = 3

[app]
...
[keys]
...
[logging]
...
[target]
...
[overlay]
...
[layout]
...
[groups.<name>]
...
[window_profiles.<name>]
...
[features.<instance_name>]
...
```

| 键 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `version` | int | 否 | `3` | 配置 schema 版本；当前只接受 `3`（`1`/`2` 会给出迁移提示） |
| `app` | table | 否 | 见下 | 主循环参数、调试模式 |
| `keys` | table | 否 | 见下 | 编辑模式的全部快捷键 |
| `logging` | table | 否 | 见下 | 日志参数 |
| `target` | table | **是** | — | 选择目标窗口 profile |
| `overlay` | table | 否 | 见下 | 覆盖层坐标空间和点击穿透 |
| `layout` | table | 否 | 见下 | 分辨率匹配 / 矩形组选择 / 隐藏开关 |
| `groups` | table | 否 | 空 | 一个或多个矩形组，按分辨率匹配 |
| `window_profiles` | table | **是** | — | 至少一个窗口匹配规则 |
| `features` | table | 否 | 空 | 一个或多个功能实例 |

配置是严格校验的：顶层、`app`、`keys`、`logging`、`target`、`overlay`、`layout`、每个 group、每个 window profile 中出现未定义的键都会直接报错退出。行内矩形表里出现未定义的键同样报错。

---

## `[app]`

| 键 | 类型 | 默认值 | 约束 | 说明 |
|---|---|---:|---|---|
| `name` | string | `"ScreenPainter"` | 非空 | 日志里显示的应用名 |
| `verify_ms` | int | `1000` | 100–10000 | 兜底自检周期（毫秒）。跟踪是事件驱动的，窗口移动/缩放/最小化会立即唤醒；这个值只决定「没有事件时多久复查一次」 |
| `idle_tick_ms` | int | `500` | 50–10000 | 目标窗口不存在时，重新查找目标窗口的间隔（毫秒） |
| `debug` | bool | `false` | — | 调试模式：运行中监视本文件并热重载 |
| `debug_interval_ms` | int | `400` | 100–5000 | 调试模式下检查文件变化的间隔（毫秒） |

```toml
[app]
name = "ScreenPainter"
verify_ms = 1000
idle_tick_ms = 500
debug = false
debug_interval_ms = 400
```

> **事件驱动**：程序用 `SetWinEventHook` 监听目标窗口的移动/缩放/显示/隐藏/最小化/还原事件，
> 有事件时立即跟随，没有事件时休眠（实测空闲 CPU 为 0%）。编辑模式和拖动目标窗口期间会短暂
> 进入 16 ms 的快循环，操作结束后自动回到休眠。这个设计是为了避免常驻轮询影响输入法候选框
> 的弹出速度。

---

## `[keys]`

编辑模式（`编辑` / `移动`）下的全部快捷键。每个值都是 `修饰键+按键` 字符串，修饰键可用
`Ctrl`、`Alt`、`Shift`、`Win`（顺序随意、大小写随意），按键可以是单个字母/数字、`F1`–`F24`，
或 `Delete` `Backspace` `Insert` `Home` `End` `PageUp` `PageDown` `Left` `Right` `Up` `Down`
`Space` `Tab` `Enter` `Esc`，以及 `-` `=` `[` `]` `\` `;` `'` `,` `.` `/` `` ` `` 等符号。

| 键 | 默认值 | 作用 |
|---|---|---|
| `cancel` | `"Escape"` | 结束编辑/移动模式 |
| `confirm` | `"Enter"` | 结束编辑/移动模式并保存 |
| `delete` | `"Delete"` | 删除选中的矩形 |
| `undo` | `"Ctrl+Z"` | 撤回上一步 |
| `redo` | `"Ctrl+Shift+Z"` | 重做被撤回的一步 |
| `select_next` | `"Tab"` | 选中下一个矩形 |
| `select_prev` | `"Shift+Tab"` | 选中上一个矩形 |
| `move_left` / `move_right` / `move_up` / `move_down` | `"Left"` / `"Right"` / `"Up"` / `"Down"` | 按 1 像素移动选中矩形（松键后保存） |
| `move_left_fast` / `move_right_fast` / `move_up_fast` / `move_down_fast` | `"Shift+Left"` 等 | 按 10 像素移动选中矩形 |

- 同一时刻不允许两个动作使用同一个快捷键（例如 `undo` 和 `redo` 都是 `"Ctrl+Z"` 会报错）。
- 进入编辑/移动模式后，这些键由 ScreenPainter 截获（低层键盘钩子），不会传给下面的程序；
  未配置的按键照常传给当前窗口，因此编辑时**不会抢走微信的键盘焦点**。
- 配置里没有 `[keys]` 表时全部使用默认值。设置界面的「快捷键」页可以按键录制。

## `[logging]`

| 键 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `level` | string | `"INFO"` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `console` | bool | `true` | 是否输出到 stderr（Windows 控制台） |
| `file` | string | `"logs/screenpainter.log"` | 相对项目根目录；空字符串关闭文件日志 |
| `max_bytes` | int | `1000000` | 单个日志文件最大字节；至少 1024 |
| `backup_count` | int | `3` | 滚动备份数量；0–100 |

命令行 `--log-level DEBUG` 会覆盖这里的 `level`。热重载**不会**改变正在使用的日志等级，需要重启（日志里会提示）。

---

## `[target]`

| 键 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `window` | string | **是** | 必须等于 `[window_profiles.*]` 中的某个键 |

```toml
[target]
window = "wechat"
```

---

## `[overlay]`

| 键 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `coordinate_space` | string | `"window"` | `"window"`（DWM 可见外框）或 `"client"`（内容区） |
| `click_through` | bool | `true` | 是否让鼠标事件穿透覆盖层 |

矩形坐标以覆盖层左上角为原点（物理像素）。`click_through = false` 只影响绘制覆盖层本身；悬浮按钮和绘制用的输入窗口是独立窗口，不受影响。

---

## `[layout]`

决定**用哪一组矩形**，以及是否隐藏它们。工具栏的“隐藏/显示”按钮会改写 `hidden`；命令行的 `--apply-group` / `--layout` 也会改写本表。

| 键 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `mode` | string | `"auto"` | `"auto"`：按分辨率自动匹配；`"manual"`：固定使用 `group` |
| `match` | string | `"monitor"` | `"monitor"`：比较目标窗口所在显示器的分辨率；`"window"`：比较目标窗口覆盖区域的尺寸 |
| `group` | string | `""` | `mode = "manual"` 时必须填写，且必须存在于 `[groups.*]` |
| `hidden` | bool | `false` | `true`：所有已保存的矩形都不显示（工具栏绘制时的预览仍然可见） |

### `mode = "auto"`

程序找出目标窗口（`overlay.coordinate_space` 对应的区域）所在的显示器，得到 `宽度x高度`，然后在 `[groups.*]` 中按文件顺序取**第一个** `resolution` 相同的组。

- 找不到匹配的组时不会画任何东西，日志会给出提示；此时用工具栏随便画一个矩形会自动创建 `auto_<宽>x<高>` 组并保存。
- 多个组可以有相同的 `resolution`（例如同一分辨率下两套不同的遮挡方案）；`"auto"` 取文件里第一个，想用后面的用 `--apply-group`。

### `mode = "manual"`

始终使用 `group` 指定的组，与当前分辨率无关。适合“这套矩形就是给这台机器/这个窗口用的”场景。

### `match`

- `"monitor"`：矩形组按**显示器分辨率**归类（1920x1080、3120x2080……）。这是默认值，也是最直观的“换分辨率就换一套矩形组”。
- `"window"`：矩形组按**目标窗口尺寸**归类（例如 `1121x685`）。当同一个窗口在不同显示器上尺寸不同、而遮挡区域随窗口尺寸变化时使用。

---

## `[groups.<name>]`

矩形组：某个分辨率（或窗口尺寸）下要画的所有矩形。`<name>` 是自由命名，但只能用字母、数字、`-`、`_`，且以字母或数字开头（命令行和写回配置时需要它是合法的 TOML 键）。

| 键 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `description` | string | 否 | `""` | 仅用于日志/命令行显示 |
| `resolution` | string | **是** | — | `"1920x1080"` 形式；`--list-monitors` 可以查显示器的值 |
| `rectangles` | table 数组 | 否 | `[]` | 矩形列表；空组合法 |

每个矩形支持：

| 键 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `name` | string | 否 | `rectangle_<序号>` | 日志/命令行可见；工具栏新建的矩形叫 `rect_<序号>` |
| `x` | int | **是** | — | 左边界；可为负，超出覆盖层部分被裁剪 |
| `y` | int | **是** | — | 上边界；可为负，超出部分被裁剪 |
| `width` | int | **是** | — | 宽度，必须 ≥ 1 |
| `height` | int | **是** | — | 高度，必须 ≥ 1 |
| `color` | string | 否 | `"#000000"` | `#RRGGBB` |
| `opacity` | number | 否 | `1.0` | 0.0–1.0；0 完全透明，1 完全不透明 |

```toml
[groups.wechat_1920x1080]
description = "WeChat mask for a 1920x1080 display"
resolution = "1920x1080"
rectangles = [
  { name = "name",     x = 430, y = 40,  width = 200, height = 30,  color = "#FAFAFA", opacity = 1.0 },
  { name = "avatar",   x = 430, y = 80,  width = 45,  height = 500, color = "#FAFAFA", opacity = 1.0 },
  { name = "contacts", x = 70,  y = 160, width = 100, height = 576, color = "#EEEEF0", opacity = 1.0 },
]
```

行为说明：

- 坐标固定在覆盖层区域，不随目标窗口内容滚动；窗口移动时整个覆盖层移动，所以矩形相对窗口位置保持不变。
- 目标窗口尺寸变化后覆盖层跟着改变尺寸；越界矩形自动裁剪。
- 多个矩形重叠时，后绘制的覆盖先绘制的像素（组内按配置顺序）。
- 工具栏写回本表时会**重写 `rectangles` 这一段**（数组内部的注释会丢失），文件其它部分（包括其它 section 的注释）保持不变。

---

## `[window_profiles.<name>]`

一个 profile 就是一条可复用的窗口匹配规则。`<name>` 可自由命名，`target.window` 引用它。

| 键 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `description` | string | `""` | 仅用于日志/诊断 |
| `process_names` | string 数组 | `[]` | 进程可执行文件名，忽略大小写，如 `"Weixin.exe"` |
| `title_patterns` | string 数组 | `[]` | Python 正则数组；匹配窗口标题（`re.search`） |
| `class_names` | string 数组 | `[]` | Win32 窗口类名精确匹配（区分大小写） |
| `exclude_title_patterns` | string 数组 | `[]` | 命中任意正则则排除 |
| `min_width` | int | `0` | 窗口最小宽度（按可见外框） |
| `min_height` | int | `0` | 窗口最小高度 |
| `select` | string | `"largest"` | 多个窗口都命中时如何选：`"largest"` / `"first"` |

匹配语义：同组值 OR，不同组之间 AND；没配置的组不参与；至少要配置 `process_names`、`class_names`、`title_patterns` 之一。

```toml
[window_profiles.wechat]
description = "WeChat / Weixin main window (3.x and 4.x)"
process_names = ["Weixin.exe", "WeChat.exe"]
title_patterns = ["^(Weixin|WeChat|微信)$"]
class_names = []
min_width = 200
min_height = 150
select = "largest"
```

微信 4.x 在查看图片时会出现同属 `Weixin.exe` 的顶层窗口 “Photos and Videos”，所以默认 profile 用标题把范围收窄。如果微信升级后标题变了，先 `python run.py --list-windows` 查实际标题/类名再改。

---

## `[features.<instance_name>]`

`<instance_name>` 是任意唯一实例名（例如 `occluder`、`toolbar`）。每个实例至少需要 `type`，并可按需设置 `enabled`。除这两个保留键外的字段会原样交给对应 feature 校验。

| 键 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---:|---|---|
| `type` | string | **是** | — | 已注册的 feature 类型：`"black_rectangles"` 或 `"toolbar"` |
| `enabled` | bool | 否 | `true` | 设为 false 可保留配置但不创建该 feature |

### 内置类型：`black_rectangles`

把当前激活矩形组的矩形画到覆盖层上。**没有自己的选项**（矩形在 `[groups.*]` 里）。

```toml
[features.occluder]
type = "black_rectangles"
enabled = true
```

> 从 0.1.0 升级的配置里如果还写着 `rectangles = [...]`，启动会报错并提示矩形已经迁移到 `[groups.<name>]`。

### 内置类型：`toolbar`（屏幕右侧悬浮按钮）

程序启动后在屏幕右侧显示一组按钮，并一直悬浮在最上层：

| 按钮 | 行为 |
|---|---|
| `编辑` | 显示所有矩形的红色描边。**点选**矩形（蓝色粗边框 + 四角小方块）；拖动矩形内部移动；拖动四条边/角缩放；在空白处拖动可以直接画出新矩形。选中后出现属性面板，显示颜色十六进制码、位置、大小、不透明度。按钮变成 `完成`。 |
| `移动` | 显示所有矩形的描边，点选后拖动移动；空白处单击取消选择。按钮变成 `完成`。 |
| `完成` | 退出当前模式（选中的矩形会立即保存，改动实时写回本文件）。 |
| `隐藏` / `显示` | 隐藏 / 恢复所有已保存矩形，同时改写 `[layout] hidden`。隐藏时覆盖层窗口**完全隐藏**，不会盖住目标窗口。 |
| `设置` | 打开设置窗口（独立进程，`python run.py --settings`），修改配置的所有内容；保存后运行中的程序自动重载。 |

编辑/移动模式下的键盘操作见 [`[keys]`](#keys)：`Delete` 删除选中矩形、`Ctrl+Z` 撤回、
`Ctrl+Shift+Z` 重做、方向键逐像素移动、`Tab` 循环选择、`Esc`/`Enter` 结束。每次松手或按键后
立即写回配置；连续按方向键会合并成一次撤回步骤，并在松键后保存。

- 拖动面板空白处（标题/状态文字区域）即可移动整组按钮；松手后把 `auto_position = false` 和新的 `x`/`y` 写回本表。
- 面板不进入任务栏/Alt+Tab，也不抢键盘焦点；编辑期间用低层键盘钩子截获上面配置的按键，
  其它按键照常传给当前窗口。
- 编辑/移动期间只有目标窗口区域被输入窗口覆盖；点击面板按钮仍然有效；点其它窗口不受影响。
- 丢失目标窗口或目标最小化时自动退出编辑模式，未保存的拖动被丢弃。

| 选项 | 类型 | 默认值 | 说明 |
|---|---|---:|---|
| `buttons` | string 数组 | `["edit", "move", "hide", "settings"]` | 显示哪些按钮及顺序；可选 `"edit"` / `"move"` / `"hide"` / `"settings"`（`"draw"` 是 `"edit"` 的旧别名） |
| `auto_position` | bool | `true` | `true`：停靠在目标窗口所在显示器右侧、垂直居中；`false`：使用 `x`/`y` |
| `x` / `y` | int | `0` | `auto_position = false` 时的屏幕坐标（物理像素） |
| `default_color` | string | `"#000000"` | 新绘制矩形的颜色 `#RRGGBB` |
| `default_opacity` | number | `1.0` | 新绘制矩形的不透明度 0.0–1.0 |
| `min_drag_size` | int | `3` | 拖拽至少多少像素才生成矩形（避免误点产生 1x1） |
| `title` | string | `"屏幕遮罩"` | 面板标题 |
| `outline_color` / `outline_width` | string / int | `"#FF3B30"` / `1` | 编辑/移动时每个矩形的描边颜色与粗细（像素） |
| `selection_color` / `selection_width` | string / int | `"#0A84FF"` / `3` | 选中矩形的粗边框颜色与粗细 |
| `handle_size` | int | `7` | 选中矩形四角缩放手柄的边长（像素） |
| `handle_tolerance` | int | `6` | 拖边缩放的判定容差（像素）：指针离边多近算抓住这条边 |
| `properties_panel` | bool | `true` | 选中矩形时显示属性面板（含十六进制颜色码） |

```toml
[features.toolbar]
type = "toolbar"
enabled = true
auto_position = true
x = 0
y = 0
buttons = ["edit", "move", "hide", "settings"]
default_color = "#000000"
default_opacity = 1.0
outline_color = "#FF3B30"
outline_width = 1
selection_color = "#0A84FF"
selection_width = 3
handle_size = 7
properties_panel = true
```

关闭悬浮按钮：`enabled = false`，或命令行 `python run.py --toolbar off`。

---

## 调试模式（配置热重载）

```toml
[app]
debug = true
debug_interval_ms = 400
```

开启后，程序每隔 `debug_interval_ms` 检查一次配置文件（用修改时间+大小判断，未变化时几乎没有开销），变化时：

1. 重新解析并校验；**校验失败只记录警告，继续使用旧配置**，不会带着半份配置运行；
2. 目标窗口 profile / 坐标空间变化 → 重新跟踪目标；
3. `overlay.click_through` 变化 → 重建覆盖层；
4. 矩形组、`[layout]` 变化 → 直接更新内存中的矩形组并立即重绘；
5. `[features.*]` 变化 → 只重建受影响的 feature（选项没变的 feature 保留，例如悬浮按钮不会闪一下）；
6. 日志等级变化不生效，需要重启。

约束：即使开启调试模式，程序也只在文件真的变了的时候重新解析；主循环频率不受影响。工具栏自己写配置时会通知程序，不会触发一次多余的重载。

命令行也可以开关：`python run.py --debug on` / `--debug off`。

---

## 分辨率 / 矩形组切换

```powershell
# 1. 看有哪些显示器（编号、设备名、分辨率、DPI）
python run.py --list-monitors

# 2. 看某个显示器分辨率下有哪些矩形组（不带其它参数时只查看，不修改配置）
python run.py --monitor 0

# 3. 查看全部矩形组
python run.py --list-groups

# 4. 应用某个矩形组（写入 [layout] mode = "manual", group = "..."）
python run.py --apply-group wechat_1920x1080

# 5. 回到按分辨率自动匹配
python run.py --layout auto

# 6. 为当前（或指定）显示器的分辨率新建一个空矩形组并立即应用
#    允许与已有组同分辨率 —— 这正是"同一分辨率多套方案"的用法
python run.py --new-group office --monitor 0
```

- `--monitor N` 的 `N` 来自 `--list-monitors` 的 `[编号]`。
- `--new-group` 用 `layout.match = "monitor"` 时取显示器分辨率；`layout.match = "window"` 时取目标窗口尺寸（需要目标窗口正在运行）。
- 这些命令都直接修改配置文件；如果程序正在运行且 `debug = true`，改动会自动生效；否则重启生效。
- 也可以完全手写配置：复制一个 `[groups.*]`，改 `resolution` 和矩形即可。

---

## 完整示例

```toml
version = 3

[app]
verify_ms = 1000
idle_tick_ms = 500
debug = false
debug_interval_ms = 400

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

[logging]
level = "INFO"
file = "logs/screenpainter.log"

[target]
window = "demo"

[overlay]
coordinate_space = "window"
click_through = true

[layout]
mode = "auto"
match = "monitor"
group = ""
hidden = false

[groups.demo_1080p]
resolution = "1920x1080"
rectangles = [
  { name = "block", x = 20, y = 20, width = 200, height = 100 },
]

[window_profiles.demo]
process_names = ["demo.exe"]
title_patterns = ["^Demo"]
select = "largest"

[features.occluder]
type = "black_rectangles"

[features.toolbar]
type = "toolbar"
enabled = true
buttons = ["edit", "move", "hide", "settings"]
```

---

## 常见配置错误

| 错误 | 原因 |
|---|---|
| `'version' must be 3` | 旧版配置；按新版重写（`[app] tick_ms` → `verify_ms`，新增 `[keys]`，`buttons` 用 `edit`） |
| `keys.undo: ...` / `duplicate shortcut(s) in [keys]` | 快捷键写法不对，或两个动作用了同一个快捷键 |
| `[features.toolbar] ... unknown option(s)` | toolbar 选项拼错，见上面的选项表 |
| `missing required table [target]` | 没有 `[target]` 表 |
| `target.window is 'x', but [window_profiles] defines: ...` | `target.window` 拼错或 profile 不存在 |
| `needs at least one of process_names, class_names or title_patterns` | profile 没有任何匹配条件 |
| `unknown key(s) in [...]` | 字段名拼错，或被放在错误的 section |
| `'layout.group' must name a [groups.<name>] table` | `mode = "manual"` 但没写 `group` |
| `layout.group is 'x', but [groups] defines: ...` | `group` 名字不存在 |
| `[groups.x] is missing required key 'resolution'` | 组没写分辨率 |
| `resolution must look like '<width>x<height>'` | 分辨率写成 `1080p`、`1920*1080p` 之类 |
| `group name 'x' must contain only letters...` | 组名有空格/点/中文等（写回配置需要合法键名） |
| `unknown feature type` | `type` 拼错，或 feature 包加载失败（查看控制台/日志） |
| `must be >= 1` | 矩形宽高不合法 |
| `configuration reload rejected ...` | 调试模式下改出了无效配置，程序继续用旧配置 |
| TOML 解析错误 | 引号、等号或数组语法写错；错误会带行号 |
