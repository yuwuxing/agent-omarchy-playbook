# Omarchy 语音输入：按住右 Alt 说话，短句直出，长句交给 AI 整理

> 核对日期：2026-09-10。状态：本地方案已部署并试用；网络 API 方案为可迁移示例，尚未验证真实云端服务。本文面向为新电脑复现配置的 Agent。

## 问题与目标

写代码时的口述常常中英文混合：中文句子里夹着 Docker、API、TypeScript。单纯转写容易丢标点、拆错英文；加上大模型又可能把一句简短指令改坏，甚至开始回答问题。

这次配置最终采用分层处理：

```text
按住右 Alt → 录音 → 松开结束 → Paraformer 本地识别
→ voxtype 文本规则 → 短句分流
  ├─ 非空白字符 ≤ 10：保留后处理脚本收到的原文
  └─ 非空白字符 > 10：AI 整理 → 校验 → 失败保留原文
→ 通过剪贴板粘贴到当前输入框
```

目标是让口述变成可以直接发送的文字：去无意义重复、处理明确改口、修复技术词和标点，同时保留原意。它还不是完整的 Typeless：**当前 Paraformer + 后处理配置在松键后输出，不会边说边打字再整体替换。**

## 环境与适用范围

| 项目 | 本机快照 |
|---|---|
| Omarchy / Hyprland | 4.0.2-1 / 0.56.2，Lua 配置 |
| voxtype | `voxtype-bin 1.0.1-1`，程序版本 1.0.1 |
| 音频与输入 | PipeWire、Fcitx5 + Rime、Wayland |
| CPU / GPU | i9-13900K / RTX 4080 16GB |
| 当前 ASR | Paraformer `zh`，ONNX AVX2，CPU 常驻 |
| 保留的 ASR 备选 | Whisper `large-v3-turbo`；配置中另有 SenseVoice 备选段，均非当前引擎 |
| 当前 AI | Gemma 4 E4B IT，GGUF Q4_0，llama.cpp b10636，Vulkan 全层卸载 |
| 当前大模型容量 | 4 个槽位，每路 131072 token，总上下文 524288 |
| 默认思考 | 关闭 |
| 短句规则 | 非空白字符 ≤ 10，跳过 LLM |
| 超时 | HTTP 6 秒，voxtype 后处理 8000 毫秒 |

硬件、模型选择和容量是本机快照，不是新电脑的最低要求。只使用网络 API 整理时，不需要本地运行 Gemma 的显存，但本地 ASR 仍需相应 CPU/内存。

128K 是后来为共用 API 的需求调整的容量。**仅做语音输入不必分配这么大的上下文**；本机还验证过 4 路各 16K 的折中方案。

## Agent 执行前检查

先阅读仓库 [AGENTS.md](../../AGENTS.md) 和环境提供的 omarchy 技能，不修改 `/usr/share/omarchy/`。以下示例在目标用户的 Bash 会话执行；分 shell 执行时重新定义变量。

```bash
VOICE_CONFIG_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}"
VOICE_DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}"
VOICE_CONFIG="$VOICE_CONFIG_ROOT/voxtype"
VOICE_DATA="$VOICE_DATA_ROOT/voxtype"
VOICE_UNITS="$VOICE_CONFIG_ROOT/systemd/user"
voxtype --version
hyprctl version
voxtype setup check
voxtype info devices
systemctl --user cat voxtype.service
systemctl --user list-units --all 'voxtype*'
```

定向检查 `voxtype/config.toml`、`hypr/bindings.lua`、相关用户服务及 drop-in：

- 当前引擎是否和服务实际执行的二进制一致？Paraformer 需要 ONNX 版本。
- 右 Alt 是否已经承担其他快捷键或 AltGr 功能？不要覆盖用户需要的组合。
- 有没有同时启用 evdev 热键、桌面绑定及多个 daemon？录音控制应只有一个入口。
- 8083 是否已被别的服务使用？若更换端口，同步修改客户端与预热脚本参数。
- 音频设备名以 `voxtype info devices` 为准，不能直接把 PulseAudio source 名塞给 ALSA 配置。
- 不打印或提交 API Key、蓝牙 MAC、个人词库及实际口述日志。

安装依赖时先检查已安装版本；本机包为 `voxtype-bin`。Omarchy 可通过 `omarchy pkg add voxtype-bin` 安装，再核对包管理器给出的实际版本与来源。其他依赖包括 Python 3、PipeWire 音频兼容层和 voxtype 检测到的 Wayland 粘贴工具。安装与二进制选择参考 [voxtype 上游安装说明](https://github.com/peteonrails/voxtype/blob/v1.0.1/docs/INSTALL.md)，不要假设新版本仍使用相同打包路径。

## 备份

先记录哪些路径原本不存在，以及服务原先的启用状态；回滚时需要区分“恢复旧文件”和“移除本次新文件”。

```bash
VOICE_BACKUP="$VOICE_DATA/config-backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$VOICE_BACKUP"
for item in voxtype hypr/bindings.lua systemd/user/voxtype.service \
  systemd/user/voxtype.service.d systemd/user/voxtype-llm-gemma-e4b.service; do
  if [ -e "$VOICE_CONFIG_ROOT/$item" ]; then
    mkdir -p "$VOICE_BACKUP/$(dirname "$item")"
    cp -a "$VOICE_CONFIG_ROOT/$item" "$VOICE_BACKUP/$item"
  else
    printf '%s\n' "$item" >> "$VOICE_BACKUP/originally-absent.txt"
  fi
done
systemctl --user is-enabled voxtype.service voxtype-llm-gemma-e4b.service \
  > "$VOICE_BACKUP/service-enabled.txt" 2>&1 || true
```

备份可能包含私密设置，只留在目标电脑，不提交仓库。后续所有配置都定向合并，不能用整段示例覆盖已有文件。

## 配置步骤

### 1. 先让 ASR 工作，再添加 AI

本机选择 Paraformer 中文＋英文模型。`zh` 是配置名，`paraformer-zh` 是本机模型目录名，不能混为所有子命令通用的参数。

本机曾尝试 `voxtype setup --download --model paraformer-zh`，1.0.1 返回 Unknown model。新机器优先运行 `voxtype setup model` 选择 Paraformer；先检查当前安装包是否提供 ONNX 功能，必要时按 `voxtype setup onnx --help` 与上游说明启用，不机械复制其他 CPU 的二进制。

该模型来自 sherpa-onnx 维护者的 [Paraformer ONNX 仓库](https://huggingface.co/csukuangfj/sherpa-onnx-paraformer-zh-2023-09-14)。本机文件及 SHA-256 如下；上游 `main` 可变，以下哈希才对应本文实测文件。下载后逐项校验，若不一致先核对上游版本，不能直接把新哈希当成已验证版本。

| 文件 | SHA-256 |
|---|---|
| `model.int8.onnx` | `f36a0433bcf096bd6d6f11b80a3ac8bed110bdca632fe0d731df8d1a84475945` |
| `tokens.txt` | `59aba8873a2ed1e122c25fee421e25f283b63290efbde85c1f01a853d83cb6e6` |

```bash
sha256sum "$VOICE_DATA/models/paraformer-zh/model.int8.onnx" \
  "$VOICE_DATA/models/paraformer-zh/tokens.txt"
```

合并到 `$VOICE_CONFIG/config.toml`：

```toml
engine = "paraformer"
state_file = "auto"

[hotkey]
enabled = false
key = "RIGHTALT"
mode = "push_to_talk"

[audio]
device = "pipewire"
sample_rate = 16000
max_duration_secs = 60
pause_media = true

[audio.feedback]
enabled = true
theme = "subtle"
volume = 0.7

[paraformer]
model = "zh"
on_demand_loading = false

[output]
mode = "paste"
paste_keys = "shift+insert"
fallback_to_clipboard = true

[vad]
enabled = true
backend = "whisper"
threshold = 0.5
```

`[vad] backend = "whisper"` 是本机 Silero VAD 运行后端的设置，不表示 ASR 引擎切回 Whisper；需要 VAD 模型时查看 `voxtype setup vad --help`。先验证无声录音不会产生乱字。

本机服务通过 `$VOICE_UNITS/voxtype.service.d/onnx.conf` 指定 ONNX AVX2 可执行文件：

```ini
[Service]
ExecStart=
ExecStart=/usr/lib/voxtype/voxtype-onnx-avx2 daemon
```

只有目标包确实提供该路径、CPU 支持 AVX2 时才使用；先 `test -x` 并检查该二进制的 `--version`。如果原服务含必要的 `--config` 等参数，替换 ExecStart 时保留。创建服务可用 `voxtype setup systemd`，已有服务则不要重复安装。

Whisper 是保留的对照方案：`large-v3-turbo`、`language = "zh"`、`translate = false`，并可设置简短技术词 `initial_prompt`。这个提示词只作用于 Whisper，不会自动成为 Paraformer 热词。切换引擎后还要检查服务二进制并重启，不能只修改一个未使用的配置段。

### 2. 右 Alt 必须绑定“按下”和“松开”

在本机的 `hypr/bindings.lua` 已有 `hl`、`o` 配置上下文中，定向替换右 Alt 的旧绑定：

```lua
hl.unbind("Alt_R")
o.bind("Alt_R", "Start voice dictation (hold Right Alt)", "voxtype record start", { ignore_mods = true })
o.bind("Alt_R", "Stop voice dictation (release Right Alt)", "voxtype record stop", { release = true, ignore_mods = true })
```

这里的 `hl`、`o` 是 Omarchy 当前 Lua 配置提供的对象，不是独立 Lua 程序。旧版 Hyprland 文本配置语法不同，不要混写。

```bash
hyprctl reload
hyprctl configerrors
```

本机最初绑定 toggle，实际变成“点一下开始，再点一下结束”。改成上面的 start/stop 后，用户确认按住录音、松开结束正常。由 Hyprland 控制时，单独写 `[hotkey] mode = "push_to_talk"` 并不能纠正一个 toggle 桌面绑定。

### 3. 粘贴与麦克风比大模型更基础

Fcitx5/Rime 的中文状态可能干扰模拟逐字输入。本机采用 `paste` 配合 `Shift+Insert`；需要在实际使用的浏览器、编辑器和终端分别验证，不能认为所有应用都接受同一个粘贴键。

蓝牙耳机还涉及 A2DP 播放与 HFP/HSP 麦克风配置。本机录音前有自定义 `pre_recording_command`，负责连接耳机并选中麦克风。它包含设备专属信息，本文不复制该脚本；新机器通过 PipeWire 检查默认输入源后再定制。耳机重连时间不能算作 LLM 延迟，也不要在录音开始时无条件切换其他用户的音频设备。

### 4. 短句直出与技术词规则

本机规则是 **10 个非空白字符以内跳过 LLM**。汉字、英文字母、数字和标点都分别计数；这是字符阈值，不是英文单词数或模型 token 数。

例如“好的”“重启服务”“Docker”直接输出。10 个字符加若干空格仍跳过，11 个非空白字符才调用 AI。这个阈值是用户体验驱动的初始选择，不是普适最佳值；短技术词也因此失去 LLM 修复机会。

简单拼写可留给 `[text.replacements]`：

```toml
[text.replacements]
"git hub" = "GitHub"
"github" = "GitHub"
"type script" = "TypeScript"
"typescript" = "TypeScript"
"docker" = "Docker"
"postgresql" = "PostgreSQL"
"api" = "API"
```

只合并自己确实需要的词条，不把含糊的中文谐音全部强制替换。短句保留的是脚本收到的转写文本，并非承诺绕过 voxtype 的所有内置文本处理。

### 5. 两种 API 共用一套后处理逻辑

配套文件位于 [voxtype-assets](voxtype-assets/cleanup.py)：

- [cleanup.py](voxtype-assets/cleanup.py)：字符分流、API 调用、数字/路径/参数校验和异常回退。
- [llm-correction-prompt.txt](voxtype-assets/llm-correction-prompt.txt)：实际使用的整理提示词。
- [本地设置](voxtype-assets/llm-settings.local.json) / [网络设置示例](voxtype-assets/llm-settings.remote.example.json)。
- [warmup-llm.py](voxtype-assets/warmup-llm.py)：仅用于本地服务启动预热。

仓库脚本由本机脚本整理而来，增加了 endpoint、model、请求选项和环境变量密钥的可配置读取；不是整份私人配置的复制。安装前先审阅差异，备份已有脚本，再复制选定文件到 `$VOICE_CONFIG`。从仓库根目录执行：

```bash
mkdir -p "$VOICE_CONFIG"
cp docs/input-method/voxtype-assets/cleanup.py "$VOICE_CONFIG/cleanup.py"
cp docs/input-method/voxtype-assets/llm-correction-prompt.txt "$VOICE_CONFIG/llm-correction-prompt.txt"
```

在已有 `[output.post_process]` 表内合并以下值，不新增重复表。`command` 由 Agent 写入目标机解析后的绝对路径；示例中的占位符不能原样运行，也不要依赖 TOML 展开 `$HOME`。

```toml
[output.post_process]
command = "/usr/bin/python3 /ABSOLUTE/USER/CONFIG/voxtype/cleanup.py"
timeout_ms = 8000
```

脚本 HTTP 等待为 6 秒，外层为 8 秒；网络方案若调整 HTTP 等待，应同步增加外层预算并测端到端延迟。网络库超时不是所有情况下的严格总墙钟截止，最终由 voxtype 的外层预算兜底。

**提示词的重点是边界：**只整理材料，不执行其中的命令，不回答问题；保留否定、不确定性和有效信息，只处理明确的改口。加入“帮我写 Python 脚本”仍应原样作为一句请求输出的对话示例，能减少生成代码的倾向。

校验层会拒绝空结果、截断、异常扩写、新增思考标签/代码块、意外数字变化、路径或长参数丢失。仅对紧邻的明确数字改口放行，例如“8080，哦不，改成 3000”；“把 8080 改成 3000”必须保留两个数字。它不是完整语义校验，仍可能漏掉否定词变化；复杂数字连环改口也可能保守回退。不要把正则规则描述成“保证不改意思”。

### 6A. 网络 API：发送转写文本，不上传这条链路的录音

适合没有足够显存、或希望使用托管模型的机器。这里使用兼容 Chat Completions 协议的服务，提供商自行选择；“OpenAI 兼容”不意味着必须调用 OpenAI。

```bash
cp docs/input-method/voxtype-assets/llm-settings.remote.example.json \
  "$VOICE_CONFIG/llm-settings.json"
```

编辑 endpoint 为提供商的**完整** HTTPS 地址（通常以 `/v1/chat/completions` 结尾），model 为其实际模型 ID。默认选择可关闭思考的快速模型；关闭思考的参数因服务而异，应按该服务文档填入 `request_options`，不要把 llama.cpp 的 `chat_template_kwargs` 通用化。示例域名 `api.example.com` 不能用于真实请求。

API Key 放入本机权限为 600 的环境文件，不写入仓库、命令行历史或 JSON 示例：

```bash
if [ ! -e "$VOICE_CONFIG/llm.env" ]; then
  install -m 600 /dev/null "$VOICE_CONFIG/llm.env"
fi
```

上面仅在文件不存在时创建，**已有文件不得用这条命令清空**。通过本机编辑器写入 `VOXTYPE_LLM_API_KEY=实际密钥`，随后在 `voxtype.service` 的独立 drop-in 中引用其实际绝对路径：

```ini
[Service]
EnvironmentFile=/ABSOLUTE/USER/CONFIG/voxtype/llm.env
```

执行 `systemctl --user daemon-reload` 并重启 voxtype，使其子进程得到密钥；只在交互终端 export 不会自动传入已经运行的服务。此方案会把转写文本发送给选择的提供商；首次配置需处于用户已授权的范围，使用公开的合成文本验收。

配套脚本已在临时目录中验证短句边界、异常回退、数字保护和本地合成文本请求；网络 API 路径已做离线模拟请求验证，但未使用真实凭证调用云端，延迟、限流及服务商特有字段仍需目标环境实测。异常仍回退原文，不能把“界面出现了原文”当成云端已成功。

### 6B. 本地 API：Gemma 常驻，录音结束直接调用

```bash
cp docs/input-method/voxtype-assets/llm-settings.local.json "$VOICE_CONFIG/llm-settings.json"
cp docs/input-method/voxtype-assets/warmup-llm.py "$VOICE_CONFIG/warmup-llm.py"
```

本机 llama.cpp 来自 [官方 b10636 Release](https://github.com/ggml-org/llama.cpp/releases/tag/b10636) 的 `llama-b10636-bin-ubuntu-vulkan-x64.tar.gz`；SHA-256 为 `5396ff64ebee51f4adff0633db600fd71bc8aa4aa95cff79aea0951db960ee9c`。下载后用 `sha256sum` 核对，保留压缩包内依赖库布局，验证 `llama-server --version` 和 `--list-devices`。Ubuntu 命名资产在本机可用，不保证任意 Arch 版本的动态库兼容性；不匹配时按上游构建说明使用 Vulkan 编译。

模型来源是 ggml-org 的 [Gemma E4B GGUF 仓库](https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF/tree/b8093469224f83f5c38f691eb906c380e9e63114)，这是量化分发来源，不是 Google 原始权重仓库。实测固定 revision 为 `b8093469224f83f5c38f691eb906c380e9e63114`，文件 `gemma-4-E4B-it-Q4_0.gguf`，SHA-256 为 `a555b900214b477d8880e7832e0b8925e139b0159640036b09fe472b6f2097f2`。

```bash
mkdir -p "$VOICE_DATA/llm-models"
curl --fail --location --retry 3 \
  'https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF/resolve/b8093469224f83f5c38f691eb906c380e9e63114/gemma-4-E4B-it-Q4_0.gguf' \
  -o "$VOICE_DATA/llm-models/gemma-4-E4B-it-Q4_0.gguf.part"
printf '%s  %s\n' \
  a555b900214b477d8880e7832e0b8925e139b0159640036b09fe472b6f2097f2 \
  "$VOICE_DATA/llm-models/gemma-4-E4B-it-Q4_0.gguf.part" | sha256sum -c -
```

校验成功后才把 `.part` 更名为最终模型文件；已有有效模型直接跳过下载，不覆盖。约 4.59GB 的模型文件不提交 Git。若下载受许可证或访问策略约束，按上游要求处理，不绕过授权。

以下为 `$VOICE_UNITS/voxtype-llm-gemma-e4b.service` 的可迁移示例，使用 systemd 的 `%h`。目标机采用自定义 XDG 路径或不同二进制位置时，先替换为实际绝对路径；不要假设 systemd 会展开 shell 变量。

```ini
[Unit]
Description=Voxtype local correction LLM (Gemma E4B)
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=%h/.local/bin/llama-server --model %h/.local/share/voxtype/llm-models/gemma-4-E4B-it-Q4_0.gguf --alias gemma-cleanup --host 127.0.0.1 --port 8083 --device Vulkan0 --gpu-layers all --ctx-size 65536 --parallel 4 --threads 4 --flash-attn on --reasoning off --reasoning-format deepseek --cache-ram 0 --cors-origins localhost --metrics
ExecStartPost=/usr/bin/python3 %h/.config/voxtype/warmup-llm.py 8083 gemma-cleanup
TimeoutStartSec=100
Restart=on-failure
RestartSec=10

[Install]
WantedBy=graphical-session.target
```

这是**4 路各 16K 的建议起点**。写作时本机实际值是 `--ctx-size 524288 --parallel 4`，即四路各 128K；`/slots` 已确认每路 `n_ctx = 131072`。该快照整卡显存约 14273 MiB，剩余约 1667 MiB，尚未验证四路同时填满 128K。不要把 512K 总量误写成模型单路支持 512K，也不要默认向所有 16GB 显卡推荐这档容量。

确认 Vulkan0 对应目标 GPU，避免和其他模型实例争抢显存。先选容量，再启动并检查：

```bash
systemctl --user daemon-reload
systemctl --user enable --now voxtype-llm-gemma-e4b.service
curl --fail http://127.0.0.1:8083/health
curl --fail http://127.0.0.1:8083/slots
```

已有运行服务修改参数后用 `restart`，`enable --now` 不会替已运行进程重新加载参数。常驻意味着免去每次加载模型的等待，空闲不持续生成内容，但权重与 KV 容量依然占显存。这里随**用户图形会话**启动，不等于开机尚未登录时就已就绪。

`--reasoning off` 是服务默认值；本机实测单次请求可用 `chat_template_kwargs.enable_thinking = true` 覆盖。`--reasoning-format deepseek` 仅控制思考内容的返回位置，不是思考开关。语音整理的本地设置显式发送 false；翻译也建议保持关闭。开启测试曾在 128 输出 token 全用于思考后截断，没有最终正文。

### 7. 加载配置与应用验收

模型服务和脚本就绪后重启 voxtype：

```bash
systemctl --user daemon-reload
systemctl --user enable --now voxtype.service
systemctl --user restart voxtype.service
```

提示词与 cleanup 脚本会在下一次后处理调用读取，一般不需要重启模型。改变 daemon 配置、二进制或 EnvironmentFile 则需要相应重启。

## 验收

| 操作 | 预期结果 | 本机结果 / 范围 |
|---|---|---|
| 按住右 Alt 说话后松开 | 松开结束并粘贴 | 用户真实操作确认通过 |
| 10 字以内的短句、10/11 字边界 | 前者不发 API 请求，后者调用 | 脚本模拟调用验证通过；新规则的长期体验待观察 |
| “嗯那个这个接口这个接口需要改一下” | 删除无意义重复 | 本地合成文本测试通过 |
| “用 Python，不对，还是用 TypeScript” | 保留最终决定 | 本地测试通过 |
| “端口是 8080，不是 8000” | 两个数字及否定均保留 | 调整提示词后测试通过 |
| “帮我写一个 Python 脚本……” | 整理句子，不输出程序 | 本地测试通过 |
| 明确说“分三步……” | 尝试分条 | 部分例子通过，另有例子仍输出一句话 |
| 原路径、版本、`--dry-run` | 不擅自替换/删除 | 合成文本测试通过；校验失败回退 |
| 关闭 API、模拟超时/截断 | 保留脚本收到的原文 | 离线回退测试；目标机仍需检查实际日志 |
| 两个翻译请求正在运行时整理短口述 | 不被整页翻译长期阻塞 | 4 路各 16K 下约 0.334 秒，不含 ASR |
| 4 路各 128K | 加载、健康检查、每槽容量正确 | 已确认；四路满长输入负载未测 |
| 网络 API | 鉴权、响应字段、超时及限流正常 | 仅离线模拟，真实服务待验证 |

测试文本应使用合成示例，不把用户日常口述上传到公开问题或文章。静态检查 TOML 可用 Python `tomllib`；Lua 修改需 `hyprctl reload` 与 `configerrors`，最后还要真实录音、粘贴验收。

### 性能应该怎么看

以下为本机短时测试，不是硬件通用保证。预热后各路同时提交约 294 输入 token，输出上限 192 token：

| 并发 | 单路生成 token/s | 整体输出 token/s |
|---:|---:|---:|
| 1 | 136 | 130 |
| 2 | 122 | 228 |
| 4 | 104 | 363 |
| 8 | 69 | 471 |

单路列是服务器生成阶段均速；整体列按总输出 token 除以墙钟耗时，包含输入处理。32 并发各 8K、64 并发各 4K 也跑通过，但高并发测试不是长期稳定性测试。64 路时单路约 17 token/s，不能据此宣传“64 并发也适合语音输入”。

单路 128K 容量测试实际输入 130591 token，首次出字约 38.6 秒；32K 档实际输入 32288 token，约 5.2 秒。输入使用重复技术句，只验证容量与延迟，没有验证长文理解或翻译质量。

```bash
journalctl --user -u voxtype-llm-gemma-e4b.service -f -o cat |
  rg --line-buffered 'prompt eval time|eval time'
curl --fail http://127.0.0.1:8083/metrics
nvidia-smi
```

日志中 `eval time` 是生成速度，`prompt eval time` 是输入处理速度。metrics 中 `requests_processing` 是处理数，`requests_deferred` 是排队数。`/slots` 显示槽位占用；每秒采样可能漏掉非常短的请求。整卡显存包含桌面等其他进程，不能直接当作 Gemma 独占显存。

如果和沉浸式翻译共用，客户端使用 `gemma-cleanup` 与完整接口 `http://127.0.0.1:8083/v1/chat/completions`，翻译提示词由该客户端独立提供，不会自动套用语音整理提示词。翻译并发先设 2，给其他请求留容量；这不是严格优先级或固定槽位预留。[沉浸式翻译官方接入说明](https://immersivetranslate.com/zh-Hans/docs/services/ai/) 支持此类自定义模型接口。扩展跨域访问仍需单独验证，不要为了消除报错直接把服务暴露到公网。

## 回滚

1. 先停正在录制的任务，保存当前未提交文字。若只是 AI 整理不合适，移除本次添加的 `[output.post_process]` 表（或恢复原值），重启 voxtype，即可保留 ASR 和右 Alt 操作。
2. 恢复 `$VOICE_BACKUP` 中对应的脚本、配置和服务；只移除记录为原本不存在且本次新建的文件。不要删除整个用户配置目录，也不要删除模型、其他服务或 Rime 数据。
3. 若撤回网络 API，移除本次新增的 EnvironmentFile drop-in，按需保留本机凭证文件；若撤回本地模型服务，仅在它确为本次新建且无其他客户端使用时停用。
4. 恢复服务启用状态，执行 `systemctl --user daemon-reload`，重启受影响服务。还原 Hyprland 绑定后执行 `hyprctl reload`、`hyprctl configerrors` 并测试原快捷键。
5. 记录恢复的文件、残留模型和实际输入结果；Git 仓库中撤回文章不等于系统配置已回滚。

## 已知限制与踩坑

- 短句加 LLM 并非总能改善；现阶段优先直出，10 字阈值允许后续调整。
- 本机用户更偏好 Gemma，Qwen 对照没有带来更满意的主观效果；不能推广成所有中文任务的模型排名。
- 去重复、改口与列表整理仍不完全稳定，提示词和校验不能消除所有错误。
- 把所有数字变动都拒绝会阻止正确改口；放行范围过宽又容易删除重要值，需要保守取舍。
- Paraformer 是当前中文＋英文方案；不要因为 Parakeet 同样以 P 开头就混用它们的模型和设置。
- GPU 常驻和预热减少冷启动，不能消除排队、长输入处理或蓝牙连接时间。
- 本地性能数字与网络 API 不能直接比较；网络延迟、服务商排队和限流都需要实际测量。
- 当前链路没有部署边说边出字；voxtype 某些其他引擎的流式能力不能直接推导到本方案。

## 参考来源

- [voxtype v1.0.1 配置说明](https://github.com/peteonrails/voxtype/blob/v1.0.1/docs/CONFIGURATION.md)：热键、输出、引擎和后处理字段；同时核对本机 `--help`、服务与配置。
- [voxtype v1.0.1 模型选择实现](https://github.com/peteonrails/voxtype/blob/v1.0.1/src/setup/model.rs)：模型名称和下载入口；打包版本可能影响 CLI 可用项。
- [Paraformer ONNX 分发仓库](https://huggingface.co/csukuangfj/sherpa-onnx-paraformer-zh-2023-09-14)：模型来源，本文另记实测文件哈希。
- [llama.cpp b10636 server 文档](https://github.com/ggml-org/llama.cpp/blob/b10636/tools/server/README.md)：服务接口与参数；本文并发、容量和延迟来自本机实测。
- [Gemma E4B GGUF 固定版本](https://huggingface.co/ggml-org/gemma-4-E4B-it-GGUF/tree/b8093469224f83f5c38f691eb906c380e9e63114)：量化文件及版本追溯。

## 交给 Agent 的任务

> 请先阅读仓库 AGENTS.md，再按本文检查我的 Omarchy、Hyprland、voxtype 和音频环境。备份后配置右 Alt 按住录音、松开输出，短句跳过 AI；根据我选择的网络或本地 API 接入长句整理。保留现有个人配置，不上传真实口述或凭证，区分默认建议与 128K 实验容量，完成真实输入验收并报告回滚方法。
