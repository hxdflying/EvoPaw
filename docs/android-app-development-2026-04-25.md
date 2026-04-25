# EvoPaw Android App 开发文档

> 日期：2026-04-25  
> 目标：在现有 EvoPaw Agent 后端基础上，快速开发一个可在 Android 真机运行的轻量 App，支持文字和语音输入，并复用现有 Agent、Skills、记忆、定时任务和 ASR 能力。

## 1. 结论

推荐路线：

**Expo + React Native + TypeScript + EAS Build + EvoPaw AppAPI 网关**

这条路线最适合当前阶段，原因是：

- 开发快：Expo 可以先用 Expo Go 或 development build 在 Android 真机快速预览。
- 部署轻：个人使用阶段可以用 APK 内部分发；正式上架再构建 AAB。
- 生态成熟：React Native/Expo 对录音、文件、权限、真机调试、云构建都有现成工具。
- 后端复用度高：EvoPaw 已经有 `Runner`、`SessionManager`、`CaptureSender`、`SpeechRecognitionService` 和 TestAPI，App 只需要接入一个生产级 HTTP 网关。
- 安全边界清楚：App 不直接接 Claude、Feishu、DashScope，不在 APK 中放任何模型或飞书凭证。

不建议一开始做原生 Kotlin 或完整 Flutter 项目，除非你明确要长期做复杂 Android 原生能力。对当前“把个人 Agent 助手先跑到手机上”的目标，Expo 的速度和维护成本更合适。

## 2. 当前 EvoPaw 能复用的能力

现有项目已经具备这些基础设施：

- `evopaw/main.py`：启动主进程、Feishu listener、metrics、可选 TestAPI。
- `evopaw/runner.py`：统一消息处理入口，负责 session、slash 命令、附件、语音转写、Agent 调用和回复发送。
- `evopaw/session/manager.py`：按 `routing_key` 管理长期对话。
- `evopaw/api/test_server.py`：已有 HTTP 调试接口，可以作为 AppAPI 的参考实现。
- `evopaw/api/capture_sender.py`：已有同步捕获回复的 sender 模型，可以改造成 App 专用 sender。
- `evopaw/asr/service.py`：服务端 ASR 封装，App 上传音频后可以复用。
- `evopaw/skills/*`：所有工具能力继续由 Agent 后端执行。

当前 TestAPI 只能作为本地调试入口，不应直接暴露给手机长期使用，因为它缺少鉴权、文件大小限制、生产级并发映射、会话列表和错误语义。

## 3. 技术方案对比

| 方案 | 适合度 | 优点 | 缺点 | 结论 |
|---|---:|---|---|---|
| Expo + React Native | 高 | 真机启动快，语音/文件/权限生态完整，EAS 可直接产 APK/AAB | 复杂原生能力需要 development build | 推荐 |
| Flutter | 中 | UI 性能好，Android/iOS 一致性强 | Dart 技术栈，和现有 JS/TS 生态衔接弱一些；初期搭建略重 | 备选 |
| Kotlin 原生 Android | 中低 | Android 原生能力最强 | 开发速度慢，未来 iOS 需重写 | 不作为 MVP |
| Capacitor/Ionic | 中低 | Web 技术迁移快 | 原生体验和语音链路不如 Expo 直接 | 仅在已有 Web App 时考虑 |

## 4. 总体架构

```text
Android App (Expo)
  - Chat UI
  - Text input
  - Push-to-talk recording
  - Local session cache
  - Secure token storage
        |
        | HTTPS / LAN HTTP during dev
        v
EvoPaw AppAPI (new aiohttp app)
  - Auth / device token
  - POST /api/app/message
  - POST /api/app/audio
  - GET /api/app/sessions
  - optional SSE/polling
        |
        v
Existing EvoPaw Core
  - Runner
  - SessionManager
  - Main Agent
  - Skills
  - Memory / ctx / pgvector
  - SpeechRecognitionService
```

关键原则：

- App 只是一个客户端，不直接运行 Agent。
- Agent、Skills、文件处理、ASR、记忆都继续放在 EvoPaw 后端。
- AppAPI 是新的生产网关，不直接复用 `/api/test/message`。
- Feishu 入口继续保留，App 入口只是新增一个 channel。

## 5. 推荐 MVP 范围

第一版只做这些：

- Android 真机可运行。
- 单用户登录或静态 token 鉴权。
- 文字输入、发送、等待回复、展示回复。
- 支持 `/new`、`/status`、`/verbose` 等现有 slash 命令。
- Push-to-talk 录音，上传后由后端转写并让 Agent 回复。
- 本地保存最近消息，断网或 App 重启后能看到最近一屏。
- 后端继续写入 EvoPaw session 历史。

暂不做：

- 多用户权限体系。
- 应用商店上架。
- Push notification。
- 多端实时同步。
- 后台常驻语音唤醒。
- 离线 Agent。

## 6. 后端 AppAPI 设计

### 6.1 新增文件建议

```text
evopaw/api/
├── app_server.py       # 生产 App API aiohttp app
├── app_schemas.py      # Pydantic 请求/响应模型
├── app_sender.py       # App 专用 SenderProtocol 实现
├── app_auth.py         # 简单 token / pairing 鉴权
└── app_audio.py        # 音频保存、探测、转码、转写辅助
```

配置新增：

```yaml
app_api:
  enabled: true
  host: "0.0.0.0"
  port: 9091
  auth_mode: "bearer_token"
  bearer_token: "${EVOPAW_APP_TOKEN}"
  max_upload_mb: 25
  request_timeout_s: 300
```

`main.py` 中和 TestAPI 类似地启动 AppAPI：

```text
if app_api.enabled:
    app_sender = AppSender()
    app_runner = _make_runner(sender=app_sender)
    app = create_app_api_app(...)
    tasks.append(_run_app_api(app, host, port))
```

### 6.2 routing_key 约定

MVP 使用：

```text
app:{device_id}
```

例如：

```text
app:pixel8-hxd
```

这样同一台手机共用一个 active session，现有 `SessionManager.get_or_create()` 可以直接复用。后续如果要做多会话列表，再升级为：

```text
app:{user_id}:{conversation_id}
```

同时更新 metrics 中的 `routing_key_type()`，让 `app:` 归类为 `app`，避免指标全部落入 `unknown`。

### 6.3 AppSender 要点

不要直接把 `CaptureSender` 暴露为生产 sender。原因是当前 `CaptureSender.update_card()` 会 resolve 第一个 pending future，对并发 App 请求不够严谨。

App 专用 sender 应该做到：

- `register(root_id)` 注册 future。
- `send(routing_key, content, root_id)` 按 `root_id` resolve。
- `send_thinking(routing_key, root_id)` 返回可解析的 `card_msg_id`，例如 `app-card:{root_id}`。
- `update_card(card_msg_id, content)` 从 `app-card:{root_id}` 解析出 root_id 后 resolve。
- 对超时 future 做清理，避免内存泄漏。

### 6.4 Text API

请求：

```http
POST /api/app/message
Authorization: Bearer <token>
Content-Type: application/json
```

```json
{
  "device_id": "pixel8-hxd",
  "content": "帮我总结今天的待办",
  "client_msg_id": "uuid-from-app"
}
```

响应：

```json
{
  "msg_id": "app_4f9d2a...",
  "session_id": "s-...",
  "reply": "好的，下面是...",
  "duration_ms": 4231,
  "skills_called": ["search_memory"]
}
```

内部流程：

```text
HTTP request
  -> validate auth
  -> build routing_key = app:{device_id}
  -> msg_id = client_msg_id or server uuid
  -> app_sender.register(msg_id)
  -> runner.dispatch(InboundMessage(...))
  -> wait future with timeout
  -> return reply JSON
```

### 6.5 Audio API

请求：

```http
POST /api/app/audio
Authorization: Bearer <token>
Content-Type: multipart/form-data
```

字段：

```text
device_id: pixel8-hxd
client_msg_id: uuid-from-app
audio: recording.m4a
duration_ms: 5300
note: 可选文字备注
```

推荐后端流程：

```text
multipart upload
  -> validate size and mime
  -> create/get session
  -> save to data/workspace/sessions/{session_id}/uploads/
  -> ffprobe inspect format/sample_rate/channels
  -> if needed, ffmpeg transcode to ASR-compatible format
  -> SpeechRecognitionService.transcribe_file()
  -> build user message:
       用户发送了一条语音消息。
       语音转写：
       {transcript}
       用户备注：
       {note}
  -> dispatch to Runner as normal text message
  -> return transcript + reply
```

注意：Expo 默认高质量录音通常是 `.m4a/AAC`，而当前 EvoPaw 的 ASR 配置默认是 `audio_format: "opus"`、`sample_rate: 16000`，这是为飞书语音链路设置的。App 语音入口不要假设手机录音可直接丢给 Fun-ASR；后端必须先探测音频并做转码，或明确把 ASR 配置切到已验证的格式。

响应：

```json
{
  "msg_id": "app_...",
  "session_id": "s-...",
  "transcript": "帮我查一下明天上午有什么安排",
  "reply": "你明天上午...",
  "duration_ms": 9120
}
```

### 6.6 会话 API

MVP 可以先不做完整会话列表，只提供三个接口：

```http
GET /api/app/session/current?device_id=pixel8-hxd
POST /api/app/session/new
GET /api/app/session/history?device_id=pixel8-hxd&limit=50
```

`/session/new` 内部可以直接复用 `SessionManager.create_new_session(routing_key)`，语义等价于发送 `/new`。

### 6.7 同步还是异步

MVP 推荐同步请求：

```text
POST /api/app/message -> 等 Agent 完成 -> 返回 reply
```

优点是实现最少，App 逻辑也简单。缺点是长任务可能等待 1-5 分钟，移动网络可能断开。

Phase 2 再升级异步：

```text
POST /api/app/message -> 202 Accepted + msg_id
GET /api/app/message/{msg_id} -> pending/done/error
GET /api/app/message/{msg_id}/events -> SSE progress
```

如果你经常让 Agent 做长报告、文件处理、网页研究，异步模式应该尽早做。

## 7. Android App 技术栈

推荐依赖：

```text
Expo SDK
React Native
TypeScript
Expo Router
expo-audio
expo-file-system
expo-secure-store
@react-native-async-storage/async-storage
```

可选：

```text
zustand                  # 简单状态管理
react-native-markdown-display
react-native-reanimated # 动效，后期再加
expo-dev-client         # 需要自定义 native config 后使用
```

项目结构建议：

```text
evopaw-mobile/
├── app/
│   ├── _layout.tsx
│   ├── index.tsx             # Chat screen
│   └── settings.tsx
├── src/
│   ├── api/
│   │   ├── client.ts         # fetch wrapper
│   │   ├── messages.ts
│   │   └── audio.ts
│   ├── components/
│   │   ├── ChatBubble.tsx
│   │   ├── MessageInput.tsx
│   │   ├── VoiceButton.tsx
│   │   └── StatusBar.tsx
│   ├── storage/
│   │   ├── secure.ts
│   │   └── localMessages.ts
│   ├── hooks/
│   │   ├── useChat.ts
│   │   └── useRecorder.ts
│   └── types.ts
├── app.json
├── eas.json
└── package.json
```

## 8. App UI 设计

首屏就是聊天，不做营销页。

页面组成：

- 顶部：EvoPaw 标题、连接状态、设置按钮。
- 主区：消息气泡列表，支持 markdown 渲染。
- 底部输入区：文本输入框、发送按钮、麦克风按钮。
- 语音状态：录音中显示时长和停止按钮；上传/转写/思考中显示轻量状态。

状态模型：

```ts
type Message = {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  transcript?: string;
  status: "sending" | "transcribing" | "thinking" | "done" | "error";
  createdAt: number;
};
```

错误提示：

- 后端连不上：显示“无法连接 EvoPaw 后端，检查网络或 API 地址。”
- 鉴权失败：显示“App token 无效，请在设置中重新配置。”
- 语音权限被拒绝：显示“需要麦克风权限才能录音。”
- Agent 超时：显示“任务还在处理或已超时，可稍后重试。”

## 9. 本地开发步骤

### 9.1 后端准备

先确保 EvoPaw 能本地启动：

```bash
python3 -m evopaw.main
```

开发 AppAPI 时建议端口：

```text
http://0.0.0.0:9091
```

手机访问电脑服务时不能使用电脑上的 `127.0.0.1`，因为手机里的 `127.0.0.1` 指手机自己。可选方案：

1. 同一 Wi-Fi：用电脑局域网 IP，例如 `http://192.168.1.20:9091`。
2. USB 调试：使用 `adb reverse tcp:9091 tcp:9091`，App 中填 `http://127.0.0.1:9091`。
3. 远程访问：使用 Tailscale、Cloudflare Tunnel 或部署到 VPS，并启用 HTTPS。

### 9.2 创建 Expo 项目

```bash
npx create-expo-app@latest evopaw-mobile
cd evopaw-mobile
npx expo install expo-audio expo-file-system expo-secure-store @react-native-async-storage/async-storage
npx expo start
```

Android 手机上安装 Expo Go，扫描终端二维码即可预览。需要自定义原生配置或内部 APK 时，再切到 development build。

### 9.3 Android 真机调试

手机开启开发者模式和 USB 调试后：

```bash
adb devices
adb reverse tcp:9091 tcp:9091
```

然后启动 Expo：

```bash
EXPO_PUBLIC_EVOPAW_API_BASE=http://127.0.0.1:9091 npx expo start --localhost
```

如果不用 USB，使用局域网：

```bash
EXPO_PUBLIC_EVOPAW_API_BASE=http://192.168.1.20:9091 npx expo start --lan
```

### 9.4 构建 APK

安装 EAS CLI：

```bash
npm install -g eas-cli
eas login
```

`eas.json`：

```json
{
  "build": {
    "development": {
      "developmentClient": true,
      "distribution": "internal"
    },
    "preview": {
      "distribution": "internal",
      "android": {
        "buildType": "apk"
      }
    },
    "production": {
      "android": {
        "buildType": "app-bundle"
      }
    }
  }
}
```

构建个人安装 APK：

```bash
eas build --platform android --profile preview
```

正式 Google Play 上架再用 `production` 生成 AAB。

## 10. 安全设计

MVP 可用静态 token，但必须满足：

- token 只保存在后端环境变量和手机 secure storage。
- 不提交到 git。
- 所有 AppAPI 请求带 `Authorization: Bearer <token>`。
- 后端限制上传大小和允许的 MIME/扩展名。
- 生产访问必须用 HTTPS。
- 不把 `ANTHROPIC_API_KEY`、`DASHSCOPE_API_KEY`、`FEISHU_APP_SECRET` 放入 App。

更好的 Phase 2：

- App 首次打开显示 pairing code。
- 后端 CLI 或管理页确认 pairing。
- 后端签发 device token。
- 支持撤销设备。

## 11. 测试计划

### 11.1 后端单元测试

新增测试：

```text
tests/unit/test_app_sender.py
tests/unit/test_app_api_auth.py
tests/integration/test_app_api.py
tests/unit/test_app_audio.py
```

覆盖：

- `AppSender` 并发请求不会串回复。
- token 缺失/错误返回 401。
- `/api/app/message` 能构造 `InboundMessage` 并返回 reply。
- `/api/app/session/new` 能切换 session。
- 音频上传超限返回 413。
- 非音频文件返回 400。

### 11.2 后端集成测试

用 fake agent：

```text
POST /api/app/message -> echo reply
POST /api/app/audio -> fake transcript + echo reply
```

再用真实 Agent 做一轮手工验收：

- 发送“你好”。
- 发送 `/status`。
- 发送 `/new`。
- 发送 3 秒中文语音。
- 断网重试。

### 11.3 App 测试

手工验收：

- Android 真机启动成功。
- API 地址和 token 可配置。
- 文字消息发送后 UI 有 sending/thinking/done 状态。
- 回复支持多段文本和 markdown。
- 拒绝麦克风权限时 UI 正确提示。
- 录音 3 秒后上传成功。
- 后端关闭时显示连接错误，不闪退。

## 12. 分阶段实施计划

### Phase 1：后端 AppAPI 文本链路

目标：手机或 curl 可以通过 `/api/app/message` 调起现有 Agent。

任务：

- 新增 `app_schemas.py`。
- 新增 `app_sender.py`。
- 新增 `app_auth.py`。
- 新增 `app_server.py`。
- `main.py` 增加 `app_api.enabled` 启动分支。
- metrics 支持 `app:` routing key。
- 写单元和集成测试。

验收：

```bash
curl -X POST http://127.0.0.1:9091/api/app/message \
  -H "Authorization: Bearer $EVOPAW_APP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"device_id":"pixel8-hxd","content":"你好"}'
```

能返回 Agent 回复。

### Phase 2：Expo App 文本聊天

目标：Android 真机可发送文字并看到回复。

任务：

- 创建 `evopaw-mobile/`。
- 实现 API client。
- 实现 chat screen。
- 实现 settings screen，保存 API base URL 和 token。
- 实现本地消息缓存。
- 真机联调。

验收：

- 手机上输入“你好”，能收到 EvoPaw 回复。
- 重启 App 后最近消息还在。
- 后端不可达时有错误提示。

### Phase 3：语音上传和转写

目标：手机录音后上传，后端转写，Agent 基于转写回复。

任务：

- App 接入 `expo-audio`，实现 push-to-talk。
- App 上传 multipart audio。
- 后端新增 `/api/app/audio`。
- 后端加入 ffprobe/ffmpeg 音频探测与转码层。
- 后端复用 `SpeechRecognitionService`。
- 回复中展示 transcript 和 answer。

验收：

- 3 秒中文语音能转写并回答。
- 20 秒语音不会让 App 假死。
- 错误音频有可读错误。

### Phase 4：远程访问和 APK

目标：不依赖开发电脑 USB，也能在手机使用。

任务：

- 后端部署到一台长期在线机器，或用 Tailscale/Cloudflare Tunnel。
- 配 HTTPS。
- EAS 构建 preview APK。
- 手机安装 APK。
- token 存入 secure storage。

验收：

- 手机 4G/5G 网络下能访问。
- APK 关闭再打开仍能对话。
- 后端日志可看到 app routing key。

### Phase 5：体验增强

可选增强：

- 异步任务和轮询/SSE。
- Agent verbose 过程展示。
- 文件/图片上传。
- 多会话列表和重命名。
- Push notification。
- Android 分享菜单：从浏览器/文件管理器分享到 EvoPaw。

## 13. 风险和处理

| 风险 | 影响 | 处理 |
|---|---|---|
| 手机无法访问本机后端 | App 请求失败 | 用局域网 IP、`adb reverse`、Tailscale 或公网 HTTPS |
| Agent 任务过长导致 HTTP 超时 | App 等待失败 | MVP 提高 timeout；Phase 2 做异步任务 |
| 语音格式不兼容 ASR | 转写失败 | 后端 ffprobe + ffmpeg 统一转码 |
| CaptureSender 并发串回复 | 回复错配 | 实现 AppSender，按 root_id/card_msg_id resolve |
| APK 暴露密钥 | 后端凭证泄漏 | App 只保存 App token，不保存模型/飞书/ASR key |
| Android 明文 HTTP 访问受限 | release 包请求失败 | 生产使用 HTTPS；本地开发用 debug/dev build |
| 后端 AppAPI 暴露到公网 | 被滥用产生模型费用 | token、限流、上传大小限制、日志审计 |

## 14. 需要优先做的决策

1. App 放在同一个 repo 的 `evopaw-mobile/`，还是新建独立 repo。推荐先放同 repo，方便联调。
2. MVP 是否只支持单设备。推荐先单设备。
3. 语音回复是否展示转写文本。推荐展示，便于确认 ASR 误差。
4. 后端部署方式。个人使用推荐先 Tailscale，稳定后再 VPS + HTTPS。

## 15. 参考官方文档

- Expo 环境和真机开发：https://docs.expo.dev/get-started/set-up-your-environment/
- Expo development build：https://docs.expo.dev/develop/development-builds/create-a-build/
- EAS Build：https://docs.expo.dev/build/introduction/
- Expo Audio：https://docs.expo.dev/versions/latest/sdk/audio/
- Expo FileSystem：https://docs.expo.dev/versions/latest/sdk/filesystem/
- React Native Android 真机运行：https://reactnative.dev/docs/0.82/running-on-device
- Flutter Android 发布文档：https://docs.flutter.dev/deployment/android

