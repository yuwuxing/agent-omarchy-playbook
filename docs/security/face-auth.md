# Omarchy 红外人脸认证：锁屏自动刷脸，唤醒重试，sudo 人脸优先

> 核对日期：2026-09-20。状态：核心场景已验证（人脸比对、锁屏刷脸、显示器亮屏重试、sudo 与 Polkit 提权）；真正睡眠恢复及完整负向验收待实测。本文面向为新电脑复现配置的 Agent。

## 问题与目标

在搭载红外摄像头的笔记本电脑上，用户希望在 Omarchy 环境中实现日常认证的无感刷脸：唤醒或点亮屏幕自动解锁，执行 `sudo` 或图形提权时优先调用人脸识别；当人脸识别超时或未匹配时，能够无缝回退至指纹或密码。

将红外采集、补光控制、识别模型、PAM 认证链与锁屏生命周期串联起来，整体处理链路如下：

```text
认证触发（锁屏 / sudo / Polkit / 亮屏 / 空回车）
→ 合盖状态检查（若合盖则跳过红外，直接走指纹/密码）
→ 硬件红外补光开启（UVC 扩展单元）
→ howdy-next 人脸识别（YuNet 检测 + SFace 特征比对，单线程推理）
  ├─ 匹配成功：直接放行提权 / 解锁
  └─ 匹配失败 / 超时：
      ├─ 锁屏后台通道：回退至指纹等待；独立密码通道随时可输入解锁
      └─ sudo / Polkit：顺序回退至指纹，最终回退至密码输入
```

目标认证行为定义如下：

| 场景 | 需要实现的行为 |
| --- | --- |
| 锁屏激活 | 自动开始人脸扫描；独立密码通道维持就绪，用户可随时输入密码 |
| 人脸未匹配 | 正常结束本轮扫描；若系统录入指纹则继续等待指纹，否则回退密码 |
| 锁屏熄屏后亮屏 | 重新触发一轮人脸扫描 |
| 系统睡眠后唤醒 | 放弃睡眠前的悬挂扫描，等待设备就绪后启动新一轮识别 |
| 用户主动重试 | 提供明确入口，例如在锁屏密码框中直接按 Enter（空输入）触发重试 |
| sudo / 图形提权 | 优先人脸识别，失败后按配置回退至指纹或密码 |
| 合盖或设备不可用 | 检测到合盖状态时跳过红外，避免黑屏扫描超时阻塞密码认证 |
| 认证取消或已解锁 | 立即中止正在运行的扫描子进程，清理待执行的重试定时器 |

红外摄像头能有效改善低照度环境下的图像采集并过滤普通平面照片，但并不能仅凭使用红外就断言具备金融级活体防伪检测或与 Windows Hello 完全等价的安全能力。

## 环境与适用范围

| 项目 | 本机快照 |
|---|---|
| Omarchy / Hyprland | 4.0.4-1 / 0.56.2-2 |
| 锁屏架构 | Omarchy Shell（基于 Quickshell，独立密码与生物双 PAM 通道） |
| howdy-next / OpenCV | 3.4.0-1.1（含 OpenCV 单线程推理补丁） / 5.0.0-9 |
| 参考硬件基线 | ThinkPad X1 Carbon Gen 9，Chicony IR 摄像头，Synaptics 指纹（`06cb:00fc`） |
| 摄像头接口 | GREY，640×360 @ 15 FPS，稳定路径位于 `/dev/v4l/by-path/` |
| 补光控制协议 | Chicony UVC Extension Unit（Unit 13, Selector 14） |

本方案的核心逻辑依赖于 Linux V4L2 视频采集、红外发射器控制、PAM 认证栈以及 Omarchy 用户锁屏插件扩展机制。硬件适配参数（如 USB ID、UVC 控制单元与稳定设备路径）来自上述参考硬件，**在新机器部署时必须重新识别硬件设备，不能直接套用硬编码参数。**

## Agent 执行前检查

执行前请先阅读仓库 [AGENTS.md](../../AGENTS.md) 以及环境提供的 omarchy 技能，不要修改 `/usr/share/omarchy/` 目录。以下检查命令在目标用户的 Bash 会话中运行；若分 shell 执行请重新导出变量。

```bash
FACE_CONFIG_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}"
FACE_DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}"
FACE_WORK="$HOME/work/face-setup"
FACE_BACKUP="$FACE_WORK/backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$FACE_WORK" "$FACE_BACKUP"

# 1. 检查基础环境与版本
cat /etc/os-release
hyprctl version
uname -r

# 2. 检查摄像头硬件节点与红外格式
v4l2-ctl --list-devices
ls -l /dev/v4l/by-path/

# 3. 检查现有 PAM 规则与冲突
head -n 20 /etc/pam.d/sudo /etc/pam.d/polkit-1 /etc/pam.d/omarchy-lock-* 2>/dev/null || true

# 4. 检查 Polkit 提权助手运行限制
systemctl status polkit.service --no-pager

# 5. 检查锁屏插件与已录入指纹
ls -la "$FACE_CONFIG_ROOT/omarchy/plugins/" 2>/dev/null || true
fprintd-list "$USER" 2>/dev/null || true
```

检查时确认以下关键项：

- **IR 设备节点稳定性**：确认哪个 `/dev/video*` 节点真正输出灰度图（GREY 格式），并记录对应的 `/dev/v4l/by-path/` 符号链接路径，防止重启后节点编号漂移。
- **补光控制协议**：检查红外发射器是否需要显式开启。若为常见 UVC 设备，需确认其 vendor extension unit 与 selector；若系统已有 `linux-enable-ir-emitter` 等成熟工具，优先使用经过该工具探测的配置。
- **现有 PAM 状态**：确认 `/etc/pam.d/sudo`、`/etc/pam.d/polkit-1` 是否已有旧版 Howdy、第三方人脸或指纹条目，避免规则叠加引起死锁或重复扫描。
- **锁屏后台通道前置条件**：检查 Omarchy 当前锁屏插件是否限制了只有录入指纹才拉起后台通道。如果目标机没有指纹设备，需相应调整锁屏启动逻辑。

## 备份

修改系统认证与锁屏插件前，必须创建带时间戳的可恢复备份，并记录原先不存在的文件，确保能够完整回滚。

```bash
# 备份 PAM 认证文件（需 root 权限）
sudo mkdir -p "$FACE_BACKUP/pam"
for f in sudo polkit-1 omarchy-lock-fingerprint omarchy-lock-password; do
  if [ -f "/etc/pam.d/$f" ]; then
    sudo cp -a "/etc/pam.d/$f" "$FACE_BACKUP/pam/$f"
  fi
done

# 备份已有 Howdy 配置与服务 drop-in
if [ -d /etc/howdy ]; then
  sudo cp -a /etc/howdy "$FACE_BACKUP/howdy"
fi
if [ -d /etc/systemd/system/polkit-agent-helper-1.service.d ]; then
  sudo cp -a /etc/systemd/system/polkit-agent-helper-1.service.d "$FACE_BACKUP/polkit-dropin"
else
  printf '%s\n' "/etc/systemd/system/polkit-agent-helper-1.service.d" >> "$FACE_BACKUP/originally-absent.txt"
fi

# 备份用户锁屏插件
if [ -d "$FACE_CONFIG_ROOT/omarchy/plugins" ]; then
  cp -a "$FACE_CONFIG_ROOT/omarchy/plugins" "$FACE_BACKUP/omarchy-plugins"
fi
```

人脸模板属于用户高敏感生物数据，备份仅保存在本地安全路径中，**严禁将人脸特征库、图像样本或相关凭证提交至 Git 仓库。**

## 配置步骤

### 1. 定位 IR 摄像头并配置补光控制

#### 定位稳定图像输入路径

通过 `v4l2-ctl` 枚举设备能力，找到支持灰度格式（如 `GREY` 或 `YUYV`）的红外摄像头节点。通过 `/dev/v4l/by-path/` 路径定位该节点，避免因设备插拔或热加载导致 `/dev/videoX` 编号变动。

例如在参考硬件上：
- 节点：Chicony IR 摄像头，USB ID `04f2:b6ea`
- 格式：`GREY`，分辨率 640×360，15 FPS
- 稳定路径：`/dev/v4l/by-path/pci-0000:00:14.0-usb-0:4:1.2-video-index0`

#### 编译轻量级 UVC 补光控制程序

多数笔记本内置红外摄像头需要显式通过 UVC Extension Unit 开启发射器。在 `/usr/local/libexec/howdy-ir-emitter` 部署受限控制程序，确保每次写入前做范围检查，不执行未知固件刷写。

```bash
cat <<'EOF' > "$FACE_WORK/ir-emitter.c"
// Bounded Chicony UVC emitter control; no scanning or firmware writes.
// Protocol reference: github.com/PetePriority/chicony-ir-toggle (on = 2,25).
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/uvcvideo.h>
#include <linux/usb/video.h>

static int query(int fd, unsigned char request, unsigned char data[2]) {
    struct uvc_xu_control_query q = {.unit=13,.selector=14,.query=request,.size=2,.data=data};
    return ioctl(fd, UVCIOC_CTRL_QUERY, &q);
}

int main(int argc, char **argv) {
    // 替换为目标电脑上实际确认的 IR 稳定路径
    const char *device = "/dev/v4l/by-path/pci-0000:00:14.0-usb-0:4:1.2-video-index0";
    unsigned char maximum[2] = {0}, current[2] = {0}, target[2] = {2, 25};
    if (argc > 1 && strcmp(argv[1], "off") == 0) target[0] = target[1] = 0;

    int fd = open(device, O_RDWR | O_CLOEXEC);
    if (fd < 0) { perror("open IR camera"); return 1; }

    if (query(fd, UVC_GET_MAX, maximum) < 0 || maximum[0] != 2 || maximum[1] != 100) {
        fprintf(stderr, "Unexpected emitter control; refusing to write\n");
        close(fd);
        return 2;
    }
    if (query(fd, UVC_GET_CUR, current) < 0) { perror("read emitter"); close(fd); return 3; }
    if (memcmp(current, target, 2) != 0 && query(fd, UVC_SET_CUR, target) < 0) {
        perror("enable emitter"); close(fd); return 4;
    }
    if (query(fd, UVC_GET_CUR, current) < 0 || memcmp(current, target, 2) != 0) {
        fprintf(stderr, "Emitter readback failed\n"); close(fd); return 5;
    }
    printf("IR emitter: mode=%u, level=%u\n", current[0], current[1]);
    close(fd);
    return 0;
}
EOF

gcc -O2 "$FACE_WORK/ir-emitter.c" -o "$FACE_WORK/howdy-ir-emitter"
sudo install -m 755 -o root -g root "$FACE_WORK/howdy-ir-emitter" /usr/local/libexec/howdy-ir-emitter
```

运行 `/usr/local/libexec/howdy-ir-emitter` 验证发射器能正常点亮，并通过参数 `off` 测试关闭。

### 2. 编译安装 howdy-next 并修复单线程推理

[howdy-next](https://codeberg.org/nathawat/howdy-next) 使用 C++ 实现，通过 OpenCV DNN 运行 YuNet 人脸检测和 SFace 身份比对。

#### 修复 OpenCV 推理 CPU 预算冲突（SIGXCPU）

Howdy 为了防止扫描进程死锁，对进程的 CPU 时间施加了资源限制。OpenCV DNN 默认的多线程工作池会在所有核心上并行计算，导致几秒钟的墙钟扫描在累计 CPU 时间上瞬间超限，直接被系统发送 `SIGXCPU` 终止。

在打包构建源码时，应用单线程推理补丁：

```patch
--- a/howdy/src/vision/face_model.cpp
+++ b/howdy/src/vision/face_model.cpp
@@ -35,6 +35,11 @@
 	    : metric_(config.sface_metric)
 	    , threshold_(config.sface_threshold)
 	    , backend_(std::make_shared<Backend>()) {
+		// Authentication has a process-wide CPU-time budget. OpenCV's default
+		// worker pool spends that budget across all cores and can trigger SIGXCPU
+		// before the scan's wall-clock deadline. Small face models also avoid
+		// worker-pool overhead with serial inference. Keep sandbox limits intact.
+		cv::setNumThreads(1);
 		backend_->check_readiness = [](const std::filesystem::path &path) -> OpenCvModelReadiness {
 			return check_opencv_model_readiness_with_label(path, "OpenCV face model file",
 			                                               static_cast<uid_t>(0));
```

通过 PKGBUILD 编译安装带补丁的软件包，确保升级链路可追踪。

#### 配置图像采集与比对模型

模型文件置于 `/usr/share/howdy/models/`：
- YuNet 人脸检测：`face_detection_yunet_2026may.onnx`
- SFace 身份比对：`face_recognition_sface_2021dec_int8.onnx`

在 `/etc/howdy/config.ini` 中配置采集参数与识别阈值：

```ini
[video]
device_path = /dev/v4l/by-path/pci-0000:00:14.0-usb-0:4:1.2-video-index0
max_height = 320
frame_width = 640
frame_height = 360
fps = 15
timeout = 5

[video.clahe]
enabled = true
clip_limit = 1.25
tile_grid_size = 8

[snapshots]
capture_failed = false
capture_successful = false

[models]
detection = yunet
detection_threshold = 0.6
recognition = sface
recognition_metric = cosine
recognition_threshold = 0.6942
```

- `detection_threshold = 0.6`：用于判定画面中是否存在有效人脸。
- `recognition_threshold = 0.6942`（余弦距离）：用于判定该人脸是否匹配已录入模板。

### 3. 现场录入人脸模板与独立比对验证

在将人脸模块接入 PAM 之前，先完成模板录入并进行独立验证。

```bash
# 录入当前用户的红外人脸模板
sudo howdy -U "$USER" add

# 执行独立测试（不接入 PAM）
sudo howdy test
```

观察 `howdy test` 输出，确保在红外补光开启的状态下，匹配耗时稳定在 350ms–450ms 之间，余弦距离显著低于阈值。此时保持 PAM 文件不变，证明硬件、推理与权限链路全部正常后，再进入系统认证改造。

### 4. 配置 Polkit 授权沙箱权限

现代 systemd 环境中，Polkit agent helper 可能在受限制的沙箱单元中运行，默认无法访问视频设备节点。

创建 drop-in 文件 `/etc/systemd/system/polkit-agent-helper-1.service.d/howdy.conf`：

```ini
[Service]
PrivateDevices=no
DeviceAllow=char-video4linux rw
DeviceAllow=/dev/uinput rw
```

重新加载 systemd 管理器：

```bash
sudo systemctl daemon-reload
```

### 5. 接入 Omarchy PAM 认证链

Omarchy 的日常认证由三处服务组成：`sudo`、`polkit-1` 以及锁屏。锁屏采用双通道架构：密码通道（`omarchy-lock-password`）与后台生物通道（`omarchy-lock-fingerprint`）。

我们保持 `omarchy-lock-password` 完全不变，确保任何时候用户都能输入密码解锁。在其余三个服务中接入人脸认证。

#### `/etc/pam.d/sudo`

合盖检查跳过红外 → 开启补光 → 人脸识别 → 指纹备选 → 密码回退。

```pam
# Face first, then the existing fingerprint/password fallback.
auth      [success=3 default=ignore] pam_exec.so quiet /usr/bin/omarchy-hw-laptop-closed
auth      optional pam_exec.so quiet /usr/local/libexec/howdy-ir-emitter
auth      sufficient pam_howdy.so
auth      sufficient pam_fprintd.so
#%PAM-1.0
auth		include		system-auth
account		include		system-auth
session		include		system-auth
session		optional	pam_systemd.so class=none
```

> [!NOTE]
> `[success=3 default=ignore]`：当笔记本处于合盖（挂接外接显示器或远程会话）状态时，`omarchy-hw-laptop-closed` 返回 0，精确跳过后续的补光、人脸与指纹 3 个模块，直接进入密码链，避免合盖时触发超时等待。

#### `/etc/pam.d/polkit-1`

图形提权同样配置人脸优先：

```pam
# Face first, then the existing fingerprint/password fallback.
auth      [success=3 default=ignore] pam_exec.so quiet /usr/bin/omarchy-hw-laptop-closed
auth      optional pam_exec.so quiet /usr/local/libexec/howdy-ir-emitter
auth      sufficient pam_howdy.so
auth      sufficient pam_fprintd.so
auth      required pam_unix.so

account   required pam_unix.so
password  required pam_unix.so
session   required pam_unix.so
```

#### `/etc/pam.d/omarchy-lock-fingerprint`

锁屏后台生物通道扩展为人脸优先、指纹备选：

```pam
#%PAM-1.0
# IR face first, fingerprint fallback; password runs independently.
auth       [success=3 default=ignore] pam_exec.so quiet /usr/bin/omarchy-hw-laptop-closed
auth       optional                  pam_exec.so quiet /usr/local/libexec/howdy-ir-emitter
auth       sufficient                pam_howdy.so
auth       required                  pam_fprintd.so
account    include                   system-local-login
```

#### 检查独立密码通道

确认 `/etc/pam.d/omarchy-lock-password` 保持原样，不添加任何生物认证模块。用户在锁屏界面打字提交密码时，由该通道独立处理，不受后台人脸扫描状态阻塞。

### 6. 通过用户锁屏插件实现唤醒与重试

Omarchy Shell 的锁屏由 Quickshell 驱动。原生插件在扫描超时后不会自动重新激活，且睡眠恢复或亮屏时缺少重试逻辑。

利用 Omarchy 的插件 clone 机制，在用户目录创建定制插件 `${XDG_CONFIG_HOME:-$HOME/.config}/omarchy/plugins/${USER}.lock`。

#### 插件清单 `manifest.json`

```json
{
  "schemaVersion": 1,
  "id": "myuser.lock",
  "name": "My Lock Screen",
  "version": "1.0.0",
  "author": "Omarchy",
  "description": "Quickshell session lock with face/fingerprint retry and independent password flow.",
  "omarchy": {
    "capabilities": [
      "authentication"
    ],
    "clonedFrom": "omarchy.lock"
  },
  "kinds": [
    "service"
  ],
  "keepLoaded": true,
  "entryPoints": {
    "service": "Service.qml"
  }
}
```

#### 修改 `LockView.qml`：允许空回车触发人脸重试

在密码输入框中，将原本必须输入非空字符才提交密码的逻辑，调整为允许空回车传递给 `Service.qml`：

```qml
// 修改占位提示
readonly property string placeholderText: "Password / Enter for face"

// 在输入框 onAccepted 中放行空回车
onAccepted: {
  var submitted = root.passwordText
  root.passwordTextEdited("")
  root.submitPassword(submitted)
}
```

#### 修改 `Service.qml`：接入亮屏、唤醒与防抖重试状态机

在 `Service.qml` 中扩展生物认证的生命周期管理：

1. **状态属性追踪**：
   ```qml
   property bool displayBlanked: false
   property bool systemSuspending: false
   property bool biometricRestartPending: false
   ```
2. **防抖重启机制**：
   ```qml
   function restartBiometrics(reason) {
     if (!lockRequested || !sessionLock.secure || !fingerprintConfigured || systemSuspending) return
     if (biometricRestartPending) return
     biometricRestartPending = true
     fingerprintRetryTimer.stop()
     // 取消正在进行的旧任务，避免旧任务迟到的成功结果影响新状态
     if (fingerprintPam.active) fingerprintPam.abort()
     fingerprintAuthenticating = false
     biometricRestartTimer.restart()
     logEvent("face-retry: " + reason)
   }
   ```
3. **空回车处理**：
   ```qml
   function submitPassword(value) {
     var password = String(value || "")
     if (!lockRequested || authenticatingPassword) return

     if (password.length === 0) {
       failureMessage = ""
       runWake()
       restartBiometrics("enter")
       return
     }
     // 非空密码按原逻辑提交至 passwordPam
     // ...
   }
   ```
4. **监听 logind 睡眠信号与显示器亮屏**：
   ```qml
   Process {
     id: sleepMonitor
     command: ["/usr/bin/gdbus", "monitor", "--system", "--dest", "org.freedesktop.login1", "--object-path", "/org/freedesktop/login1"]
     running: true
     stdout: SplitParser {
       onRead: function(line) { root.handleSleepMonitorLine(line) }
     }
     onExited: sleepMonitorRetry.restart()
   }

   function handleSleepSignal(sleeping) {
     systemSuspending = sleeping
     if (sleeping) {
       biometricRestartPending = false
       biometricRestartTimer.stop()
       fingerprintRetryTimer.stop()
       if (fingerprintPam.active) fingerprintPam.abort()
       fingerprintAuthenticating = false
     } else if (lockRequested) {
       displayBlanked = false
       runWake()
       restartBiometrics("resume")
     }
   }
   ```
5. **重试延时定时器**：
   设置 750ms 的 `biometricRestartTimer`，确保设备硬件与驱动完全退出旧会话后再启动新一轮 PAM 认证。

### 7. 加载与生效

在已解锁的桌面会话中，重启 Omarchy Shell 并检验插件加载状态：

```bash
# 重启 shell 生效用户锁屏插件
omarchy shell restart

# 检查当前启用的插件是否包含自定义锁屏
grep -rn "myuser.lock" "$FACE_CONFIG_ROOT/omarchy/" 2>/dev/null || true
```

## 验收

人脸认证涉及设备控制、推理性能与交互状态机，必须将独立命令行比对、提权场景与真实锁屏操作分开验证：

| 操作 | 预期结果 | 实测结果 |
|---|---|---|
| 独立本人比对（`howdy test`） | 多次比对均成功识别本人，耗时在合理区间，无异常信号终止 | 已验证（耗时 346–407ms） |
| 非本人 / 遮挡测试 | 明确拒绝认证，超时后正常退出，不发生误认或挂死 | 待完整负向验收 |
| 清空凭据后执行 `sudo` | 终端触发红外补光，人脸匹配成功后直接放行提权 | 已验证 |
| 运行图形授权（如 `pkexec whoami`） | 弹出 Polkit 授权窗口，人脸匹配成功后提权 | 已验证 |
| 正常触发锁屏 | 屏幕锁定后自动开启红外扫描并秒解 | 已验证（解锁耗时约 568ms） |
| 锁屏独立密码输入 | 在红外扫描过程中输入密码回车，密码能立即解锁 | 待严格实测 |
| 锁屏空回车重试 | 密码框为空按 Enter，重新激活红外补光与人脸扫描 | 状态机测试通过 |
| 锁屏熄屏后再点亮 | 移动鼠标或按键点亮屏幕，自动开始新一轮扫描 | 已验证 |
| 系统睡眠挂起后唤醒 | 睡眠前终止旧扫描；唤醒恢复后重新启动人脸识别 | 状态机通过，实机待实测 |
| 连续重试与取消 | 连续敲击 Enter 不引发多重并发扫描，旧认证被干净中止 | 状态机已验证 |
| 合盖模式测试 | 盖上笔记本接入外接屏幕，提权操作直接进入密码/指纹提示 | 待严格实测 |
| 全黑暗光环境 | 仅靠红外发射器补光完成人脸检测与比对 | 全黑场景待实测 |

验收时的辅助验证命令：

```bash
# 验证 sudo 人脸优先
sudo -k
sudo whoami

# 验证 Polkit 图形提权
pkexec whoami

# 检查锁屏事件与 PAM 状态机日志
journalctl --user -u omarchy-shell.service -f -o cat
```

## 回滚

当人脸识别出现异常、补光驱动故障或锁屏逻辑不符合预期时，按最小动作原则执行顺序回滚：

1. **优先切断 PAM 人脸链**：
   从备份恢复 `/etc/pam.d/sudo`、`/etc/pam.d/polkit-1` 和 `/etc/pam.d/omarchy-lock-fingerprint`，确保系统的密码与指纹认证基线立即恢复：
   ```bash
   sudo cp "$FACE_BACKUP/pam/"* /etc/pam.d/
   ```
2. **停用用户锁屏插件**：
   在解锁状态下移除或禁用自定义锁屏插件目录，恢复原版 `omarchy.lock`，随后执行 `omarchy shell restart` 重载桌面。
3. **移除 Polkit 提权 drop-in**：
   ```bash
   sudo rm -rf /etc/systemd/system/polkit-agent-helper-1.service.d/howdy.conf
   sudo systemctl daemon-reload
   ```
4. **移除补光辅助程序与软件包**：
   确认无外部引用后，移除 `/usr/local/libexec/howdy-ir-emitter`。若需卸载 howdy-next，可通过包管理器移除，但保留用户已录入的模板文件以备后续排错。
5. **回滚验收**：
   重新执行 `sudo -k && sudo whoami` 以及锁屏解锁，确认密码及原有指纹功能完全正常。

## 已知限制与踩坑

- **OpenCV 多线程与 `SIGXCPU` 超限**：Howdy-next 对认证子进程设置了 CPU 时间上限；OpenCV DNN 默认的多线程推理会在多核 CPU 上瞬间耗尽该预算，导致扫描被内核信号强行杀死。必须在源码中调用 `cv::setNumThreads(1)` 强制单线程推理。
- **PAM `[success=N]` 跳转计数绑定**：PAM 中的 `[success=3 default=ignore]` 严格依赖其后紧跟的条目数量（补光、人脸、指纹共 3 项）。如果目标机未配置指纹或增加了其他模块，必须重新计算跳转步长，否则合盖检测会跳过正常的密码认证链。
- **锁屏双 PAM 通道 vs 单链 PAM**：Omarchy 锁屏采用了密码与后台生物分离的架构。将人脸接入后台通道并保留指纹备选，能保证用户随时输入密码而不受阻塞。但在传统的单一 PAM 链桌面（如普通 SDDM 或 GDM）上，人脸和密码是串行等待的，无法获得同等的并行解锁体验。
- **UVC 补光控制具有硬件专属协议**：本文给出的 Unit 13 / Selector 14 参数仅对特定型号的 Chicony IR 摄像头有效。其他厂商（如 Sunplus、Realtek）的红外发射器通常采用完全不同的扩展单元或需要通过内核驱动控制，不可直接盲写。
- **锁屏状态机的竞态处理**：亮屏与唤醒事件频繁触发时，如果不显式中止正在进行的旧 PAM 会话并设置防抖（750ms），极易产生并发采集冲突，甚至让旧尝试迟到的成功结果在异常时机触发解锁。
- **红外生物识别的安全边界**：虽然红外成像能阻挡绝大多数普通屏幕翻拍或照片攻击，但当前开源模型（YuNet + SFace）尚未集成严格的 3D 结构光或深度活体检测算法，对高质量仿造物不具备绝对防御能力。

## 参考来源

- [howdy-next 源码仓库](https://codeberg.org/nathawat/howdy-next)：基于 C++ 与 OpenCV DNN 的现代 Howdy 实现。
- [Chicony IR Toggle 协议参考](https://github.com/PetePriority/chicony-ir-toggle)：Chicony 红外摄像头 UVC 控制指令参考。
- [Omarchy 官方仓库](https://github.com/omacom/omarchy)：锁屏架构、插件机制与 PAM 默认配置基线。

## 交给 Agent 的任务

> 请先阅读仓库 AGENTS.md，再按本文检查并配置我的 Omarchy 系统红外人脸认证。核对 IR 摄像头设备路径与补光控制协议，应用 OpenCV 单线程推理补丁安装 howdy-next，现场录入模板并完成独立测试。在备份后定向修改 sudo、polkit-1 和锁屏生物 PAM 通道，通过用户锁屏插件实现亮屏与唤醒重试，保留独立密码通道。完成各场景验收并报告实测结果与回滚方法。
