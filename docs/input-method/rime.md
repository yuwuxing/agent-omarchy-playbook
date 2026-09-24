# Omarchy 中文输入法：从“能输入”到“顺手”的 Agent 配置指南

> 核对日期：2026-09-10。本文面向接手新电脑的 Agent，包含可复制的配置、执行顺序、验收标准与回滚方法。本文聚焦 **Omarchy + Hyprland + Wayland + Fcitx5**，记录本机验证过的配置。Arch 是 Omarchy 的系统基础；其他发行版和桌面不在本仓库的部署范围内。

这次配置的目标，是尽量接近用户习惯的微信输入法：中英文切换自然，中文状态能插入英文，候选框跟随输入位置，文字清晰，长句模型可以试用但不影响退回原版。

最后最有价值的改变不是换一套输入法，而是两件事：**让 Rime 独占 Shift 切换，并避免把额外文字缩放叠加进 Chromium/Electron 的设备缩放。**

## 1. Agent 先读：部署目标与操作边界

新电脑的目标状态如下。

| 层级 | 目标 |
|---|---|
| 显示 | 保留原生 Wayland；文字缩放先保持默认，显示比例按屏幕调整 |
| 输入框架 | 仅运行一个 Fcitx5 实例 |
| 输入方案 | 雾凇全拼 `rime_ice`，可选长句版 `rime_ice_long` |
| Fcitx 切换键 | `Ctrl+Space`；主键与备用键列表中都没有 Shift |
| Rime 切换键 | 左右 Shift 都为 `commit_code` |
| 输入中按 Shift | 提交原始字母，切入英文 |
| 输入中按回车 | 提交原始字母，保持中文状态 |
| 按住 Shift 输入字母 | 输入大写，不因组合键而切换中英文 |
| 词库 | 雾凇已有英文与混输词库，保留原排序作为基线 |
| 长句模型 | 万象 LTS，可切回原版，两方案共用 `rime_ice` 用户词库 |
| 可恢复性 | 修改前备份；已有配置做定向合并，不整体覆盖 |

执行时遵循以下原则：

- 先检查环境与现有配置，再决定是否安装或修改。本文中的操作授权由新任务的用户请求决定。
- 所有用户路径通过 `$HOME`、`XDG_CONFIG_HOME` 和 `XDG_DATA_HOME` 获取，不写死原电脑用户名。
- Omarchy 的 `/usr/share/omarchy/` 只读，定制放在用户目录。若环境提供 omarchy 技能，先读对应技能。
- 不清空 Rime 用户目录，不删除 `*.userdb`、`sync/` 或个人短语，不从正在使用的浏览器配置中顺带读取无关数据。
- 修改显示或启动参数后，必须验证真实窗口。把中文文本粘贴到网页不能证明 IME 正常。
- 不把模型文件存在、菜单中出现方案或一次部署命令返回成功当作最终验收。
- 新系统版本若与本文不同，以本机帮助、当前上游文档及实测为准；不要为了复刻本文而降级整套系统。

## 2. 这台机器最终是什么状态

以下是本文写作时的实际快照，不是对任意 Linux 机器的默认假设。

| 项目 | 实际值 |
|---|---|
| Omarchy | 4.0.2 |
| Hyprland | 0.56.2 |
| Fcitx5 / Fcitx5-Rime | 5.1.21 / 5.1.14 |
| librime | 1.17.0，已有 Lua、octagram 插件 |
| 雾凇源码 Git HEAD | `75e6572bebc05b49021e842949ce947882e3e4b2` |
| 当前基础方案版本字段 | `2026-03-08`，这是方案字段，不等于仓库发布日期 |
| 当前选中方案 | `rime_ice`，原版雾凇拼音 |
| 已安装备选 | `rime_ice_long`，雾凇·长句模型 |
| 显示器 | 3840 × 2160，显示缩放 1.25 |
| Omarchy TEXT SIZE | 12px，即本机默认值 |
| GTK `text-scaling-factor` | 1.0 |
| Chrome / ChatGPT | 原生 Wayland，无强制设备缩放参数 |
| Fcitx 服务 | `omarchy-fcitx5.service` 正常运行 |

长句模型曾被切入试用，后来检查时当前选中的是原版；不能在新机器上声称“长句模型默认更好”。基础方案排第一，长句方案排第二。

还有两项应区分的历史遗留：

1. `hypr/autostart.lua` 仍有 `fcitx5 -d`，但系统服务已经负责启动。新电脑**不要复制这条重复启动命令**。本文整理阶段未修改原电脑的这项残留。
2. 普通拼音的 `pinyin.conf` 还在，但当前使用的是 Rime。修改其中的云拼音、预测和模糊音，不会改变当前雾凇方案。

## 3. 前置检查、备份与依赖

### 3.1 识别环境

在目标用户的图形会话内运行，避免从另一个 root 或 SSH 会话误读输入法状态。

```bash
cat /etc/os-release
printf '%s\n' "$XDG_SESSION_TYPE" "$XDG_CURRENT_DESKTOP"
pgrep -a -x fcitx5
pgrep -a -x ibus-daemon
systemctl --user status omarchy-fcitx5.service --no-pager
```

最后一个服务在非 Omarchy 系统上可能不存在，这不是输入法损坏。检测到其他输入框架时先确认桌面依赖，不要直接卸载或杀掉 GNOME 自带组件。

定义后续示例使用的目录。下面的 shell 片段假定在同一个 Bash 会话中执行；若 Agent 每次调用都会新建 shell，必须重新设置变量。

```bash
IME_CONFIG_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}"
IME_DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}"
RIME_DIR="$IME_DATA_ROOT/fcitx5/rime"
IME_WORK="$PWD/work/ime-setup"
IME_BACKUP="$IME_WORK/backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$IME_WORK" "$IME_BACKUP"
```

备份计划修改的配置，包括 `fcitx5/`、相关应用 flags 文件、Hyprland 个人设置、Rime 的 `*.custom.yaml` 和附加方案。新机器原有的词库也必须保护。

如需迁移完整用户学习库，先使用 Rime 同步功能，或者在停止输入法、释放数据库锁后复制完整用户目录；不要把运行中的 LevelDB 目录复制结果当作一致性备份。用于公开文章或问题报告时，不上传个人词库。

### 3.2 安装组件

Omarchy 上优先使用它的包管理入口：

```bash
omarchy pkg add fcitx5 fcitx5-configtool fcitx5-gtk fcitx5-qt fcitx5-rime librime noto-fonts-cjk git curl python
```

需要系统更新时遵守 Omarchy 的更新流程，不做 Arch 部分升级。权限提升使用目标环境规定的方法。

`fcitx5-chinese-addons` 是普通拼音等引擎，不是本方案运行 Rime 的必要条件；已安装时也无需删除。

检查 Rime 插件：

```bash
ls /usr/lib/rime-plugins/
fc-match 'Noto Sans CJK SC'
```

这台 Arch 机器的 librime 包已提供 `librime-lua.so` 和 `librime-octagram.so`。其他发行版可能拆成独立包，Agent 应查询包内容补齐，而不是假设所有系统都有相同包名或 `/usr/lib` 布局。Lua 是雾凇扩展所需，octagram 是语法模型所需；`librime-predict` 的“下一词预测”与本文长句模型不是一回事。

同时检查 Rime 核心共享库的动态链接完整性，预防 Arch/Omarchy 滚动更新导致 `opencc` 与 `librime` 的 soname 不匹配（例如缺少 `libopencc.so.1.2` 导致模块静默损坏打不出字）：

```bash
ldd /usr/lib/fcitx5/librime.so | grep -i "not found"
```

若出现缺失，需同步升级运行库：`sudo pacman -Syu opencc librime fcitx5-rime`。

## 4. 启动与应用通信：先保证每一层工作

### 4.1 只保留一个启动入口

Omarchy 已有 `omarchy-fcitx5.service` 时，检查服务是否启用并运行。只有尚未启用时才在图形会话内启用它：

```bash
systemctl --user enable --now omarchy-fcitx5.service
```

不要同时添加 Hyprland autostart、桌面 autostart 和第二个用户服务。原机器服务参数包含 `--disable notificationitem`，这是 Omarchy 自己渲染状态项时的选择，不要泛化为所有桌面必需参数。

若目标机不属于 Omarchy，停止套用本部署流程，先向用户说明适用范围。不同桌面的协议背景可参考[Fcitx Wayland 指南](https://fcitx-im.org/wiki/Using_Fcitx_5_on_Wayland/en)

### 4.2 环境变量不能机械地“一套全局通用”

原机器的 `environment.d/im.conf` 为：

```ini
GTK_IM_MODULE=fcitx
QT_IM_MODULE=fcitx
XMODIFIERS=@im=fcitx
SDL_IM_MODULE=fcitx
GLFW_IM_MODULE=ibus
```

这是**现存配置记录**，不是新机器必须逐字照抄的清单。

- 先保留发行版已有的正确设置，再补缺少的应用支持。
- 原生 Wayland GTK 应用可以走原生 text-input 协议；遇到问题时用单应用环境对照，不能认定全局 `GTK_IM_MODULE=fcitx` 总是最好。
- `XMODIFIERS` 服务于 X11/XWayland 客户端；Qt 是否需要 `QT_IM_MODULE` 取决于 Qt 和合成器支持。
- `GLFW_IM_MODULE=ibus` 不代表应该再启动一个 IBus 守护进程。Fcitx 支持部分 IBus 协议兼容用途。
- 修改环境变量不会自动更新已经启动的应用；登录会话、启动器和用户服务也可能继承不同环境。

验收时用 Fcitx 调试信息确认应用连接，不只看当前 shell 的 `printenv`：

```bash
gdbus call --session --dest org.fcitx.Fcitx5 \
  --object-path /controller \
  --method org.fcitx.Fcitx.Controller1.DebugInfo
```

输出中的 `frontend:wayland_v2` 是 Fcitx 前端名称，不能据此直接推断应用采用的是 text-input-v2；应用侧 text-input 与输入法侧 input-method 是不同接口。

## 5. 安装雾凇：先固定来源，再增加个人补丁

本方案基于[雾凇拼音](https://github.com/iDvel/rime-ice)。要复现本文，可从上面的 Git 提交开始；要用新版，则记录新版提交并重新测试，不要无记录地跟随 `main`。

```bash
git clone https://github.com/iDvel/rime-ice.git "$IME_WORK/rime-ice-src"
git -C "$IME_WORK/rime-ice-src" checkout 75e6572bebc05b49021e842949ce947882e3e4b2
```

对于全新的空 Rime 目录，可复制源码内容。以下脚本会拒绝覆盖非空目录，并跳过仓库 `.git` 与预编译 `build`，后续由本机重新部署：

```bash
export RIME_DIR IME_WORK
python - <<'PY'
from pathlib import Path
import os, shutil
src = Path(os.environ['IME_WORK']) / 'rime-ice-src'
dst = Path(os.environ['RIME_DIR'])
dst.mkdir(parents=True, exist_ok=True)
if any(dst.iterdir()):
    raise SystemExit('Rime 目录非空：先备份并定向合并，禁止整体覆盖。')
for p in src.iterdir():
    if p.name.startswith('.') or p.name == 'build':
        continue
    if p.is_dir():
        shutil.copytree(p, dst / p.name)
    else:
        shutil.copy2(p, dst / p.name)
PY
```

对于已有输入法的机器：先备份、比较目录，再更新上游词库和方案文件；保留个人 `*.custom.yaml`、`custom_phrase.txt`、用户词库与同步资料。不要照搬上游“清空用户目录”的简化安装步骤。

### 5.1 Fcitx 输入法列表

在配置工具中设置默认组为“英语键盘 + Rime”，默认中文引擎为 Rime。全新配置的 `$IME_CONFIG_ROOT/fcitx5/profile` 可采用：

```ini
[Groups/0]
Name=Default
Default Layout=us
DefaultIM=rime

[Groups/0/Items/0]
Name=keyboard-us
Layout=

[Groups/0/Items/1]
Name=rime
Layout=

[GroupOrder]
0=Default
```

已有其他键盘布局或语言时，保留它们，通过配置工具合并，不替换整个 profile。

## 6. 中英切换：一个按键只交给一层负责

这是用户明确反馈“体验提升很多”的改动。

### 6.1 Fcitx 不再拦截 Shift

全新 `$IME_CONFIG_ROOT/fcitx5/config` 的相关内容如下。已有文件必须合并相应段落，不能用这段抹掉全部全局设置。

```ini
[Hotkey]
EnumerateWithTriggerKeys=False
ModifierOnlyKeyTimeout=250

[Hotkey/TriggerKeys]
0=Control+space

[Hotkey/AltTriggerKeys]
```

**空的 `AltTriggerKeys` 段是有意保留的。** 只删除主切换键里的 Shift 不够：本机运行时默认备用切换键还有 `Shift_L`。清空整个主键列表中的 Shift 条目，并显式清空备用列表，不要只按某个序号删行。

### 6.2 Rime 负责左右 Shift

基础阶段 `$RIME_DIR/default.custom.yaml`：

```yaml
patch:
  schema_list:
    - schema: rime_ice
  "ascii_composer/switch_key/Shift_L": commit_code
  "ascii_composer/switch_key/Shift_R": commit_code
```

增加长句方案后，再在 `schema_list` 追加 `rime_ice_long`。已有补丁应合并，避免第二个 `patch:` 或重复键。

`commit_code` 表示输入中按 Shift 时提交原始字母并进入英文；按住 Shift 输入大写不应被当作单按切换。这是 Rime 自身的切换语义。[雾凇键位定义](https://github.com/iDvel/rime-ice/blob/main/default.yaml)

没有增加“右 Shift 临时英文”模式，没有改动全局状态记忆。本文快照中 Fcitx `ShareInputState=No`，Rime `InputState=All`；这不是统一状态策略的推荐，只表示本轮刻意保留用户已经适应的行为。

**重要排查：Omarchy 默认键盘选项截获 Shift**

Omarchy 在 `/usr/share/omarchy/default/hypr/input.lua` 中默认配置了 `shift:both_capslock_cancel`。该选项在 XKB 层面会将实体 Shift 键的释放（release）事件拦截或映射为 CapsLock 取消动作，导致输入法（无论是 Fcitx 还是 Rime）无法稳定捕获单按 Shift 切换中英文。

解决方案是在用户配置文件 `~/.config/hypr/input.lua` 中覆盖键盘选项，移除 `shift:both_capslock_cancel` 并保留 CapsLock 作为 Compose 键：

```lua
hl.config({
  input = {
    kb_options = "compose:caps",
  },
})
```

保存后执行 `hyprctl reload`，并用 `hyprctl getoption input:kb_options` 确认已变为 `str: compose:caps`。如果用户有自定义多语言布局或特殊 XKB 参数，需合并原参数，不要盲目整体覆盖。

### 6.3 混输先利用现成能力

雾凇已经加载 `melt_eng` 和 `cn_en`，英文候选、固定中英混合词、网址和邮箱识别都有基础配置。第一轮不全局提高英文权重，不导入额外大词库。

实际使用目标：

- 中文状态输入 `docker`，可以选英文候选，也可以回车提交字母后接着输入中文。
- 连续英文或代码用 Shift 切入英文。
- 输入字母时的回车提交，与没有预编辑文字时应用收到回车，是不同状态；不要承诺回车永远不会发送聊天消息。
- 技术缩写若不好选，收集具体词后加个人短语；不要为了几个缩写把所有英文排到中文前面。

这些设置接近用户熟悉的操作方式，但不等于复刻微信输入法的预测、纠错和任意整句混输能力。

## 7. 模糊音与候选外观

原用户启用的模糊音写在 `$RIME_DIR/rime_ice.custom.yaml`：

```yaml
patch:
  "speller/algebra/+":
    - derive/^([zcs])h/$1/
    - derive/^([zcs])([^h])/$1h$2/
    - derive/^l/n/
    - derive/^n/l/
    - derive/in$/ing/
    - derive/ing$/in/
```

分别对应平翘舌、n/l、in/ing 双向模糊音。为同一用户迁移时保留；为其他用户部署时按需要启用，不将所有模糊音视作普遍优化。候选范围扩大可能增加歧义。

当前 `$IME_CONFIG_ROOT/fcitx5/conf/classicui.conf` 的外观设置：

```ini
Vertical Candidate List=False
PerScreenDPI=True
Font="Noto Sans CJK SC 13"
MenuFont="Noto Sans CJK SC 12"
Theme=Tokyonight-Night
DarkTheme=Tokyonight-Night
UseDarkTheme=True
UseInputMethodLanguageToDisplayText=True
```

原机主题资源在 Fcitx5 用户数据目录的 `themes/Tokyonight-Night/`。新电脑若没有该主题，先使用已安装主题，或迁移完整主题目录；不能只复制主题名。候选数由 Rime 的 `menu/page_size` 控制，当前是 5。Linux 外观用 Fcitx 配置，不能套用小狼毫、鼠须管的皮肤字段。

## 8. Wayland 候选框错位：最终有效的解决路径

现象：浏览器搜索框正在输入 `wo`，候选框却出现在上方；ChatGPT 底部输入时，候选框偏到窗口中部。误差随输入位置变化，不能用一个固定偏移量修复。

本机对照表：

| 设置 | 定位 | 显示与字号 |
|---|---|---|
| 原生 Wayland + 额外 GTK 文字缩放约 1.3636 | 明显错位 | 字体较大且清晰 |
| XWayland | 独立 Chrome 测试定位正常 | 界面变小；再用合成器小数放大时明显模糊 |
| Wayland + `--force-device-scale-factor=1` | 独立 Chrome 测试定位正常 | 界面变小，用户不接受 |
| Wayland + 强制设备缩放约 1.359375 | 仍错位 | 没有解决问题 |
| **Wayland + 默认文字大小 + 显示缩放 1.25** | **用户实际使用反馈正常** | **可读且保留原生显示效果** |

因此最终没有留下 XWayland 绕过，也没有留下强制设备缩放参数。

这与[Omarchy 上游问题 #7559](https://github.com/omacom/omarchy/issues/7559)的后续讨论相符，但只能认定为该版本组合下得到验证的触发条件与规避方式，不应声称所有 Chromium/Wayland 的候选框问题都源于文字缩放。

### 8.1 Omarchy 上如何复现有效设置

本机通过显示面板设置 TEXT SIZE 为默认 12px，SCALE 为 1.25×。Agent 可以先查询：

```bash
omarchy display text size
gsettings get org.gnome.desktop.interface text-scaling-factor
hyprctl monitors -j
```

目标值分别为默认文字大小、GTK 比例 1.0，以及适合目标屏幕的显示比例。原机 4K 屏幕选 1.25，不代表所有屏幕都应强制 1.25。

需要通过 CLI 恢复默认文字时，可以运行 `omarchy display text size 12`，但应先了解它同时会修改 Omarchy 面板、GTK 字体比例和终端字号。已有用户自定义终端字号时，需要保存并按意图恢复。

原机 `monitors.lua` 的有效设置如下；新机器应编辑现有输出配置，不复制未知显示器名，不覆盖多屏布局：

```lua
local omarchy_gdk_scale = 1
local omarchy_monitor_scale = 1.25
hl.env("GDK_SCALE", tostring(omarchy_gdk_scale))
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = omarchy_monitor_scale })
```

修改 Hyprland Lua 后运行：

```bash
hyprctl reload
hyprctl configerrors
```

Omarchy 原有的 `xwayland.force_zero_scaling=true` 已恢复默认。本文目标是原生 Wayland，不需要新增这项覆盖。

### 8.2 应用启动参数

Chrome/Chromium 保留已有密码存储、扩展等参数，只确保对应的启动入口采用：

```text
--ozone-platform=wayland
```

不要残留 `--ozone-platform=x11` 或本次试验加入的 `--force-device-scale-factor=1`。不要因为网上教程就堆叠 `--gtk-version=4`、text-input v1/v3 和旧 Ozone 开关。

原机 Chrome 包装脚本读取 `chrome-flags.conf`，Chromium 使用相应 flags 文件；**原机的** `/usr/bin/chatgpt` 包装脚本读取 `codex-flags.conf`。这是本机包装脚本实现，不是所有 ChatGPT/Codex Linux 安装包都支持的标准接口。新机器必须先读启动脚本或 `.desktop` 的 `Exec=`，确认参数确实传递到进程。

启动参数改变后完全退出再打开应用。先保存未提交内容，不强制杀死用户会话。用 `hyprctl clients -j` 确认目标窗口 `xwayland=false`。字体设置改变后如果表现未更新，也要在重开应用后再判断。

### 8.3 全屏 Foot 终端下候选框消失：Hyprland 单窗口直接扫描优化绕行

Omarchy 默认使用 `Super + Return` 启动 foot 终端。普通窗口和按 `Super + Alt + F` 最大化时可以正常显示输入法候选窗，但在按 `Super + F` 进入真全屏后，候选窗可能会消失。

**机理分析**：这不是 Fcitx5 或 Rime 的故障，而是完全不透明的全屏 foot 窗口触发了 Hyprland 的单窗口直接渲染/扫描优化（direct scanout），合成器跳过了图层合成，导致作为 Wayland layer-shell 弹出的输入法候选窗未被合成渲染。

**有效规避规则**：在 `~/.config/hypr/hyprland.lua` 末尾添加如下窗口规则：

```lua
o.window("foot", {
  opacity = "1 1 0.999 override",
})
```

前两个 `1` 保持普通状态下的活动与非活动窗口透明度不变，第三个 `0.999 override` 将全屏透明度固定为 `0.999`。肉眼看起来仍然完全不透明，但足以阻止合成器触发单窗口直接扫描优化，让 `Super + F` 全屏状态下继续正常合成候选窗。

修改后执行重载并检查：

```bash
hyprctl reload
hyprctl configerrors
```

确保 `hyprctl configerrors` 无任何报错输出。

## 9. 长句模型：保留原版，增加可比较的备选

我们使用[雾凇官方语法模型配方](https://github.com/iDvel/rime-ice/blob/main/others/recipes/grammar.recipe.yaml)推荐的万象 LTS 简体模型。它本地运行，不是调用在线大模型，也不是“上屏后预测下一词”。

2026-09-09 下载快照：

- 文件名：`wanxiang-lts-zh-hans.gram`
- 大小：420,339,756 字节，约 401 MiB
- SHA-256：`1eb651288b117e68374110b32796346e128be790e994559cb7c1e3b0fd63029d`

[LTS 发布资产](https://github.com/amzxyz/RIME-LMDG/releases/tag/LTS)会更新。上面的哈希用于识别本次快照，不保证将来同名 URL 仍返回同一文件。

### 9.1 下载并校验

下面脚本需要 Python 3.11 或更新版本，使用标准库查询当前发布资产并核对大小和 GitHub 提供的 SHA-256；元数据缺失或文件已存在时不会静默覆盖。

```bash
export RIME_DIR IME_WORK
python - <<'PY'
from pathlib import Path
import os, json, urllib.request, hashlib, shutil
work = Path(os.environ['IME_WORK'])
work.mkdir(parents=True, exist_ok=True)
root = Path(os.environ['RIME_DIR'])
name = 'wanxiang-lts-zh-hans.gram'
url = 'https://api.github.com/repos/amzxyz/RIME-LMDG/releases/tags/LTS'
with urllib.request.urlopen(url, timeout=60) as response:
    release = json.load(response)
asset = next(a for a in release['assets'] if a['name'] == name)
expected = asset.get('digest', '')
if not expected.startswith('sha256:'):
    raise SystemExit('发布资产没有 SHA-256：核对来源后再继续，不跳过校验。')
target = root / name
if target.exists():
    raise SystemExit('模型已存在：先校验现有文件，升级时另行备份替换。')
part = work / (name + '.part')
with urllib.request.urlopen(asset['browser_download_url'], timeout=60) as src, part.open('wb') as dst:
    shutil.copyfileobj(src, dst)
with part.open('rb') as f:
    actual = 'sha256:' + hashlib.file_digest(f, 'sha256').hexdigest()
if part.stat().st_size != asset['size'] or actual != expected:
    raise SystemExit('大小或 SHA-256 不一致，停止安装。')
shutil.copy2(part, target)
(work / 'model-asset.json').write_text(json.dumps(asset, ensure_ascii=False, indent=2))
print('已安装并校验：', target, actual)
PY
```

这校验的是发布来源与下载文件一致，不是独立的软件签名验证。网络失败时保留基线输入法可用，下载完成前不要启用依赖模型的新方案。

### 9.2 生成独立方案

原版 `rime_ice` 不加模型。新增 `rime_ice_long.schema.yaml`，基础内容复制已选择版本的雾凇源文件，只改变方案标识并追加个人补丁和模型设置。

**不能只写一个没有顶层 `schema:` 的 `__include` 文件。** 本机 Fcitx5-Rime 部署预检查会报告 `invalid schema definition`。此外，上游源 YAML 中可能存在 YAML-cpp 接受、PyYAML 却拒绝的制表符；因此这里用带断言的文本替换保留上游原文。

```bash
export RIME_DIR
python - <<'PY'
from pathlib import Path
import os
root = Path(os.environ['RIME_DIR'])
src = root / 'rime_ice.schema.yaml'
dst = root / 'rime_ice_long.schema.yaml'
if dst.exists():
    raise SystemExit('长句方案已存在，先比较与备份，避免重复追加。')
s = src.read_text()
assert s.count('  schema_id: rime_ice\n') == 1
assert s.count('  name: 雾凇拼音\n') == 1
assert '\n__patch:' not in s, '上游结构已变化，需要重新合并补丁'
s = s.replace('  schema_id: rime_ice\n', '  schema_id: rime_ice_long\n', 1)
s = s.replace('  name: 雾凇拼音\n', '  name: 雾凇·长句模型\n', 1)
s += '''
# Inherit personal patches and share the original learning dictionary.
__patch:
  - rime_ice.custom:/patch
  - translator/user_dict: rime_ice
    translator/contextual_suggestions: false
    translator/max_homophones: 5
    translator/max_homographs: 5
    grammar:
      language: wanxiang-lts-zh-hans
      collocation_max_length: 7
      collocation_min_length: 2
      collocation_penalty: -10
      non_collocation_penalty: -20
      weak_collocation_penalty: -35
      rear_penalty: -12
'''
dst.write_text(s)
print('已生成：', dst)
PY
```

这里对万象模型的语法参数进行了调优：
- `collocation_min_length: 2`（原为 3）：将搭配最小长度设为 2，使高频二字搭配也能获得语言模型打分与排序优化。
- `weak_collocation_penalty: -35`（原为 -100）：大幅平滑弱搭配惩罚，避免合法但低频的组合被严厉抑制。
- `translator/max_homographs: 5`：限制同形字词的最大建议数，保持候选列表精简。

这里要求 `rime_ice.custom.yaml` 已存在；不启用个人模糊音的新用户也可保留一个 `patch: {}` 文件。对于其他用户复杂的个人补丁，先检查它是否覆盖模型键或方案标识，再合并。

`translator/user_dict: rime_ice` 明确让两个方案共用原用户词库。不要默认使用独立的新词库，否则对照会混入学习历史差异。

最终 `default.custom.yaml`：

```yaml
patch:
  schema_list:
    - schema: rime_ice
    - schema: rime_ice_long
  "ascii_composer/switch_key/Shift_L": commit_code
  "ascii_composer/switch_key/Shift_R": commit_code
```

新方案是基础方案的快照。以后更新雾凇源码时，也要重新生成、比较并部署长句版，不能只更新原版后让两个方案永久漂移。

### 9.3 模型效果要诚实报告

我们用 12 句日常与工作句子、无个人历史的隔离词库做了对照：原版和模型版首选完全正确都是 **11/12**。

- “我已经把修改后的文件发送给你了”：原版出现“发送给力了”，模型版正确。
- “我们需要在保证质量的前提下提高效率”：原版正确，模型版出现“质量得前提下”。

引擎测试中整句逐键处理时间中位数约 39ms → 55ms，样本最大单键约 8.3ms → 9.1ms。它不含用户打字时间、窗口渲染和端到端延迟，也不是通用性能基准。

因此模型是可试用选项，不能承诺全面变准。当前稳定配置默认保留原版，F4 可进入方案菜单切换长句版。

## 10. 部署、读取实际状态与验收

### 10.1 应用配置

写 Fcitx 配置时先确认没有正在编辑的未提交文字。通过配置工具保存，或修改文件后立即重载；不要修改文件后再让旧进程退出保存，把新文件覆盖回旧状态。

```bash
gdbus call --session --dest org.fcitx.Fcitx5 \
  --object-path /controller \
  --method org.fcitx.Fcitx.Controller1.ReloadConfig

gdbus call --session --dest org.fcitx.Fcitx5 \
  --object-path /controller \
  --method org.fcitx.Fcitx.Controller1.SetConfig \
  fcitx://config/addon/rime/deploy '<@a{sv} {}>'
```

这些接口在本机已验证。新版本先通过 `gdbus introspect` 确认；接口不可用时使用 Fcitx5 配置工具的重新部署功能，不猜测方法名。部署可能在后台进行，等待生成文件与错误日志检查完成后再切换方案。

### 10.2 不能省略的状态检查

```bash
gdbus call --session --dest org.fcitx.Fcitx5 \
  --object-path /controller \
  --method org.fcitx.Fcitx.Controller1.GetConfig fcitx://config/global

gdbus call --session --dest org.fcitx.Fcitx5 \
  --object-path /controller \
  --method org.fcitx.Fcitx.Controller1.FullInputMethodGroupInfo "Default"

gdbus call --session --dest org.fcitx.Fcitx5 \
  --object-path /rime --method org.fcitx.Fcitx.Rime1.ListAllSchemas

gdbus call --session --dest org.fcitx.Fcitx5 \
  --object-path /rime --method org.fcitx.Fcitx.Rime1.GetCurrentSchema
```

确认全局 `TriggerKeys` 只有预期框架切换键、`AltTriggerKeys` 为空；`build/default.yaml` 中两个 Shift 都为 `commit_code`。`FullInputMethodGroupInfo` 应确认包含 `keyboard-us` 与 `rime`。

若安装了长句版，确认 `build/rime_ice_long.schema.yaml` 真正存在且含 `grammar`，并与原版比较 `speller`、`engine`、`key_binder`、`switches`，这些不应被无意改变。`build/` 是生成结果，只读取验收，不把直接编辑它作为持久配置。

用 F4 选长句版，或在接口存在时：

```bash
gdbus call --session --dest org.fcitx.Fcitx5 \
  --object-path /rime --method org.fcitx.Fcitx.Rime1.SetSchema rime_ice_long
```

在模型方案实际建立输入上下文后，Linux 上可检查目标 Fcitx5 进程的 `/proc/<pid>/maps` 是否映射 `wanxiang-lts-zh-hans.gram`。权限不足时改用日志或功能对照。切回原版后映射可能仍缓存，不能只凭 maps 判断当前选中方案。

### 10.3 用户可感知的验收矩阵

在不发送消息的本地测试页或安全草稿中，用真实键盘事件测试。覆盖 Chrome 地址栏、网页输入框、ChatGPT 编辑区和一个终端。

| 操作 | 通过标准 |
|---|---|
| 单按左右 Shift | 两者都能切换中英文 |
| 输入 `docker` 后按 Shift | 提交原字母并进入英文 |
| 输入 `docker` 后按回车 | 提交原字母，下一段仍能输中文 |
| 按住 Shift 输入 A | 得到大写，不误切换 |
| 输入常见英文 | 有可用英文候选，不要求任意缩写都首选 |
| 上下输入框来回切换 | 候选框随当前输入位置更新 |
| 多行、滚动、移动窗口 | 不停在旧位置，不远离文字 |
| 显示 1.25×，默认文字比例 | 清晰度与定位均可接受 |
| F4 切换原版与模型版 | 两方案可用，切换习惯相同 |
| 重开应用或重新登录 | 配置仍有效，只有一个 Fcitx5 实例 |

做长句 A/B 时，每次输入完整拼音，中途不选词；观察首选后 Esc 清除，再换方案输入同样内容。尽量不提交以减少学习影响，但这不等同于严格实验隔离。正式比较应使用独立用户词库，绝不把测试用例灌入真实学习库。

完成后交付实际读取的状态、修改文件、备份位置、通过与未通过项。没有验证真实应用，就明确写“配置已部署，应用验证待完成”。

## 11. 回滚与常见误区

回滚优先使用最小动作：

- 模型不好用：F4 切回 `rime_ice`；无需删除模型或用户词库。
- Shift 不符合习惯：恢复本次备份的 Fcitx 全局配置与 `default.custom.yaml`，重载并部署；保留后续其他修改。
- 定位异常：检查文字比例是否又不为 1.0、应用是否改变后端，再逐项对照。
- 显示变糊：检查是否切成 XWayland、是否新增合成器整体拉伸。
- 显示变小：检查是否有遗留的 `--force-device-scale-factor=1`。
- 修改无效：确认改的是实际用户目录、包装脚本读取的 flags 文件，以及运行进程是否已重开。
- 配置有错误：查看部署日志与生成文件；必要时先切回原版，不清空整个用户目录。

不要重演以下做法：把普通拼音参数当 Rime 参数；只清主 Shift 键却漏掉备用键；为候选框错位盲目堆启动参数；通过固定候选框屏幕坐标掩盖问题；看到模型加载就声称准确率提高；把新模型配方直接覆盖原方案且没有可比较的基线。

## 12. 可直接交给新 Agent 的任务说明

> 请按本文为当前用户配置 Fcitx5 + Rime 雾凇全拼。先识别操作系统、桌面和已有设置，做可恢复备份；只保留一个 Fcitx5 启动入口。覆盖 Hyprland 默认的 shift:both_capslock_cancel 键盘选项释放 Shift 键。让 Fcitx5 保留 Ctrl+Space 并清空主/备用 Shift 绑定，让 Rime 左右 Shift 使用 commit_code。保留英文混输能力，不全局提高英文优先级。为同一用户迁移时复用本文模糊音偏好。针对 foot 全屏配置 opacity 规避直接扫描导致的候选框消失。保留原生 Wayland，先用默认文字比例和适合屏幕的显示缩放验证定位，不使用 XWayland 或强制设备缩放作为默认修复。安装可选万象 LTS 长句方案，采用调优后的二字搭配参数，与原版共用学习库，原版排第一。执行后验证实际全局配置、生成方案、模型加载及真实输入行为。发现已有个人配置时合并而不是覆盖。交付修改摘要、备份路径、验证结果和回滚方式；不要声称尚未验证的部分已经完成。

## 13. 参考来源与版权致谢

本文在整理与实践过程中，学习并吸收了以下社区开源成果与经验，特此注明引用与致谢：

- **[ManateeLazyCat: Omarchy 安装手册 (2026-09-20)](https://manateelazycat.github.io/2026/09/20/omarchy-installation-manual/)**：
  - 学习并采纳了全屏 Foot 终端下因触发 Hyprland 单窗口直接扫描优化（direct scanout）导致 Fcitx 候选窗消失的机理分析，以及通过 `opacity = "1 1 0.999 override"` 进行平滑规避的解决方案。
  - 参考了 Omarchy 桌面环境下的插件生态与窗口规则设计理念。
- **[ManateeLazyCat: rime-ice-installer (GPL-3.0)](https://github.com/manateelazycat/rime-ice-installer)**：
  - 学习并验证了 Omarchy 默认 Hyprland 键盘选项 `shift:both_capslock_cancel` 会截获实体 Shift 释放事件的深层根因，确立了在用户 `input.lua` 中使用 `kb_options = "compose:caps"` 覆盖修复的方法。
  - 采纳了针对万象 LTS 语法模型的二字搭配优化参数（`collocation_min_length: 2` 与 `weak_collocation_penalty: -35`）及同形词限制。
  - 采纳了基于 `ldd /usr/lib/fcitx5/librime.so` 校验动态链接完整性以防御 `opencc` soname 不匹配的检查方案，以及通过 Fcitx5 D-Bus 控制器接口进行非侵入式运行时断言的实践。

