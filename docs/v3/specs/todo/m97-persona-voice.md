> 状态：TODO（先搁置，不要照此实施）
> 阶段：P5。领域亮点及之后的规格先搁置，等 P0–P3（M63–M85）落地后再决定是否升为可执行规格。
> 本文件正文是提前写好的草稿，执行者不要从这里开工。

# M97 人格与语音（规格）

> 阶段：P5　依赖：M96　分支：`persona-voice`　迁移：无
> 本文是 M97 的唯一规格；实施偏离记在 [13 §8](../../13-决策记录.md)。
> 开工前必须让用户确认 [13 §9](../../13-决策记录.md) 的 Q1（人格设定）与 Q2（语音用浏览器还是远端），没有答复不开工（§0 第 3 条、§10 第 1 条）。

## 0. 前置检查

| # | 检查什么 | 命令 | 期望 |
|---|---|---|---|
| 1 | 在 dev 上新建的分支，工作区干净 | `git status -sb` | `## persona-voice`，没有改动 |
| 2 | M96 已合并 | `git tag -l 'v3-m96'` | `v3-m96` |
| 3 | 用户已答复 Q1、Q2 | `rg -n "Q1\|Q2\|人格与语音" docs/v3/13-决策记录.md` | §8 有一条标题含「人格与语音」的记录，写明 Q1、Q2 的答复与日期；§9 的 Q1、Q2 两行标为已拍板并指向它。没有就停下，按 §10 第 1 条问用户，拿到答复后先补这条记录（并按 §10 第 1 条的对照表改默认值），再继续 |
| 4 | 系统提示的组装位置 | `rg -n "common" maa_api/agent/context/layout.py` | 找到把 `common.md` 拼进系统提示的函数（M67 的 `ContextLayout`）；函数名与「段落列表」的写法记进台账，T2 在它后面插人格段 |
| 5 | 总控定义 | `rg -n "^kind: orchestrator" maa_api/agent/specs/builtin/*.md` | 一行，文件是 `orchestrator.md` |
| 6 | 按名字取模型档案并解密 key 的函数 | `rg -n "api_key_encrypted\|def .*profile" maa_api/agent/llm/profiles.py` | 找到；签名记进台账，T3 用它 |
| 7 | 模型客户端的代理与超时写法 | `rg -n "proxy\|trust_env\|timeout" maa_api/agent/llm/client.py` | 记进台账，语音客户端用同一套代理设置 |
| 8 | 前端文件都在 | `ls web/src/features/agent/Composer.tsx web/src/features/console/conversation/ConversationComposer.tsx web/src/features/console/ConsolePage.tsx web/src/features/settings/SettingsPage.tsx web/src/features/settings/SettingFieldRow.tsx` | 5 个文件 |
| 9 | 04 的 M97 配置行 | `rg -n "^\| M97 \| .(persona\|voice)\." docs/v3/04-接口与事件协议.md` | 4 行（`persona.enabled`、`persona.name`、`voice.tts_provider`/`voice.stt_provider`、`voice.remote_profile`） |
| 10 | 错误码还没加 | `rg -n "VOICE_UNAVAILABLE" maa_api/domain/errors.py` | 没有输出 |
| 11 | 设置整表测试的位置 | `rg -n "ENV_OVERRIDES" tests/test_settings.py` | 有匹配 |
| 12 | 基线 | `.venv/bin/python -m pytest -q > /tmp/m97-base.log 2>&1; echo $?` 与 `cd web && pnpm test --run > /tmp/m97-vitest-base.log 2>&1; echo $?; cd ..` | 记录退出码与已有失败 |

## 1. 背景与目标

01 §4 把「角色化人格、语音播报、语音输入」排在 M97。人格与语音都只影响「怎么说、怎么听」，不碰做什么。做完后：

1. 人格叠加层：`persona.enabled=true` 时，总控的系统提示在共用约定之后多一段「说话的方式」，用 `persona.name` 的口吻、以 `persona.address` 称呼用户。它只改语气与称呼，不改安全规则、确认口径与事实口径；Worker、子代理、后台代理不受影响。关闭时（默认）总控的系统提示与 M96 逐字节相同。
2. 语音播报：`voice.tts_provider` 为 `browser`（默认）时用浏览器的 `speechSynthesis`，为 `remote` 时经 `POST /api/agent/voice/tts` 调 OpenAI 兼容的 `/audio/speech`。指挥台标题栏有播报开关，按设备记住；打开后朗读总控的新回复。
3. 语音输入：`voice.stt_provider` 为 `browser`（默认）时用 Web Speech API 的 `zh-CN` 识别，为 `remote` 时录音后经 `POST /api/agent/voice/stt` 调 `/audio/transcriptions`。输入栏的麦克风按钮只把识别结果填进草稿，不自动发送。
4. 两个端点只在对应 provider 为 `remote` 时可用，否则返回 503 `VOICE_UNAVAILABLE`；远端失败同样返回 503，带上游状态。
5. 环境不支持时（非安全上下文、浏览器没有识别接口、不能录音、远端没配档案）不显示对应按钮，设置分区说明原因与替代办法（例如输入法自带的语音输入）。
6. 12 §4 的门禁通过；regression 评测在人格关闭时照旧通过，capability 评测不低于 M96 基线。

## 2. 非目标

- 人格作用于 Worker、子代理、后台代理、汇报、界面文案、IM 卡片模板；人格改变安全规则、确认、事实口径或工具选择。
- 唤醒词、连续监听、把语音直接当命令执行、Worker 汇报的单独播报、IM 里的语音消息。
- 保存录音或合成音频（服务端不落盘、不写日志、不进审计）。
- 新表、新依赖。不引入 `python-multipart`：识别端点直接收原始音频字节；转发给远端的 multipart 由 httpx 自己拼。
- 改 v2 的通知与 Web Push。

## 3. 相关文档

- [13 §9](../../13-决策记录.md) Q1、Q2；[04](../../04-接口与事件协议.md) §2.5（M97 行）、§5（`VOICE_UNAVAILABLE`）、§6（M97 配置行）；[05](../../05-运行时与代理定义.md) §7（提示词分层）；[11](../../11-前端指挥台.md) §9、§10。
- v2 的安全上下文约束：`rg -n "^## 12\|^### 12.1\|^### 12.2" docs/v2/09-前端重构方案.md`，读「约束事实」与「降级矩阵」两节（麦克风与 Service Worker 一样要求安全上下文）。
- [文案规范](../../../copy-audit/规范-文案规范.md)。
- 既有代码入口：

| 位置 | 符号 | 用途 |
|---|---|---|
| `maa_api/settings.py` | `Settings`（约 477–501）、`SETTING_KEYS`（约 73–157）、`AgentSettings`（约 322，写法参照） | 加两段设置 |
| `maa_api/services/settings_schema.py` | `_FIELDS`（约 46–144） | 设置元数据 |
| `config.template.yaml` | 顶层各段 | 加两段带注释的模板 |
| `tests/test_settings.py` | `ENV_OVERRIDES` 整表（约 327–399）与默认值断言（约 426） | 同步 |
| `maa_api/domain/errors.py` | `ErrorCode`、`ERROR_HTTP_STATUS` | 加错误码 |
| `tests/domain/test_errors.py` | 计数常量 | 同步 |
| `maa_api/api/routers/agent.py` | `_effective_llm_settings`（约 201） | 读生效设置的写法：有 `app.state.setting_service` 就 `await service.refresh()`，否则 `get_settings()` |
| `maa_api/main.py` | `create_app()` 的 `include_router`（约 1523–1544） | 挂语音路由 |
| `maa_api/agent/context/layout.py` | 前置检查 #4 找到的函数 | 插人格段 |
| `web/src/features/agent/Composer.tsx` | `ComposerProps` | 加 `accessory` |
| `web/src/features/console/conversation/ConversationComposer.tsx` | 状态行与草稿 | 放麦克风按钮 |
| `web/src/features/console/ConsolePage.tsx` | 标题栏 `actions` | 放播报开关、接朗读 |
| `web/src/features/settings/SettingsPage.tsx` | `agent` 分区的 `intro`（约 85） | 加语音可用性说明 |
| `web/src/features/settings/SettingFieldRow.tsx` | `FIELD_NOTES`（约 23）、选项渲染（约 130–143） | 加字段说明与选项中文名 |
| `web/src/features/settings/use-settings.ts` | `settingsQueryOptions` | 前端读 provider |
| `web/src/stores/ui.ts` | 主题偏好的 localStorage 写法 | 播报开关照抄 |
| `web/src/features/agent/stream.ts` | `requestId` | 语音请求的 `X-Request-Id` |

## 4. 设计

### 4.1 数据模型

无。

### 4.2 模块与文件

后端：

| 路径 | 新建或修改 | 职责 |
|---|---|---|
| `maa_api/agent/persona/__init__.py` | 新 | 导出 `render_persona_section` |
| `maa_api/agent/persona/render.py` | 新 | 选模板、填名字与称呼 |
| `maa_api/agent/persona/builtin/amiya.md`、`generic.md` | 新 | 人格模板（§4.7） |
| 前置检查 #4 的 `layout.py` 函数 | 改 | 总控的系统提示插人格段 |
| `maa_api/agent/voice/__init__.py`、`voice/remote.py` | 新 | 远端语音客户端 |
| `maa_api/api/routers/voice.py` | 新 | 两个端点 |
| `maa_api/main.py` | 改 | `include_router(voice.router)`，紧跟其他 agent 组路由 |
| `maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml` | 改 | 两段设置 |
| `maa_api/domain/errors.py` | 改 | `VOICE_UNAVAILABLE` |
| `docs/v3/14-现行接口清单.md`、`docs/v3/15-开放API接入指南.md` | 改 | 两条路由、一个错误码（指南由脚本重新生成） |
| `tests/agent/test_persona.py`、`tests/api/test_voice_router.py` | 新 | §6 |
| `tests/test_settings.py`、`tests/test_vapid_settings.py`（不加 config-only 键，所以不用改）、`tests/domain/test_errors.py` | 改 | 计数与整表 |

```python
# maa_api/settings.py（新增）
VoiceProvider = Literal["off", "browser", "remote"]

class PersonaSettings(BaseModel):
    """总控的人格叠加层：只改语气与称呼。"""
    model_config = ConfigDict(extra="ignore")
    enabled: bool = False
    name: str = Field(default="阿米娅", min_length=1, max_length=16, pattern=r"^[^\r\n\t]+$")
    address: str = Field(default="博士", min_length=1, max_length=16, pattern=r"^[^\r\n\t]+$")

class VoiceSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    tts_provider: VoiceProvider = "browser"
    stt_provider: VoiceProvider = "browser"
    remote_profile: str = Field(default="", max_length=48)
    tts_model: str = Field(default="tts-1", max_length=128)
    tts_voice: str = Field(default="alloy", max_length=64)
    stt_model: str = Field(default="whisper-1", max_length=128)

# Settings 类末尾加：
#   persona: PersonaSettings = Field(default_factory=PersonaSettings)
#   voice: VoiceSettings = Field(default_factory=VoiceSettings)
# SETTING_KEYS 加 9 项，形如 "persona.enabled": ("persona", "enabled")、"voice.tts_provider": ("voice", "tts_provider")
```

```python
# maa_api/agent/persona/render.py
BUILTIN_TEMPLATES: dict[str, str] = {"阿米娅": "amiya.md"}
GENERIC_TEMPLATE = "generic.md"
SECTION_MARKER = "## 说话的方式"

def render_persona_section(persona: PersonaSettings) -> str | None:
    """persona.enabled 为假返回 None；否则读 builtin/ 下的模板（名字在 BUILTIN_TEMPLATES 里用对应文件，
    否则用 generic.md），把 {name}、{address} 两个占位符用 str.replace 换掉（不用 str.format），去掉首尾空白后返回。
    模板文件在模块加载时读一次并缓存。"""
```

插入规则（改前置检查 #4 的函数）：组装总控（`spec.kind == "orchestrator"`）的系统提示时，在 `common.md` 段之后、角色附加段之前插入 `render_persona_section(settings.persona)` 的结果；返回 `None` 时什么都不插（不多出空行或分隔符）。其他种类的代理不调用它。设置从调用方已有的生效设置里取，不在组装函数里另读配置文件。人格段是稳定内容，开关一次会让总控的前缀缓存失效一次，之后照常命中（06 §2）。

```python
# maa_api/agent/voice/remote.py
TTS_TIMEOUT_SECONDS = 30
STT_TIMEOUT_SECONDS = 60

class RemoteVoiceClient:
    def __init__(self, *, base_url: str, api_key: str, proxy: str | None,
                 transport: httpx.AsyncBaseTransport | None = None) -> None: ...

    async def synthesize(self, text: str, *, model: str, voice: str) -> tuple[bytes, str]:
        """POST {base_url}/audio/speech，JSON {model, input: text, voice, response_format: "mp3"}，
        Authorization: Bearer <key>；返回 (音频字节, 响应的 content-type，缺省 audio/mpeg)。"""

    async def transcribe(self, audio: bytes, content_type: str, *, model: str) -> str:
        """POST {base_url}/audio/transcriptions，multipart：file=("speech.<扩展名>", audio, content_type)、
        model、language="zh"、response_format="json"；返回响应 JSON 的 text（缺失时为空串）。"""

async def build_voice_client(app_state: Any, settings: Settings) -> RemoteVoiceClient:
    """按 settings.voice.remote_profile 取档案（前置检查 #6 的函数）并解密 key。
    remote_profile 为空、档案不存在或 base_url 为空时抛 AppError(VOICE_UNAVAILABLE)。
    app_state 上有 voice_http_transport 时传给客户端（测试用 httpx.MockTransport）。"""
```

`base_url` 去掉末尾的 `/` 再拼路径（与 v2 LLM 客户端拼 `/chat/completions` 的口径一致）。上游返回非 2xx、超时、连不上，一律抛 `AppError(VOICE_UNAVAILABLE, …)`，`details` 带 `upstream_status`（有的话）与截到 200 字、过了 `redact_remote_data` 的 `upstream_message`，不带 key。扩展名按 `content_type`：webm → `webm`、ogg → `ogg`、mp4 → `m4a`、mpeg → `mp3`、wav/x-wav → `wav`。

```python
# maa_api/api/routers/voice.py
router = APIRouter(prefix="/api/agent", tags=["agent"])
TTS_MAX_CHARS = 1000
STT_MAX_BYTES = 10 * 1024 * 1024
STT_CONTENT_TYPES = ("audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav", "audio/x-wav")

class SpeechSynthesisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=TTS_MAX_CHARS)

class SpeechTranscription(BaseModel):
    text: str

@router.post(
    "/voice/tts",
    response_class=Response,
    summary="合成语音",
    responses={**error_responses("VOICE_UNAVAILABLE", "UNAUTHORIZED"),
               200: {"description": "合成好的音频", "content": {"audio/mpeg": {"schema": {"type": "string", "format": "binary"}}}}},
    dependencies=[Depends(require_auth)],
)
async def synthesize_speech(payload: SpeechSynthesisRequest, request: Request) -> Response: ...

@router.post(
    "/voice/stt",
    response_model=SpeechTranscription,
    summary="识别语音",
    openapi_extra={"requestBody": {"required": True, "content": {t: {"schema": {"type": "string", "format": "binary"}} for t in STT_CONTENT_TYPES}}},
    responses=error_responses("INVALID_PARAMETER", "VOICE_UNAVAILABLE", "UNAUTHORIZED"),
    dependencies=[Depends(require_auth)],
)
async def transcribe_speech(request: Request) -> SpeechTranscription: ...
```

operationId 因此是 `agent_synthesize_speech`、`agent_transcribe_speech`（04 §2.5）。

前端（路径都在 `web/src/` 下）：

| 路径 | 新建或修改 | 职责 |
|---|---|---|
| `features/voice/voice-settings.ts` | 新 | 从设置缓存读三项 |
| `features/voice/capabilities.ts` | 新 | 环境探测与可用性判断 |
| `features/voice/speech-text.ts` | 新 | 回复转朗读文本 |
| `features/voice/api.ts` | 新 | 两个端点的请求 |
| `features/voice/voice-store.ts` | 新 | 播报开关（按设备） |
| `features/voice/use-speech-output.ts` | 新 | 朗读与「新回复才读」 |
| `features/voice/use-speech-input.ts` | 新 | 浏览器识别与远端录音 |
| `features/voice/MicButton.tsx`、`SpeakToggle.tsx`、`VoiceSupportNote.tsx` | 新 | 三个组件 |
| `features/agent/Composer.tsx` | 改 | `accessory?: ReactNode`，渲染在输入框与发送按钮之间 |
| `features/console/conversation/ConversationComposer.tsx` | 改 | 放 `MicButton`，状态行加语音状态 |
| `features/console/ConsolePage.tsx` | 改 | 标题栏加 `SpeakToggle`，接 `useOrchestratorPlayback` |
| `features/settings/SettingsPage.tsx` | 改 | `agent` 分区 `intro` 改成 `<><AgentCapabilityCheck /><VoiceSupportNote /></>` |
| `features/settings/SettingFieldRow.tsx` | 改 | `FIELD_NOTES` 加 5 项；新增 `OPTION_LABELS`，分段控件与下拉都按它显示选项 |

### 4.3 接口

`POST /api/agent/voice/tts`：

| 情形 | 状态码 | 错误码 | message |
|---|---|---|---|
| 成功 | 200 | | 响应体是音频字节，`Content-Type` 取上游（缺省 `audio/mpeg`），`Cache-Control: no-store` |
| `voice.tts_provider` 不是 `remote` | 503 | `VOICE_UNAVAILABLE` | `语音播报没有设为远端服务` |
| `voice.remote_profile` 为空 | 503 | `VOICE_UNAVAILABLE` | `还没有给远端语音选模型档案，到设置里选一个` |
| 档案不存在或没有地址 | 503 | `VOICE_UNAVAILABLE` | `找不到模型档案「{name}」，到设置里重新选` |
| 上游返回非 2xx | 503 | `VOICE_UNAVAILABLE` | `远端语音服务返回 {status}`，`details.upstream_status`、`details.upstream_message` |
| 超时或连不上 | 503 | `VOICE_UNAVAILABLE` | `连不上远端语音服务，稍后重试` |
| `text` 为空或超过 1000 字 | 422 | `VALIDATION_ERROR` | 框架校验 |

`POST /api/agent/voice/stt`（请求体是原始音频，`Content-Type` 为 `STT_CONTENT_TYPES` 之一，可带 `;codecs=…` 参数）：

| 情形 | 状态码 | 错误码 | message |
|---|---|---|---|
| 成功 | 200 | | `{"text": "把日常做了"}`；没听出内容时 `{"text": ""}` |
| `voice.stt_provider` 不是 `remote` | 503 | `VOICE_UNAVAILABLE` | `语音输入没有设为远端服务` |
| `Content-Type` 去掉参数后不在列表里 | 400 | `INVALID_PARAMETER` | `不支持这种录音格式` |
| `Content-Length` 或实际字节超过 10 MiB | 400 | `INVALID_PARAMETER` | `录音太长，请控制在一分钟以内` |
| 请求体为空 | 400 | `INVALID_PARAMETER` | `没有收到录音` |
| 档案与上游的各种失败 | 503 | `VOICE_UNAVAILABLE` | 同 TTS 表 |

两个端点都不写审计、不落盘；日志只记一行「语音合成 / 识别：字节数、耗时、上游状态」，不记文本与音频。SSE 与 WebSocket 不加事件。

### 4.4 配置项

| 键 | 类型 | 默认 | 范围 | 分组 | `_FIELDS` | 说明 |
|---|---|---|---|---|---|---|
| `persona.enabled` | bool | `false` | | Agent | `dict(label="启用人格", group="Agent", type="boolean", hot_action="none")` | 04 §6 已有 |
| `persona.name` | str | `阿米娅` | 1–16 字，不含换行 | Agent | `dict(label="人格角色", group="Agent", type="string", hot_action="none")` | 04 §6 已有 |
| `persona.address` | str | `博士` | 1–16 字，不含换行 | Agent | `dict(label="对你的称呼", group="Agent", type="string", hot_action="none")` | 新增 |
| `voice.tts_provider` | enum | `browser` | off / browser / remote | Agent | `dict(label="语音播报", group="Agent", type="string", enum=["off", "browser", "remote"], hot_action="none")` | 04 §6 已有 |
| `voice.stt_provider` | enum | `browser` | off / browser / remote | Agent | `dict(label="语音输入", group="Agent", type="string", enum=["off", "browser", "remote"], hot_action="none")` | 04 §6 已有 |
| `voice.remote_profile` | str | 空 | 档案名 | Agent | `dict(label="远端语音的模型档案", group="Agent", type="string", hot_action="none")` | 04 §6 已有 |
| `voice.tts_model` | str | `tts-1` | | Agent | `dict(label="远端播报模型", group="Agent", type="string", hot_action="none")` | 新增 |
| `voice.tts_voice` | str | `alloy` | | Agent | `dict(label="远端播报音色", group="Agent", type="string", hot_action="none")` | 新增 |
| `voice.stt_model` | str | `whisper-1` | | Agent | `dict(label="远端识别模型", group="Agent", type="string", hot_action="none")` | 新增 |

都不是 config-only，`tests/test_vapid_settings.py` 不用改。默认值以 Q1、Q2 的答复为准（§10 第 1 条的对照表），与上表不同时同步改 04 §6 并在 13 §8 记录。

`config.template.yaml` 片段：

```yaml
persona:
  # 只作用于总控：改变说话的语气与称呼，不影响确认、安全规则与事实口径
  enabled: false
  name: 阿米娅
  address: 博士

voice:
  # off 关闭；browser 用浏览器自带的朗读与识别；remote 用 remote_profile 档案的 OpenAI 兼容语音接口
  tts_provider: browser
  stt_provider: browser
  # 模型档案名（设置 › 模型与角色）；只取它的地址与密钥
  remote_profile: ""
  tts_model: tts-1
  tts_voice: alloy
  stt_model: whisper-1
```

`tests/test_settings.py` 的 `ENV_OVERRIDES` 加 9 项，环境变量名按规则生成：`MAA_PERSONA_ENABLED`、`MAA_PERSONA_NAME`、`MAA_PERSONA_ADDRESS`、`MAA_VOICE_TTS_PROVIDER`、`MAA_VOICE_STT_PROVIDER`、`MAA_VOICE_REMOTE_PROFILE`、`MAA_VOICE_TTS_MODEL`、`MAA_VOICE_TTS_VOICE`、`MAA_VOICE_STT_MODEL`。

前端字段说明（`SettingFieldRow.tsx` 的 `FIELD_NOTES`）：

| 键 | 说明 |
|---|---|
| `persona.enabled` | `只改变总控说话的语气与称呼，不影响确认与安全规则` |
| `persona.name` | `内置「阿米娅」的口吻；填其他名字时用平稳的通用语气` |
| `voice.tts_provider` | `浏览器：用系统自带的朗读；远端服务：用下面的模型档案` |
| `voice.stt_provider` | `浏览器：网页语音识别，需要 HTTPS；远端服务：录音后上传识别` |
| `voice.remote_profile` | `填「模型与角色」里的档案名，只用它的地址和密钥` |

`OPTION_LABELS`：`voice.tts_provider` 与 `voice.stt_provider` 都是 `{ off: '关闭', browser: '浏览器', remote: '远端服务' }`；没登记的键与取值照旧显示原值。

### 4.5 行为与算法

#### 4.5.1 端点

```
synthesize_speech(payload):
  settings = 生效设置（_effective_llm_settings 同样的取法）
  settings.voice.tts_provider != "remote" → 503「语音播报没有设为远端服务」
  client = await build_voice_client(request.app.state, settings)
  audio, content_type = await client.synthesize(payload.text, model=settings.voice.tts_model, voice=settings.voice.tts_voice)
  return Response(audio, media_type=content_type, headers={"Cache-Control": "no-store"})

transcribe_speech(request):
  settings.voice.stt_provider != "remote" → 503「语音输入没有设为远端服务」
  base_type = request.headers["content-type"] 去掉 ";" 之后的参数并转小写；不在 STT_CONTENT_TYPES → 400
  Content-Length 头存在且大于 STT_MAX_BYTES → 400「录音太长…」（不读请求体）
  audio = await request.body()；为空 → 400「没有收到录音」；大于 STT_MAX_BYTES → 400「录音太长…」
  client = await build_voice_client(...)
  text = (await client.transcribe(audio, base_type, model=settings.voice.stt_model)).strip()
  return SpeechTranscription(text=text)
```

#### 4.5.2 前端可用性

```ts
// features/voice/voice-settings.ts
export type VoiceProvider = 'off' | 'browser' | 'remote'
export interface VoiceConfig { ttsProvider: VoiceProvider; sttProvider: VoiceProvider; remoteConfigured: boolean }
export function useVoiceConfig(): VoiceConfig
// 读 useQuery(settingsQueryOptions).data.values 的 voice.tts_provider、voice.stt_provider、voice.remote_profile；
// 值不认识或还没读到按 'browser'；remoteConfigured = remote_profile 是非空字符串

// features/voice/capabilities.ts
export type SupportReason = 'ok' | 'off' | 'insecure' | 'unsupported' | 'no_recorder' | 'not_configured'
export interface SpeechSupport { available: boolean; reason: SupportReason }
export interface VoiceEnv { secure: boolean; recognition: unknown; synthesis: unknown; recorder: unknown; getUserMedia: unknown }
export function readVoiceEnv(): VoiceEnv
// secure = window.isSecureContext；recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition；
// synthesis = window.speechSynthesis；recorder = window.MediaRecorder；getUserMedia = navigator.mediaDevices?.getUserMedia
export function speechInputSupport(config: VoiceConfig, env?: VoiceEnv): SpeechSupport
export function speechOutputSupport(config: VoiceConfig, env?: VoiceEnv): SpeechSupport
```

| 判断 | 语音输入 | 语音播报 |
|---|---|---|
| provider 为 `off` | `off` | `off` |
| 非安全上下文 | `insecure`（麦克风要求 HTTPS 或 localhost） | 不影响 |
| `browser` 且没有对应接口 | `unsupported`（没有 `SpeechRecognition`） | `unsupported`（没有 `speechSynthesis`） |
| `remote` 且没配档案 | `not_configured` | `not_configured` |
| `remote` 且没有 `MediaRecorder` 或 `getUserMedia` | `no_recorder` | 不影响 |
| 其余 | `ok` | `ok` |

`available` 只在 `ok` 时为真；不可用时对应按钮不渲染。`VoiceSupportNote` 在两者有一项不是 `ok`、也不是 `off` 时显示一个 `InlineAlert tone="info"`（标题 `语音`），逐条列原因：

| 原因 | 文案 |
|---|---|
| 输入 `insecure` | `语音输入需要通过 HTTPS 或 localhost 访问；现在可以用输入法自带的语音输入。` |
| 输入 `unsupported` | `这个浏览器不支持网页语音识别，可以用输入法自带的语音输入，或把语音输入改成远端服务。` |
| 输入 `no_recorder` | `这个浏览器不能录音，远端语音输入用不了。` |
| 输入或播报 `not_configured` | `远端语音还没有选模型档案。` |
| 播报 `unsupported` | `这个浏览器不支持语音播报。` |

#### 4.5.3 朗读

```ts
// features/voice/speech-text.ts
export function toSpeechText(markdown: string, maxChars = 600): string
```

1. 围栏代码块整体换成 `（代码略）`；以 `|` 开头的表格行，第一处换成 `（表格略）`，其余删掉。
2. `[文字](地址)` 只留文字；裸链接 `https?://\S+` 换成 `（链接略）`；行内代码去掉反引号留内容。
3. 去掉标题的 `#`、列表标记（`- `、`* `、`1. `）、引用的 `> `、强调标记（`**`、`*`、`~~`）。
4. 每行去首尾空白后，行尾没有中文句末标点（`。？；…` 与全角感叹号 `\uFF01`）的补一个 `。`，再把各行连起来；连续空白合成一个。
5. 超过 `maxChars` 时在 `maxChars` 之前最后一个句末标点处截断（找不到就硬截），末尾加 `后面的内容请看屏幕。`。

```ts
// features/voice/use-speech-output.ts
export interface SpeechOutput { speak: (text: string) => void; stop: () => void; speaking: boolean; error: string | null }
export function useSpeechOutput(config: VoiceConfig): SpeechOutput
export function pickChineseVoice(voices: readonly SpeechSynthesisVoice[]): SpeechSynthesisVoice | null
export function useOrchestratorPlayback(messages: readonly V3Message[], output: SpeechOutput, enabled: boolean): void
```

- 浏览器：`speak` 先 `speechSynthesis.cancel()`，再建 `SpeechSynthesisUtterance`，`lang='zh-CN'`、`rate=1`、`voice=pickChineseVoice(getVoices())`（取 `lang` 不分大小写等于 `zh-CN` 或 `zh_CN` 的第一个；没有就取 `zh` 开头的第一个；再没有为 `null`，用浏览器默认）；`onerror` 的 `interrupted`、`canceled` 不算错误，其他写 `播报失败：{error}`。`getVoices()` 为空时等一次 `voiceschanged` 再选。
- 远端：中止上一次请求与播放；`synthesizeSpeech(text)` 拿 blob，`new Audio(URL.createObjectURL(blob))` 播放，`ended` 或换下一段时 `revokeObjectURL`；`play()` 被 `NotAllowedError` 拒绝时写 `浏览器拦住了自动播放，点一下播报开关再试`；接口报错写 `播报失败：{原因}`。
- `useOrchestratorPlayback`：`enabled` 变为真时把「已读到的最大 id」设为当前历史的最大 id（不读旧消息）；之后历史里出现 id 更大的消息时，取其中最后一条 `role=assistant`、不是收件箱事件、`toSpeechText` 结果非空的消息朗读一次，再把最大 id 推进到全部新消息的最大值；`enabled` 变为假或卸载时 `stop()`。只在指挥台用，工单页不朗读。

#### 4.5.4 听写

```ts
// features/voice/use-speech-input.ts
export type ListenState = 'idle' | 'listening' | 'transcribing'
export interface SpeechInput { state: ListenState; interim: string; error: string | null; start: () => void; stop: () => void }
export function useSpeechInput(config: VoiceConfig, onText: (text: string) => void): SpeechInput
export const RECORDER_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'] as const
export const MAX_RECORD_MS = 60_000
```

- 浏览器：`new Recognition()`，`lang='zh-CN'`、`interimResults=true`、`continuous=false`、`maxAlternatives=1`；`onresult` 拼出临时与最终文本，临时文本写进 `interim`；`onend` 时最终文本非空就 `onText(最终文本)`，回到 `idle`；再点一次按钮 `stop()`。
- 远端：`getUserMedia({ audio: true })`，`MediaRecorder` 用 `RECORDER_TYPES` 里第一个 `isTypeSupported` 的类型（都不支持就不传）；`MAX_RECORD_MS` 到点自动停；停止后关掉音轨、进入 `transcribing`，`transcribeSpeech(blob)`（`Content-Type` 取 `blob.type` 去掉参数，缺省 `audio/webm`）；结果为空写 `没有听清，再说一次`，否则 `onText(text)`。
- 错误文案：

| 来源 | 文案 |
|---|---|
| `not-allowed`、`service-not-allowed`、`getUserMedia` 的 `NotAllowedError` | `没有麦克风权限，到浏览器的网站设置里允许后再试` |
| `network` | `浏览器的语音识别服务连不上，可以用输入法的语音输入，或在设置里改用远端服务` |
| `no-speech` | `没有听到声音` |
| `audio-capture`、`NotFoundError` | `没有找到麦克风` |
| `aborted` | 不算错误 |
| 识别接口报错 | `语音识别失败：{原因}` |
| 其他 | `语音识别失败：{错误名}` |

```ts
// features/voice/api.ts（都用 globalThis.fetch，带 X-Token 与 X-Request-Id；401 调 useAuth.getState().onUnauthorized()；非 2xx 抛 toApiError(body, response)）
export async function synthesizeSpeech(text: string, signal?: AbortSignal): Promise<Blob>   // POST /api/agent/voice/tts，JSON
export async function transcribeSpeech(audio: Blob, signal?: AbortSignal): Promise<string>  // POST /api/agent/voice/stt，原始字节

// features/voice/voice-store.ts（写法照 stores/ui.ts 的主题偏好；localStorage 键 maa-api-voice-playback，值 on / off）
export interface VoiceUiState { playback: boolean; setPlayback: (on: boolean) => void }
export const useVoiceUi: UseBoundStore<StoreApi<VoiceUiState>>
```

#### 4.5.5 边界情况

| 输入 | 期望 |
|---|---|
| 人格关闭 | 总控系统提示与 M96 逐字节相同；regression 回放照旧通过 |
| `persona.name` 是「阿米娅」以外的名字 | 用 `generic.md`，名字与称呼照填 |
| 用户在会话里要求换回平常语气 | 人格段里写了照做；总控在本会话后续回复里用平常语气 |
| 人格开启时用户问消耗类操作 | 确认照常发起，确认卡文案不受人格影响（确认卡是界面文案） |
| 通过 `http://192.168.x.x` 访问 | 麦克风按钮不出现；设置分区说明 HTTPS 要求与输入法替代 |
| Chrome 在连不上 Google 的网络下用浏览器识别 | 报 `network`，提示改用输入法或远端服务 |
| iOS Safari 的 PWA 独立模式没有识别接口 | 按 `unsupported` 处理，按钮不出现 |
| 远端播报时上一段还没读完就来了新回复 | 停掉上一段，读新的 |
| 播报开着时用户点「停止本轮」 | 正在读的照读完；关掉播报开关才立即停 |
| 录音超过一分钟 | 到点自动停止并识别 |
| 远端识别返回空文本 | 提示 `没有听清，再说一次`，草稿不变 |

### 4.6 前端

#### 4.6.1 组件

```ts
// features/voice/MicButton.tsx
export interface MicButtonProps {
  onText: (text: string) => void
  onStatus: (status: ReactNode | null) => void   // 交给输入栏状态行显示：正在听、正在识别、错误
  disabled?: boolean
}
// features/voice/SpeakToggle.tsx
export interface SpeakToggleProps { output: SpeechOutput }
// features/voice/VoiceSupportNote.tsx
export function VoiceSupportNote(): JSX.Element | null
// features/agent/Composer.tsx 新增 prop
//   /** 输入框与发送按钮之间的附加按钮，例如语音输入。 */
//   accessory?: ReactNode
```

- `MicButton`：`speechInputSupport(config).available` 为假时返回 `null`。`Button size="icon" variant="ghost" className="rounded-full"`；空闲时图标 `Mic`、`aria-label="语音输入"`；听写中图标 `MicOff`、`aria-label="停止语音输入"`、`aria-pressed`；识别中 spinner、禁用、`title="正在识别…"`。状态经 `onStatus` 交出：听写中 `正在听：{interim}`（`interim` 为空时 `正在听…`），识别中 `正在识别…`，出错时错误文案（5 秒后清掉），空闲时 `null`。
- `ConversationComposer`：`accessory={<MicButton onText={insertAtCaret} onStatus={setVoiceStatus} disabled={Boolean(disabledReason)} />}`；`insertAtCaret(text)` 在光标处插入（前面不是空白且草稿非空时先补一个空格），插完把光标放到插入内容之后并聚焦输入框。状态行优先级在 M72 的基础上改成：正在停止本轮 > 语音状态 > 其余原有项。
- `SpeakToggle`：`speechOutputSupport(config).available` 为假时返回 `null`。图标按钮：开时 `Volume2`、`aria-label="关闭播报"`，关时 `VolumeX`、`aria-label="打开播报"`，带 `aria-pressed`；打开时立即 `output.speak('已打开播报')`（这一下发生在点击里，iOS 因此允许之后的朗读）；关闭时 `output.stop()`。`output.error` 非空时按钮旁显示一个 `InfoHint` 放错误原文。
- `ConsolePage`：`const config = useVoiceConfig()`、`const output = useSpeechOutput(config)`、`const playback = useVoiceUi((s) => s.playback)`；`useOrchestratorPlayback(controller.history, output, playback && speechOutputSupport(config).available)`；标题栏 `actions` 在「指挥台操作」菜单前放 `<SpeakToggle output={output} />`。
- `VoiceSupportNote`：§4.5.2 的规则；读设置失败时不渲染。

#### 4.6.2 文案

| 位置 | 原文 |
|---|---|
| 麦克风按钮 | `语音输入`、`停止语音输入`；提示 `正在识别…` |
| 状态行 | `正在听…`、`正在听：{临时文本}`、`正在识别…`、`没有听清，再说一次` 与 §4.5.4 的错误文案 |
| 播报开关 | `打开播报`、`关闭播报`；朗读 `已打开播报`；错误 `播报失败：{原因}`、`浏览器拦住了自动播放，点一下播报开关再试` |
| 设置说明 | §4.4 的字段说明与选项中文名；§4.5.2 的可用性文案，标题 `语音` |

### 4.7 提示词与代理定义

`maa_api/agent/persona/builtin/amiya.md`：

```markdown
## 说话的方式

和用户说话时，你用「{name}」的口吻，称呼用户为「{address}」。

- 语气温和、坚定、简洁；先说结论，再说需要{address}做的事。
- 人格只决定语气和称呼。上文的规则、确认与安全约定、工具结果和数字一律照旧执行，不能拿人格设定当理由跳过或改变它们。
- 不编造游戏剧情、角色设定或台词，不复述游戏原文；拿不准的事实照常用工具查，查不到就直说。
- 不用感叹号、颜文字和 emoji。
- {address}要求换回平常的语气时，在本会话里照做。
```

`maa_api/agent/persona/builtin/generic.md`：

```markdown
## 说话的方式

和用户说话时，你自称「{name}」，称呼用户为「{address}」。

- 语气平稳、简洁；先说结论，再说需要{address}做的事。
- 人格只决定语气和称呼。上文的规则、确认与安全约定、工具结果和数字一律照旧执行，不能拿人格设定当理由跳过或改变它们。
- 不编造设定或台词；拿不准的事实照常用工具查，查不到就直说。
- 不用感叹号、颜文字和 emoji。
- {address}要求换回平常的语气时，在本会话里照做。
```

Q1 的答复改了语气要求时，只改 `amiya.md` 第一条要点的措辞；「人格只决定语气和称呼」那条与其后两条不改。代理定义文件不改。人格段插在 05 §7 第 2 段（共用约定）与第 3 段（角色附加段）之间，只给总控。

开启人格的评测：capability 套件里给总控加一个用例 `persona_keeps_confirmation`（人格开启，用户要求用理智药刷图，期望总控建工单、委托范围不擅自加「可以用理智药」以外的内容、回复里称呼用户为「博士」）；用例与录制按 M68 的场景格式写进 `tests/evals/scenarios/`。

## 5. 实施任务

### T1 拍板记录与设置

- 写范围：`docs/v3/13-决策记录.md`（§8 新增一条、§9 两行改状态）、`docs/v3/04-接口与事件协议.md`（只在 Q1、Q2 改了默认值时改 §6 对应行）、`maa_api/settings.py`、`maa_api/services/settings_schema.py`、`config.template.yaml`、`tests/test_settings.py`
- 步骤：
  1. 若前置检查 #3 还没有记录：停下按 §10 第 1 条问用户；拿到答复后在 13 §8 加「人格与语音拍板（M97，日期）」，按 13 §8 的格式写原定、实际、理由、影响；§9 的 Q1、Q2 两行「当前默认」改成「已拍板，见 §8.N」。
  2. 按 §4.2、§4.4 加两段设置、9 个 `SETTING_KEYS`、9 个 `_FIELDS`、模板片段、`ENV_OVERRIDES` 9 项与默认值断言。
- 验证：`.venv/bin/python -m pytest -q tests/test_settings.py tests/test_vapid_settings.py > /tmp/m97-t1.log 2>&1; echo $?` → 0。
- 完成判据：`GET /api/settings/schema` 里有 9 个新键，分组都是 `Agent`。

### T2 人格叠加层

- 写范围：`maa_api/agent/persona/`、前置检查 #4 的 `layout.py` 函数、`tests/agent/test_persona.py`、`tests/evals/scenarios/` 新增的一个场景及其录制
- 步骤：按 §4.2、§4.7 实现；插入点按前置检查 #4 记下的段落写法。
- 验证：`.venv/bin/python -m pytest -q tests/agent/test_persona.py > /tmp/m97-t2.log 2>&1; echo $?` → 0；`.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m97-eval-reg.log 2>&1; echo $?` → 0。
- 完成判据：人格关闭时回放全部通过；§6 的人格用例通过。

### T3 远端语音客户端与端点

- 写范围：`maa_api/agent/voice/`、`maa_api/api/routers/voice.py`、`maa_api/main.py`、`maa_api/domain/errors.py`、`tests/api/test_voice_router.py`、`tests/domain/test_errors.py`、`docs/v3/14-现行接口清单.md`、`docs/v3/15-开放API接入指南.md`
- 步骤：
  1. `ErrorCode` 加 `VOICE_UNAVAILABLE`（上一行中文注释 `# 语音服务不可用`），`ERROR_HTTP_STATUS` 绑 503；14 的错误码表加一行、改总数；`tests/domain/test_errors.py` 的计数常量按定义各加 1。
  2. 按 §4.2、§4.3、§4.5.1 实现客户端与两个端点，`main.py` 挂路由。
  3. 14 的 agent 小节加两行、改小节计数与总数句；`.venv/bin/python scripts/generate_api_guide.py` 重新生成 15 的错误码表；`.venv/bin/python scripts/dump_openapi.py`。
- 验证：`.venv/bin/python -m pytest -q tests/api/test_voice_router.py tests/domain/test_errors.py tests/domain/test_route_inventory.py tests/test_api_guide_generator.py > /tmp/m97-t3.log 2>&1; echo $?` → 0；`.venv/bin/python scripts/generate_api_guide.py --check; echo $?` → 0。
- 完成判据：§6 的端点用例通过。

### T4 前端语音模块

- 写范围：`web/src/features/voice/` 全部文件与测试
- 步骤：按 §4.5.2–§4.5.4、§4.6.1 实现；jsdom 没有 `speechSynthesis`、`SpeechRecognition`、`MediaRecorder`、`getUserMedia`，测试里用 `vi.stubGlobal` 造最小假对象。
- 验证：`cd web && pnpm test --run src/features/voice > /tmp/m97-t4.log 2>&1; echo $?; cd ..` → 0。
- 完成判据：§6 的语音用例通过。

### T5 接进输入栏、指挥台与设置

- 写范围：`web/src/features/agent/Composer.tsx`、`web/src/features/console/conversation/ConversationComposer.tsx`、`web/src/features/console/ConsolePage.tsx`、`web/src/features/console/ConsolePage.test.tsx`、`web/src/features/settings/SettingsPage.tsx`、`web/src/features/settings/SettingFieldRow.tsx`、`web/src/features/settings/settings.test.tsx`
- 步骤：按 §4.4 的前端字段说明、§4.6.1 修改；`pnpm gen:api` 拿到两个端点的生成类型（`api.ts` 仍用 `globalThis.fetch`，类型只用于响应体）。
- 验证：`cd web && pnpm gen:api && pnpm typecheck > /tmp/m97-tc.log 2>&1 && pnpm test --run > /tmp/m97-t5.log 2>&1; echo $?; cd ..` → 0。
- 完成判据：全量 vitest 通过。

### T6 全量验证、文档与台账

- 写范围：`docs/v3/ledgers/m97-persona-voice.md`（新）、与实现不一致的 v3 文档
- 步骤：按 §7 跑；台账写 Q1、Q2 的答复、前置检查 #4、#6、#7 抄下的名字、评测结果、补充检查的设备与浏览器。
- 验证：§7 全部通过。
- 完成判据：12 §4 的十项都满足。

## 6. 测试清单

后端：

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `tests/agent/test_persona.py` | `test_persona_disabled_leaves_orchestrator_prompt_unchanged` | `enabled=False` 时总控系统提示里没有 `## 说话的方式`，且与把人格钩子换成恒返回 `None` 时组装出的结果逐字节相同（没有多出空行） |
| 同上 | `test_persona_section_sits_after_common_rules_for_orchestrator_only` | 开启后总控系统提示里人格段在 `common.md` 内容之后、角色附加段与知识段之前；`daily_ops` 与 `researcher` 的系统提示不含它 |
| 同上 | `test_amiya_template_fills_name_and_address` | `name="阿米娅"`、`address="博士"` 时输出与 §4.7 的 `amiya.md` 填好后的文本相同 |
| 同上 | `test_unknown_persona_name_uses_generic_template` | `name="凯尔希"` 用 `generic.md`，含 `你自称「凯尔希」` |
| 同上 | `test_persona_section_keeps_rules_sentence` | 两个模板渲染后都含 `人格只决定语气和称呼` 那一句 |
| 同上 | `test_persona_name_rejects_line_breaks` | `PersonaSettings(name="阿\n米娅")` 校验失败；17 字的名字校验失败 |
| `tests/api/test_voice_router.py` | `test_tts_requires_remote_provider` | 缺省设置下 503，`error.code == "VOICE_UNAVAILABLE"`，message 是 `语音播报没有设为远端服务` |
| 同上 | `test_tts_requires_a_profile` | `tts_provider=remote`、`remote_profile` 为空时 503 与对应 message |
| 同上 | `test_tts_forwards_to_audio_speech` | `httpx.MockTransport` 收到 POST `<base>/audio/speech`，JSON 是 `{model, input, voice, response_format: "mp3"}`，带 `Authorization: Bearer`；响应体与 `Content-Type` 原样返回，`Cache-Control: no-store` |
| 同上 | `test_tts_upstream_failure_maps_to_voice_unavailable` | 上游 401 时 503，`details.upstream_status == 401`，`details` 里没有 key 原文 |
| 同上 | `test_stt_accepts_raw_audio_and_returns_text` | 发 `audio/webm;codecs=opus` 的字节；上游收到 multipart 的 `file`、`model`、`language=zh`；响应 `{"text": …}` 去掉首尾空白 |
| 同上 | `test_stt_rejects_unsupported_content_type` | `application/octet-stream` 时 400 `INVALID_PARAMETER`，message `不支持这种录音格式` |
| 同上 | `test_stt_rejects_empty_or_oversized_audio` | 空体 400 `没有收到录音`；`Content-Length` 超过 10 MiB 时 400 `录音太长…` 且没有调用上游 |
| 同上 | `test_voice_routes_are_in_agent_group` | 两条路径的 operationId 是 `agent_synthesize_speech`、`agent_transcribe_speech`，`tags == ["agent"]` |
| `tests/test_settings.py` | 既有整表与默认值用例 | 9 个新键的环境变量与默认值 |
| `tests/domain/test_errors.py` | 既有计数用例 | 计数加 1 |

前端（路径相对 `web/src/`）：

| 文件 | 用例名 | 断言要点 |
|---|---|---|
| `features/voice/speech-text.test.ts` | `strips markdown, code, tables and links before speaking` | §4.5.3 的 1–4 条 |
| 同上 | `truncates long replies at a sentence end` | 截断位置与结尾句 |
| `features/voice/capabilities.test.ts` | `hides speech input on insecure origins` | `secure=false` → `insecure` |
| 同上 | `reports browsers without speech recognition as unsupported` | 没有识别接口 → `unsupported`；没有朗读接口 → 播报 `unsupported` |
| 同上 | `needs a profile and MediaRecorder for remote input` | `not_configured` 与 `no_recorder` |
| 同上 | `allows browser speech output without a secure context` | 播报在非安全上下文仍 `ok` |
| `features/voice/use-speech-output.test.tsx` | `speaks only replies that arrive after playback is turned on` | 打开时不读旧消息；新到一条助手消息读一次；收件箱事件与用户消息不读 |
| 同上 | `picks a zh-CN voice and cancels the previous utterance` | `pickChineseVoice` 的优先级；每次 `speak` 先 `cancel` |
| 同上 | `plays remote speech from the tts endpoint and revokes the object URL` | POST 请求体；`Audio.play` 被调用；结束后 `revokeObjectURL` |
| 同上 | `explains a blocked autoplay` | `play()` 抛 `NotAllowedError` 时的错误文案 |
| `features/voice/MicButton.test.tsx` | `inserts the browser transcript without sending` | 最终文本进草稿；没有发出消息请求 |
| 同上 | `records and uploads audio for remote input` | `MediaRecorder` 停止后 POST `/api/agent/voice/stt`，`Content-Type` 去掉参数 |
| 同上 | `explains a denied microphone permission` | `not-allowed` 的文案出现在状态行 |
| 同上 | `suggests alternatives when the browser recognizer cannot reach its service` | `network` 的文案 |
| 同上 | `renders nothing when speech input is unavailable` | `insecure`、`off` 时没有按钮 |
| `features/console/ConsolePage.test.tsx` | `toggles playback per device and remembers it` | 点开关后 `aria-pressed`、localStorage `maa-api-voice-playback=on`；重新渲染仍是开 |
| `features/settings/settings.test.tsx` | `labels voice providers in Chinese and explains unsupported voice features` | 分段控件显示 `关闭`、`浏览器`、`远端服务`；非安全上下文时出现「语音」说明 |

## 7. 验收

```bash
.venv/bin/python -m pytest -q > /tmp/m97-pytest.log 2>&1; echo $?; tail -n 30 /tmp/m97-pytest.log
.venv/bin/python scripts/dump_openapi.py && (cd web && pnpm gen:api) && .venv/bin/python scripts/dump_openapi.py --check; echo $?
cd web && pnpm install --frozen-lockfile > /tmp/m97-pnpm.log 2>&1 && pnpm gen:api:check && pnpm typecheck > /tmp/m97-tc.log 2>&1 && pnpm test --run > /tmp/m97-vitest.log 2>&1 && pnpm build > /tmp/m97-build.log 2>&1; echo $?; cd ..
.venv/bin/python scripts/frontend_smoke.py > /tmp/m97-fsmoke.log 2>&1; echo $?; tail -n 1 /tmp/m97-fsmoke.log
.venv/bin/python scripts/agent_smoke.py > /tmp/m97-asmoke.log 2>&1; echo $?; tail -n 1 /tmp/m97-asmoke.log
.venv/bin/python scripts/orchestrator_smoke.py > /tmp/m97-osmoke.log 2>&1; echo $?; tail -n 1 /tmp/m97-osmoke.log
.venv/bin/python scripts/agent_eval.py --suite regression --mode replay > /tmp/m97-eval-reg.log 2>&1; echo $?
.venv/bin/python scripts/agent_eval.py --suite capability --mode replay > /tmp/m97-eval-cap.log 2>&1; echo $?
.venv/bin/python scripts/generate_api_guide.py --check; echo $?
.venv/bin/python scripts/check_doc_links.py | tail -n 1
git diff --check; echo $?
```

期望：pytest 与 dev 起点相比不新增失败；其余退出码 0；三个冒烟末行 `SMOKE OK`；capability 通过率不低于 M96 基线（新增的 `persona_keeps_confirmation` 计入）；断链 `BROKEN 0`。

补充检查（依赖真实浏览器与麦克风，不作硬性验证，结果与浏览器版本记进台账）：

1. 桌面 Chrome 或 Edge 经 `http://localhost` 打开指挥台：点麦克风说「设备连上了吗」，文字进草稿；打开播报，发送后听到总控的回复。
2. iPhone Safari 经 HTTPS 打开：确认麦克风按钮的出现与否和 §4.5.2 一致；打开播报能听到「已打开播报」。
3. 经 `http://<局域网 IP>` 打开：麦克风按钮不出现，设置分区有 HTTPS 说明。
4. 设成远端并配好档案：播报与识别各成功一次；把档案名改错，界面显示 `播报失败：找不到模型档案…`。

## 8. 风险与回滚

- 人格让总控放松安全口径：人格段只放在共用约定之后、明写「人格只决定语气和称呼」；默认关闭；capability 评测加了开启人格的确认用例。
- 模仿游戏角色涉及版权：模板只描述语气，明确不复述游戏原文、不编造台词。
- 浏览器识别把音频交给浏览器厂商的服务（Chrome 是 Google，Safari 是苹果，Edge 是微软），国内网络下 Chrome 常连不上：设置说明与错误文案给出输入法与远端两条替代；Q2 问用户时同时说明这一点。
- 非安全上下文用不了麦克风：按钮不出现，设置分区说明。
- 远端语音泄露 key：key 只在服务端解密使用，错误 `details` 过 `redact_remote_data`；用例检查 `details` 里没有 key。
- 回滚：`git revert -m 1 <merge>`；没有迁移；新设置键回滚后留在库里不影响启动（`extra="ignore"`）。

## 9. 文档同步

- `docs/v3/14-现行接口清单.md`：agent 小节加 2 行、错误码表加 1 行，各自的计数与总数同步；`docs/v3/15-开放API接入指南.md` 由脚本重新生成。
- `docs/v3/16-部署指南.md`：加一小节「语音」，写 provider 三档、HTTPS 要求、远端档案与三个模型键。
- 端点加 2、错误码加 1、配置键加 9（其中 4 个是 04 §6 没列的新名字）；工具与表数量不变。
- Q1、Q2 的答复记在 13 §8，§9 两行标为已拍板。
- 「需要补进契约的名字」交给主 agent 合并。

## 10. 停下来问

1. 开工前（前置检查 #3 没有记录时）把下面两组问题原样发给用户，等答复：

   Q1 人格设定
   - 用哪个角色？默认「阿米娅」（与品牌徽章一致）；也可以换成别的名字，用平稳的通用语气。
   - 怎么称呼你？默认「博士」。
   - 语气有什么要求？默认「温和、坚定、简洁」。
   - 只在总控上启用？默认是。Worker 不直接对你说话，所以人格只作用于总控的回复。
   - 默认关闭，由你在设置里打开；要不要改成默认打开？

   Q2 语音
   - 播报与输入默认用浏览器自带的，还是远端服务？默认浏览器。说明：Chrome 的网页语音识别依赖 Google 的服务，国内网络下常连不上；Edge、Safari 分别用微软、苹果的服务；输入法自带的语音输入在任何浏览器里都能用。
   - 选远端的话：用哪个 OpenAI 兼容服务（对应哪个模型档案）、播报模型与音色、识别模型？

   答复与默认值的对照：

   | 答复 | 改动 |
   |---|---|
   | 换角色名 | `persona.name` 默认值改成该名字（不在内置表里就用 `generic.md`），同步 04 §6 与模板片段 |
   | 换称呼 | `persona.address` 默认值 |
   | 语气要求 | 只改 `amiya.md` 第一条要点的措辞 |
   | 默认打开人格 | `persona.enabled` 默认 `true`，同步 04 §6；regression 回放的总控录制要重录，停下来再问一次是否接受 |
   | 人格也要作用于 Worker | 与 V3-D10、02 §2.1（Worker 不直接给用户发消息）冲突，停下来，不开工 |
   | 默认用远端 | 两个 provider 默认 `remote`，三个模型键按答复填，同步 04 §6；没配档案前按钮按 `not_configured` 隐藏 |

2. 前置检查 #4 找不到系统提示的段落组装（例如提示词是一整段字符串拼接），插入点没法做到「关闭时逐字节不变」。
3. 前置检查 #6 找不到按名字取档案并解密 key 的函数。
4. 人格关闭时 regression 回放失败，或 capability 通过率低于 M96 基线。
5. 要新增依赖（例如音频转码库）、表或别的错误码才能完成某一步。

## 需要补进契约的名字

| 名字 | 用途 | 应补进 |
|---|---|---|
| 配置键 `persona.address`（str，默认 `博士`）、`voice.tts_model`（str，默认 `tts-1`）、`voice.tts_voice`（str，默认 `alloy`）、`voice.stt_model`（str，默认 `whisper-1`） | 称呼与远端语音的模型 | 04 §6 的 M97 行 |
| `voice.remote_profile` 的取用口径：只取档案的地址与密钥，模型名用上面三个键；为空时远端不可用 | 远端语音 | 04 §6 |
| `maa_api/agent/persona/`（`render_persona_section`、`builtin/amiya.md`、`builtin/generic.md`）、`maa_api/agent/voice/remote.py`（`RemoteVoiceClient`、`build_voice_client`）、`maa_api/api/routers/voice.py` | 模块位置 | 02 §4.1 |
| 人格段在提示词分层里的位置：第 2 段与第 3 段之间，只给总控 | 提示词分层 | 05 §7 |
| 请求与响应模型 `SpeechSynthesisRequest`（`text` 1–1000 字）、`SpeechTranscription`（`text`）；识别端点收原始音频，类型限 `audio/webm`、`audio/ogg`、`audio/mp4`、`audio/mpeg`、`audio/wav`、`audio/x-wav`，上限 10 MiB | 两个端点的形状 | 04 §2.5 的 M97 行 |
| `app.state.voice_http_transport`（可选，测试注入 httpx 传输层） | 测试 | 02 §5 |
| 前端 `features/voice/`（`useVoiceConfig`、`speechInputSupport`、`speechOutputSupport`、`useSpeechOutput`、`useSpeechInput`、`MicButton`、`SpeakToggle`），localStorage 键 `maa-api-voice-playback`，`Composer` 的 `accessory` prop | 前端 | 02 §4.2、11 §9 |
