"""全部参与持久化的状态枚举（docs/04 §4）。

纪律：

- 一律用 :class:`enum.StrEnum`（Python 3.11 起内置），数据库侧存纯字符串列，
  不用 ``sa.Enum`` —— SQLite 无法 ``ALTER TABLE ... DROP CONSTRAINT``，
  加一个枚举值就得 batch 重建整张表（docs/04 §3.3）。
- ``CoreStatus`` / ``DeviceStatus`` 是运行期内存状态，不参与持久化，因此**不在本模块**
  （分别见 docs/03 与 docs/08），只在日志与响应体里以字符串出现。
- 内核指令枚举（``Message`` / ``InstanceOptionKey`` / ``StaticOptionKey``）属于
  ``maa_api/core/enums.py``，不在这里重复。
"""

from enum import IntEnum, StrEnum


class PipelineStatus(StrEnum):
    PENDING = "pending"          # 已入队，等待 PipelineRunner 取用
    RUNNING = "running"          # 正在执行
    COMPLETED = "completed"      # 全部任务成功
    FAILED = "failed"            # 至少一个任务最终失败，或内核崩溃
    CANCELLED = "cancelled"      # 被用户/关机流程终止

    @property
    def is_terminal(self) -> bool:
        return self in (PipelineStatus.COMPLETED, PipelineStatus.FAILED, PipelineStatus.CANCELLED)


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"          # 达到 max_retries 后跳过，流水线继续


class PipelineSource(StrEnum):
    MANUAL = "manual"
    AGENT = "agent"
    SCHEDULED = "scheduled"


class Priority(IntEnum):
    MANUAL = 0                   # 最高
    AGENT = 1
    SCHEDULED = 2


class LogSource(StrEnum):
    MAA_TASK = "maa_task"            # MaaCore 回调翻译成的任务日志
    SERVER = "server"                # Python logger + uvicorn
    MAACORE_DEBUG = "maacore_debug"  # MaaCore 自身的 asst.log


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ScreenshotTrigger(StrEnum):
    TASK_SNAPSHOT = "task_snapshot"  # 任务结束自动留档
    MANUAL = "manual"                # 前端主动截图
    AGENT = "agent"                  # agent 工具调用
    CRASH = "crash"                  # 内核崩溃现场
    UPDATE = "update"                # 更新前后对照


class ScreenshotBackend(StrEnum):
    CORE = "core"                    # AsstAsyncScreencap + AsstGetImage
    ADB = "adb"                      # adbutils 直接截屏


class ConfirmationStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class RiskLevel(StrEnum):
    NONE = "none"
    CONSUME = "consume"              # 碎石、理智药、加急许可、购物、投资
    DESTRUCTIVE = "destructive"      # 清空基建方案、重装游戏/内核、改全局配置


class CallerType(StrEnum):
    REST = "rest"
    INTERNAL = "internal"            # 内置 agent


class AuditStatus(StrEnum):
    PENDING = "pending"              # 等待人工确认中
    SUCCESS = "success"
    FAILED = "failed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class UpdateTarget(StrEnum):
    CORE = "core"
    RESOURCE = "resource"
    GAME = "game"


class UpdateStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"              # 已是最新，未实际执行


class ReleaseChannel(StrEnum):
    STABLE = "stable"
    BETA = "beta"
    ALPHA = "alpha"


class ResourceChannel(StrEnum):
    OTA = "ota"                      # 通道 A：api.maa.plus 的 OTA tasks.json
    REPO = "repo"                    # 通道 B：MaaResource 仓库同步
    ALL = "all"                      # 两条一起更新，默认值


class UpdatePhase(StrEnum):
    CHECKING = "checking"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    WAITING_IDLE = "waiting_idle"
    APPLYING = "applying"
    RESTARTING = "restarting"
    FAILED = "failed"
    DONE = "done"


class NotifyChannelType(StrEnum):
    EMAIL = "email"
    WEBHOOK = "webhook"
    BARK = "bark"
    DINGTALK = "dingtalk"
    WECOM = "wecom"


class NotifyEvent(StrEnum):
    PIPELINE_COMPLETED = "pipeline_completed"
    PIPELINE_FAILED = "pipeline_failed"
    CORE_CRASHED = "core_crashed"
    DEVICE_DISCONNECTED = "device_disconnected"
    UPDATE_AVAILABLE = "update_available"
    UPDATE_FINISHED = "update_finished"
    CONFIRMATION_REQUIRED = "confirmation_required"


class AgentRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class AgentSessionStatus(StrEnum):
    ACTIVE = "active"
    FINISHED = "finished"
    ERROR = "error"


class ResourceAssetKind(StrEnum):
    COPILOT = "copilot"              # Copilot 作业 JSON
    INFRAST_PLAN = "infrast_plan"    # 自定义基建方案
    CUSTOM_TASK = "custom_task"      # 增量注入的 tasks.json task 定义
    OTA_RESOURCE = "ota_resource"    # 通道 A 下发的文件，每个清单条目一行
    REPO_RESOURCE = "repo_resource"  # 通道 B 的仓库同步，整体一行
