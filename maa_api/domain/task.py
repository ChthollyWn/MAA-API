"""MaaCore 任务与 Copilot 任务的 pydantic v2 模型、判别联合及参数元信息。

字段名、类型与说明迁移自旧 ``maa_api/model/core/task.py`` 的任务类 docstring；与
docs/05 §7.5／§7.6 冲突或过时之处以 docs/05 为准（``filename`` 取代旧的 ``failename``，
``Recruit.recruitment_time`` 是 ``dict[str, int]``，JSON 对象键只能是字符串）。

有内核默认值、能用 JSON 字面量表达、任何上下文下都能显式下发的字段写标准 JSON Schema
``default``：前端据此预填，``to_core_params()`` 的 ``exclude_none=True`` 让默认值随下发
快照显式下发（``raw_params`` 用 ``exclude_unset=True``，只记客户端显式提交的字段）。
可变默认值写字面量（``default=[...]`` / ``default={...}``）：pydantic v2 的
``default_factory`` 不进 JSON Schema，而字面量由 pydantic 按实例深拷贝。保持
``default=None`` 的是语义为「无限」的字段、语义为「空 = 不启用」的字段、随主题／模式变的
条件默认值，以及校验器禁止「设了但不生效」的 ``Recruit.expedite_times``；渠道字段
``client_type`` / ``server`` 同样为 ``None``，由服务层按 ``model_fields_set`` 注入全局
渠道默认值（docs/05 §9）。

主题／模式相关的跨字段规则实现为 ``@model_validator(mode="after")`` 并抛 ``AppError``：
字段类型、``ge``/``le``、``Literal`` 能拦住的错误留给 pydantic 原生 ``ValidationError``
（422 ``VALIDATION_ERROR``），跨字段语义才抛 ``TASK_PARAM_INVALID``（422）或
``TASK_PARAM_DEPRECATED``（400，``Roguelike.mode=2``）。领域层不读数据库、不做 IO。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from maa_api.domain.enums import RiskLevel
from maa_api.domain.errors import AppError, ErrorCode

__all__ = [
    "CLIENT_TYPES",
    "ChannelDefaults",
    "CloseDownInput",
    "CopilotInput",
    "DepotInput",
    "FightInput",
    "InfrastInput",
    "MaaField",
    "MallInput",
    "NormalizedTask",
    "OperBoxInput",
    "PipelineCreate",
    "RECLAMATION_THEMES",
    "RISK_CONSUME",
    "RISK_FIELDS",
    "ROGUELIKE_BLACKFLOW_STRATEGIES",
    "ROGUELIKE_COMMON_MODES",
    "ROGUELIKE_CULTIVATION_TARGETS",
    "ROGUELIKE_MODES",
    "ROGUELIKE_THEMES",
    "ROGUELIKE_THEME_MODES",
    "RUNTIME_IMMUTABLE",
    "RecruitInput",
    "ReclamationInput",
    "RoguelikeCollectibleStartList",
    "RoguelikeCoreChar",
    "RoguelikeInput",
    "SERVERS",
    "TASK_LABELS",
    "TASK_MODELS",
    "TaskInput",
    "TaskInputBase",
    "normalize",
]


#: ``x-risk`` 的取值。与 :class:`~maa_api.domain.enums.RiskLevel` 的 ``CONSUME`` 对齐，
#: agent 侧 ``PolicyEngine`` 直接 import :data:`RISK_FIELDS`，避免前后端各维护一份清单。
RISK_CONSUME: str = RiskLevel.CONSUME.value  # "consume"

#: 客户端版本枚举（6 值），来自 ``task.py`` 各任务 docstring 与 docs/05 §7.5。
CLIENT_TYPES = Literal["Official", "Bilibili", "txwy", "YoStarEN", "YoStarJP", "YoStarKR"]

#: 服务器枚举（4 值），影响掉落识别与数据上传。
SERVERS = Literal["CN", "US", "JP", "KR"]

#: 肉鸽主题枚举（6 值），来自 ``RoguelikeTask`` docstring 与上游集成文档 v6.18.0。
ROGUELIKE_THEMES = Literal["Phantom", "Mizuki", "Sami", "Sarkaz", "JieGarden", "BlackFlow"]

#: 生息演算主题枚举（2 值）。
RECLAMATION_THEMES = Literal["Fire", "Tales"]

#: 基建换班设施枚举（9 值，顺序即换班顺序）。上游集成文档 v6.18.0 的 `facility` 还有
#: `AssistantChange`（换助战），它不是房间、不参与换班顺序，本服务不支持。
INFRAST_FACILITIES = Literal[
    "Mfg", "Trade", "Power", "Control", "Reception", "Office", "Dorm", "Processing", "Training"
]

#: 设施枚举的中文标签。挂在 ``facility`` 的**元素**注解上走 ``x-enum-labels``：
#: 前端 ``arrayItemOptions()`` 读的是 items 的标签，字段级标签对数组不生效。
INFRAST_FACILITY_LABELS: dict[str, str] = {
    "Mfg": "制造站",
    "Trade": "贸易站",
    "Power": "发电站",
    "Control": "控制中枢",
    "Reception": "会客室",
    "Office": "办公室",
    "Dorm": "宿舍",
    "Processing": "加工站",
    "Training": "训练室",
}

#: 无人机用途枚举（7 值）。
INFRAST_DRONES = Literal[
    "_NotUse", "Money", "SyntheticJade", "CombatRecord", "PureGold", "OriginStone", "Chip"
]


def MaaField(
    *,
    label: str,
    group: str = "基础",
    widget: str | None = None,
    enum_labels: Mapping[str, str] | None = None,
    risk: str | None = None,
    depends_on: Mapping[str, Any] | None = None,
    x_extra: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """在标准 :func:`pydantic.Field` 之上附加前端渲染所需的 ``x-*`` 中文元信息。

    ``x-label`` / ``x-group`` 恒定输出；``x-widget`` / ``x-enum-labels`` / ``x-risk`` /
    ``x-depends-on`` 仅在给出时输出。``x_extra`` 是逃生口：需要挂新的 ``x-`` 关键字
    时从这里传入，避免与 :func:`Field` 的 ``json_schema_extra`` 形参撞车
    （见 ``tests/fixtures/api_probe_findings.md`` §2.7）。
    """
    extra: dict[str, Any] = {"x-label": label, "x-group": group}
    if widget:
        extra["x-widget"] = widget
    if enum_labels:
        extra["x-enum-labels"] = {str(key): value for key, value in enum_labels.items()}
    if risk:
        extra["x-risk"] = risk
    if depends_on:
        extra["x-depends-on"] = dict(depends_on)
    if x_extra:
        extra.update(x_extra)
    return Field(**kwargs, json_schema_extra=extra)


def _task_config(*examples: dict[str, Any]) -> ConfigDict:
    """子类模型配置：继承基类的收紧项，并挂上 docs/05 §11.3 要求的可执行示例。

    示例必须能被模型自身校验通过（测试逐个 ``model_validate`` 钉住）：从
    ``/api/tasks/types`` 拿到 schema 的 agent，一个完整示例比二十条字段描述更能
    让它正确构造第一次调用。
    """
    return ConfigDict(
        extra="forbid",
        populate_by_name=True,
        validate_assignment=True,
        json_schema_extra={"examples": list(examples)},
    )


class TaskInputBase(BaseModel):
    """任务输入模型的公共基类。

    ``extra="forbid"`` 是这次重构最重要的一处收紧：参数写错（拼错、写错任务类型）
    时不再静默丢弃，而是立刻可见的 422 ``extra_forbidden``。``populate_by_name=True``
    允许用 Python 字段名或 MAA 原名提交（别名见各字段的 ``validation_alias``），
    ``validate_assignment=True`` 让运行中改参数（M5）也走同一套校验。
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        validate_assignment=True,
    )

    enable: bool | None = MaaField(
        default=True,
        label="启用本任务",
        description="关掉后执行流水线时会跳过本任务（默认开启）",
    )

    def to_core_params(self) -> dict[str, Any]:
        """导出为投递给 ``AsstAppendTask`` 的参数字典。

        ``exclude_none=True`` 保留现有 ``Task.__init__`` 里「None 不下发、由内核用自己
        的默认值」的行为，只是位置从构造环节挪到导出环节；``by_alias=True`` 保证
        ``DrGrandet`` 等非 snake_case 字段用 MAA 原名下发。
        """
        return self.model_dump(
            exclude={"name"},
            exclude_none=True,
            by_alias=True,
        )


# --------------------------------------------------------------------------- #
# 11 种任务类型
# --------------------------------------------------------------------------- #


class StartUpInput(TaskInputBase):
    """开始唤醒：启动客户端并进入游戏。"""

    model_config = _task_config(
        {"name": "StartUp", "client_type": "Official", "start_game_enabled": True}
    )

    name: Literal["StartUp"] = MaaField(
        default="StartUp",
        label="任务类型",
        description="由任务类型选择器决定，不用填",
    )
    client_type: CLIENT_TYPES | None = MaaField(
        default=None,
        label="客户端版本",
        group="账号",
        widget="select",
        enum_labels={
            "Official": "官服",
            "Bilibili": "B 服",
            "txwy": "腾讯/繁中服",
            "YoStarEN": "国际服（EN）",
            "YoStarJP": "日服",
            "YoStarKR": "韩服",
        },
        description="游戏客户端渠道；不填时用设置里选的默认渠道",
    )
    start_game_enabled: bool | None = MaaField(
        default=False,
        label="自动启动客户端",
        group="账号",
        description="启动任务前自动打开游戏客户端（默认关闭）",
    )
    account_name: str | None = MaaField(
        default=None,
        label="切换账号",
        group="账号",
        description="切换到已登录的账号，输入登录名的一部分即可；不填则不切换",
        examples=["123****4567", "4567", "张三"],
    )


class CloseDownInput(TaskInputBase):
    """关闭游戏：任务结束后关闭客户端。"""

    model_config = _task_config({"name": "CloseDown", "client_type": "Bilibili"})

    name: Literal["CloseDown"] = MaaField(
        default="CloseDown",
        label="任务类型",
        description="由任务类型选择器决定，不用填",
    )
    client_type: CLIENT_TYPES | None = MaaField(
        default=None,
        label="客户端版本",
        group="账号",
        widget="select",
        enum_labels={
            "Official": "官服",
            "Bilibili": "B 服",
            "txwy": "腾讯/繁中服",
            "YoStarEN": "国际服（EN）",
            "YoStarJP": "日服",
            "YoStarKR": "韩服",
        },
        description="要关闭的客户端渠道；不填时用设置里选的默认渠道",
    )


class FightInput(TaskInputBase):
    """刷理智：刷指定关卡，支持理智药、碎石与连战。"""

    model_config = _task_config(
        {
            "name": "Fight",
            "stage": "1-7",
            "series": 0,
            "medicine": 2,
            "stone": 1,
            "DrGrandet": True,
        }
    )

    name: Literal["Fight"] = MaaField(
        default="Fight",
        label="任务类型",
        description="由任务类型选择器决定，不用填",
    )
    stage: str | None = MaaField(
        default=None,
        label="关卡名",
        description="剿灭作战填 Annihilation，留空识别当前或上次关卡（不支持运行中设置）",
        examples=["1-7", "S3-2", "Annihilation", "CE-6Hard"],
    )
    times: int | None = MaaField(
        default=None,
        label="指定次数",
        ge=0,
        description="刷多少次后停止；不填则不限制次数",
    )
    series: int | None = MaaField(
        default=1,
        label="连战次数",
        ge=-1,
        le=6,
        widget="select",
        enum_labels={
            "-1": "禁用切换",
            "0": "自动选择最大可用次数",
            "1": "1 次",
            "2": "2 次",
            "3": "3 次",
            "4": "4 次",
            "5": "5 次",
            "6": "6 次",
        },
        description="同一关卡连续作战的次数（默认 1）",
    )
    medicine: int | None = MaaField(
        default=0,
        label="最大理智药数",
        group="资源消耗",
        ge=0,
        risk=RISK_CONSUME,
        description="最多吃几瓶理智药（默认 0，即不吃）",
    )
    expiring_medicine: int | None = MaaField(
        default=0,
        label="最大 48 小时内过期理智药数",
        group="资源消耗",
        ge=0,
        risk=RISK_CONSUME,
        description="最多吃几瓶 48 小时内过期的理智药；非 0 时内核会把这些药一起用掉",
    )
    stone: int | None = MaaField(
        default=0,
        label="最大碎石数",
        group="资源消耗",
        ge=0,
        risk=RISK_CONSUME,
        description="最多碎几颗源石换理智（默认 0，即不碎）",
    )
    dr_grandet: bool | None = MaaField(
        default=False,
        label="节省理智碎石模式",
        group="资源消耗",
        description="在碎石确认界面等到理智恢复 1 点再碎，省一颗源石（默认关闭）",
        validation_alias=AliasChoices("DrGrandet", "dr_grandet"),
        serialization_alias="DrGrandet",
    )
    server: SERVERS | None = MaaField(
        default=None,
        label="服务器",
        group="账号",
        widget="select",
        enum_labels={"CN": "国服", "US": "美服", "JP": "日服", "KR": "韩服"},
        description="游戏服务器，影响掉落识别与上传；不填时用设置里选的默认服务器",
    )
    client_type: CLIENT_TYPES | None = MaaField(
        default=None,
        label="客户端版本",
        group="账号",
        widget="select",
        enum_labels={
            "Official": "官服",
            "Bilibili": "B 服",
            "txwy": "腾讯/繁中服",
            "YoStarEN": "国际服（EN）",
            "YoStarJP": "日服",
            "YoStarKR": "韩服",
        },
        description="客户端渠道；崩溃后按它重启游戏继续刷，明确留空则不启用",
    )
    report_to_penguin: bool | None = MaaField(
        default=False,
        label="汇报企鹅数据",
        group="数据上报",
        description="把作战结果上传到企鹅物流（默认关闭）",
    )
    penguin_id: str | None = MaaField(
        default=None,
        label="企鹅数据 ID",
        group="数据上报",
        depends_on={"report_to_penguin": True},
        description="企鹅物流的汇报 id，配合「汇报企鹅数据」使用",
    )
    drops: dict[str, int] | None = MaaField(
        default={},
        label="指定掉落数量",
        group="高级",
        widget="item-count-map",
        description="指定掉落目标与数量，任一达标就停止任务（默认不指定）",
    )


class RecruitInput(TaskInputBase):
    """自动公招：按 Tag 等级自动选择与确认，支持加急与数据上报。"""

    model_config = _task_config(
        {
            "name": "Recruit",
            "select": [4, 5],
            "confirm": [3, 4, 5],
            "times": 4,
            "expedite": True,
            "expedite_times": 1,
        }
    )

    name: Literal["Recruit"] = MaaField(
        default="Recruit",
        label="任务类型",
        description="由任务类型选择器决定，不用填",
    )
    refresh: bool | None = MaaField(
        default=False,
        label="刷新三星 Tags",
        description="连三星标签也一起刷新（默认关闭）",
    )
    select: list[Annotated[int, Field(ge=1, le=6)]] | None = MaaField(
        default=[1, 4, 5],
        label="点击的 Tag 等级",
        widget="tags",
        description="要点击选中的 Tag 星级（默认 1、4、5 星）",
    )
    confirm: list[Annotated[int, Field(ge=1, le=6)]] | None = MaaField(
        default=[2, 3, 4, 5],
        label="确认的 Tag 等级",
        widget="tags",
        description="要点击确认的 Tag 星级（默认 2、3、4、5 星）",
    )
    first_tags: list[str] | None = MaaField(
        default=[],
        label="首选 Tags",
        group="公招标签",
        widget="tags",
        description="无论其他设置如何都会优先进这些 Tag（仅 3 星）",
    )
    extra_tags_mode: Literal[0, 1, 2] | None = MaaField(
        default=0,
        label="选择更多的 Tags",
        widget="select",
        enum_labels={
            "0": "默认行为",
            "1": "选 3 个 Tags（即使可能冲突）",
            "2": "尽可能同时选择更多高星 Tag 组合（即使可能冲突）",
        },
        description="一轮里选择更多 Tag 的策略（默认 0）",
    )
    times: int | None = MaaField(
        default=0,
        label="招募次数",
        ge=0,
        description="招募多少次；默认 0，只进公招页不招募",
    )
    set_time: bool | None = MaaField(
        default=True,
        label="设置招募时限",
        depends_on={"times": 0},
        description="按「招募时限映射」把游戏内招募计时器点到位（默认开启）",
    )
    expedite: bool | None = MaaField(
        default=False,
        label="使用加急许可",
        group="加急与跳过",
        risk=RISK_CONSUME,
        description="用加急许可立刻完成招募（默认关闭）",
    )
    expedite_times: int | None = MaaField(
        default=None,
        label="加急次数",
        group="加急与跳过",
        ge=0,
        depends_on={"expedite": True},
        description="当前内核不再读取此值，加急次数由「招募次数」决定",
    )
    skip_robot: bool | None = MaaField(
        default=False,
        label="跳过小车词条",
        group="加急与跳过",
        description="识别到小车词条时跳过（默认关闭）",
    )
    recruitment_time: dict[str, int] | None = MaaField(
        default={"3": 540, "4": 540, "5": 540, "6": 540},
        label="招募时限映射",
        group="公招标签",
        widget="duration-map",
        description="各 Tag 星级对应的希望招募时限（默认 9 小时）",
    )
    report_to_penguin: bool | None = MaaField(
        default=False,
        label="汇报企鹅数据",
        group="数据上报",
        description="把公招结果上传到企鹅物流（默认关闭）",
    )
    penguin_id: str | None = MaaField(
        default=None,
        label="企鹅数据 ID",
        group="数据上报",
        depends_on={"report_to_penguin": True},
        description="企鹅物流的汇报 id，配合「汇报企鹅数据」使用",
    )
    report_to_yituliu: bool | None = MaaField(
        default=False,
        label="汇报一图流数据",
        group="数据上报",
        description="把公招结果上传到一图流（默认关闭）",
    )
    yituliu_id: str | None = MaaField(
        default=None,
        label="一图流 ID",
        group="数据上报",
        depends_on={"report_to_yituliu": True},
        description="一图流的汇报 id，配合「汇报一图流数据」使用",
    )
    server: SERVERS | None = MaaField(
        default=None,
        label="服务器",
        group="账号",
        widget="select",
        enum_labels={"CN": "国服", "US": "美服", "JP": "日服", "KR": "韩服"},
        description="游戏服务器，影响数据上传；不填时用设置里选的默认服务器",
    )

    @model_validator(mode="after")
    def _check_expedite_times(self) -> "RecruitInput":
        """``expedite_times`` 只在 ``expedite=true`` 时有意义，不允许「设了但不生效」。"""
        if self.expedite_times is not None and self.expedite is not True:
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "expedite_times 仅在 expedite=true 时有效；请同时设置 expedite=true，"
                "或去掉 expedite_times",
                {"field": "expedite_times", "depends_on": {"expedite": True}},
            )
        return self


class InfrastInput(TaskInputBase):
    """基建换班：默认换班、自定义换班与一键轮换。"""

    model_config = _task_config(
        {
            "name": "Infrast",
            "mode": 0,
            "facility": ["Mfg", "Trade", "Power", "Control", "Reception", "Office", "Dorm"],
            "drones": "Money",
            "threshold": 0.3,
        },
        {
            "name": "Infrast",
            "mode": 10000,
            "facility": ["Mfg", "Trade"],
            "filename": "plans/custom.json",
            "plan_index": 0,
        },
    )

    name: Literal["Infrast"] = MaaField(
        default="Infrast",
        label="任务类型",
        description="由任务类型选择器决定，不用填",
    )
    mode: Literal[0, 10000, 20000] | None = MaaField(
        default=0,
        label="换班工作模式",
        widget="select",
        enum_labels={
            "0": "默认换班（单设施最优解）",
            "10000": "自定义换班（读取用户配置）",
            "20000": "一键轮换",
        },
        description="换班工作模式；一键轮换会跳过部分设施（默认换班）",
    )
    facility: list[
        Annotated[
            INFRAST_FACILITIES,
            Field(json_schema_extra={"x-enum-labels": INFRAST_FACILITY_LABELS}),
        ]
    ] | None = MaaField(
        default=["Mfg", "Trade", "Power", "Control", "Reception", "Office", "Dorm"],
        label="要换班的设施（有序）",
        widget="ordered-list",
        description="要换班的设施，顺序即换班顺序；默认不含加工站与训练室（不支持运行中设置）",
    )
    drones: INFRAST_DRONES | None = MaaField(
        default="_NotUse",
        label="无人机用途",
        widget="select",
        enum_labels={
            "_NotUse": "不使用",
            "Money": "龙门币",
            "SyntheticJade": "合成玉",
            "CombatRecord": "作战记录",
            "PureGold": "赤金",
            "OriginStone": "源石碎片",
            "Chip": "芯片",
        },
        description="无人机用在哪里；自定义换班模式下不生效（默认不使用）",
    )
    threshold: float | None = MaaField(
        default=0.3,
        label="工作心情阈值",
        ge=0.0,
        le=1.0,
        description="干员心情低于这个比例就换班（默认 0.3）",
    )
    replenish: bool | None = MaaField(
        default=False,
        label="源石碎片自动补货",
        description="贸易站「源石碎片」卖完后自动补货（默认关闭）",
    )
    dorm_notstationed_enabled: bool | None = MaaField(
        default=False,
        label='启用宿舍「未进驻」选项',
        description="宿舍填充时勾选「未进驻」筛选（默认关闭）",
    )
    dorm_trust_enabled: bool | None = MaaField(
        default=False,
        label="剩余位置填入信赖未满干员",
        description="宿舍剩余位置填信赖未满的干员（默认关闭）",
    )
    filename: str | None = MaaField(
        default=None,
        label="自定义配置路径",
        group="自定义换班",
        description="自定义换班用的配置文件路径（不支持运行中设置）",
    )
    plan_index: int | None = MaaField(
        default=0,
        label="方案序号",
        group="自定义换班",
        ge=0,
        description="用配置文件里的第几个方案，默认第一个（不支持运行中设置）",
    )

    @model_validator(mode="after")
    def _check_custom_mode(self) -> "InfrastInput":
        """自定义换班模式必须给出 ``filename`` 与 ``plan_index``。"""
        if self.mode == 10000 and (self.filename is None or self.plan_index is None):
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "自定义换班模式（mode=10000）必须同时提供 filename 与 plan_index",
                {"field": "mode", "required": ["filename", "plan_index"]},
            )
        return self


class MallInput(TaskInputBase):
    """获取信用及商店购物。"""

    model_config = _task_config(
        {
            "name": "Mall",
            "shopping": True,
            "buy_first": ["招聘许可"],
            "blacklist": ["加急许可", "家具零件"],
        }
    )

    name: Literal["Mall"] = MaaField(
        default="Mall",
        label="任务类型",
        description="由任务类型选择器决定，不用填",
    )
    shopping: bool | None = MaaField(
        default=True,
        label="是否购物",
        risk=RISK_CONSUME,
        description="用信用点买东西，默认开启（不支持运行中设置）",
    )
    buy_first: list[str] | None = MaaField(
        default=[],
        label="优先购买列表",
        group="购买清单",
        widget="tags",
        description="优先购买的商品名（不支持运行中设置）",
    )
    blacklist: list[str] | None = MaaField(
        default=[],
        label="黑名单列表",
        group="购买清单",
        widget="tags",
        description="不买的商品名（不支持运行中设置）",
    )
    force_shopping_if_credit_full: bool | None = MaaField(
        default=False,
        label="信用溢出时无视黑名单",
        description="信用点溢出时也买黑名单里的商品（默认关闭）",
    )
    only_buy_discount: bool | None = MaaField(
        default=False,
        label="只购买折扣物品",
        description="只买打折商品，第二轮购买生效（默认关闭）",
    )
    reserve_max_credit: bool | None = MaaField(
        default=False,
        label="信用点低于 300 时停止购买",
        description="信用点低于 300 就停止购买，第二轮生效（默认关闭）",
    )


class AwardInput(TaskInputBase):
    """领取各种奖励。"""

    # ``award`` 与 ``mail``/``recruit``/``orundum``/``mining``/``specialaccess``
    # 都写 schema ``default``（内核默认值：``award=true``，其余 ``false``）。旧
    # ``TaskRequest`` 曾把它们都写成 True 与 docstring 冲突，现在以内核源码的实际
    # 默认值为准，逐字段依据见
    # docs/superpowers/specs/2026-09-28-m17-task-param-defaults.md §4。

    model_config = _task_config({"name": "Award", "award": True, "mail": True})

    name: Literal["Award"] = MaaField(
        default="Award",
        label="任务类型",
        description="由任务类型选择器决定，不用填",
    )
    award: bool | None = MaaField(
        default=True,
        label="领取每日/每周任务奖励",
        description="领取每日与每周任务奖励（默认开启）",
    )
    mail: bool | None = MaaField(
        default=False,
        label="领取邮件奖励",
        group="奖励项",
        description="领取所有邮件奖励（默认关闭）",
    )
    recruit: bool | None = MaaField(
        default=False,
        label="领取每日免费单抽",
        group="奖励项",
        description="领取限定池的每日免费单抽（默认关闭）",
    )
    orundum: bool | None = MaaField(
        default=False,
        label="领取幸运墙合成玉",
        group="奖励项",
        description="领取幸运墙的合成玉（默认关闭）",
    )
    mining: bool | None = MaaField(
        default=False,
        label="领取限时开采许可奖励",
        group="奖励项",
        description="领取限时开采许可的合成玉（默认关闭）",
    )
    specialaccess: bool | None = MaaField(
        default=False,
        label="领取五周年月卡奖励",
        group="奖励项",
        description="领取五周年赠送的月卡奖励（默认关闭）",
    )


#: 肉鸽模式枚举（11 值，含两个「能提交但会被校验器拒绝」的值）。
#:
#: 上游 ``RoguelikeConfig.h`` 的 ``is_valid_mode``：通用模式是 0/1/4/6/7；
#: 5 仅 Sami、10001 仅 Sarkaz、20001 仅 JieGarden、30001 仅 BlackFlow；
#: BlackFlow 只接受 0/1/30001。2 已被上游移除、3 尚未开放，留在这里只为给出
#: 「已弃用」与「开发中」两种具体错误（否则会落成 pydantic 的 literal_error）。
ROGUELIKE_MODES = Literal[0, 1, 2, 3, 4, 5, 6, 7, 10001, 20001, 30001]

#: 各主题允许的模式（``RoguelikeConfig.h::is_valid_mode``）。
ROGUELIKE_COMMON_MODES: frozenset[int] = frozenset({0, 1, 4, 6, 7})
ROGUELIKE_THEME_MODES: dict[str, frozenset[int]] = {
    "Phantom": ROGUELIKE_COMMON_MODES,
    "Mizuki": ROGUELIKE_COMMON_MODES,
    "Sami": ROGUELIKE_COMMON_MODES | {5},
    "Sarkaz": ROGUELIKE_COMMON_MODES | {10001},
    "JieGarden": ROGUELIKE_COMMON_MODES | {20001},
    "BlackFlow": frozenset({0, 1, 30001}),
}

#: 黑流树海策略（上游集成文档 v6.18.0；留空时内核按 ``mode`` 与 ``investment_enabled`` 推断）。
ROGUELIKE_BLACKFLOW_STRATEGIES = Literal[
    "baby_animal", "investment", "burn_with_investment", "burn"
]

#: 刷襁褓动物的目标（``blackflow_strategy=baby_animal``）。
ROGUELIKE_CULTIVATION_TARGETS = Literal[
    "swaddled_cat", "swaddled_feathered_serpent", "swaddled_dog", "swaddled_cerberus"
]


class RoguelikeCoreChar(BaseModel):
    """``core_char_list`` 的一项：开局第 N 次招募指定的一名干员。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = MaaField(
        max_length=32,
        label="干员中文名",
        description="干员中文名，无论区服",
    )
    use_support: bool = MaaField(
        default=False,
        label="借助战",
        description="这一顺位使用助战干员",
    )


class RoguelikeCollectibleStartList(BaseModel):
    """``collectible_mode_start_list``：凹开局时期望的开局奖励，默认全部关闭。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    hot_water: bool = MaaField(default=False, label="热水壶", description="热水壶奖励（通用）")
    shield: bool = MaaField(default=False, label="护盾", description="护盾奖励，约等于额外生命（通用）")
    ingot: bool = MaaField(default=False, label="源石锭", description="源石锭奖励（通用）")
    hope: bool = MaaField(default=False, label="希望", description="希望奖励（通用；界园主题没有）")
    random: bool = MaaField(default=False, label="随机收藏品", description="用全部源石锭换随机收藏品（通用）")
    key: bool = MaaField(default=False, label="钥匙", description="钥匙奖励（仅水月主题）")
    dice: bool = MaaField(default=False, label="骰子", description="骰子奖励（仅水月主题）")
    ideas: bool = MaaField(default=False, label="构想", description="2 构想奖励（仅萨卡兹主题）")
    ticket: bool = MaaField(default=False, label="票券", description="票券奖励（仅界园主题）")


class RoguelikeInput(TaskInputBase):
    """无限刷肉鸽（集成战略）。

    主题／模式相关的跨字段规则全部实现为 ``model_validator`` 并抛 ``AppError``
    （``TASK_PARAM_INVALID``；``mode=2`` 是已弃用值，抛 ``TASK_PARAM_DEPRECATED``）
    ——「设了但不生效」必须在提交时就告诉用户，而不是静默忽略。矩阵对齐上游
    ``RoguelikeConfig.cpp``：模式按主题收口，``start_with_elite_two`` 只许 ``mode=4``，
    ``difficulty`` 在 Phantom 下也允许（``-1`` 表示不指定）。
    """

    model_config = _task_config(
        {
            "name": "Roguelike",
            "theme": "Sami",
            "mode": 1,
            "investment_enabled": True,
            "investments_count": 10,
        },
        {
            "name": "Roguelike",
            "theme": "Sami",
            "mode": 5,
            "check_collapsal_paradigms": True,
            "expected_collapsal_paradigms": ["目空一些", "睁眼瞎"],
        },
        {
            "name": "Roguelike",
            "theme": "Sarkaz",
            "mode": 4,
            "core_char_list": [
                {"name": "维什戴尔", "use_support": True},
                {"name": "古米", "use_support": False},
            ],
            "start_with_elite_two": True,
            "collectible_mode_start_list": {"hot_water": True, "shield": True},
        },
    )

    name: Literal["Roguelike"] = MaaField(
        default="Roguelike",
        label="任务类型",
        description="由任务类型选择器决定，不用填",
    )
    theme: ROGUELIKE_THEMES | None = MaaField(
        default="Phantom",
        label="主题",
        widget="select",
        enum_labels={
            "Phantom": "傀影与猩红孤钻",
            "Mizuki": "水月与深蓝之树",
            "Sami": "探索者的银凇止境",
            "Sarkaz": "萨卡兹的无终奇语",
            "JieGarden": "岁的界园志异",
            "BlackFlow": "黑流树海",
        },
        description="要刷的肉鸽主题（默认傀影与猩红孤钻）",
    )
    mode: ROGUELIKE_MODES | None = MaaField(
        default=0,
        label="模式",
        widget="select",
        enum_labels={
            "0": "刷分/奖励点数",
            "1": "刷源石锭",
            "2": "已弃用（无法提交）",
            "3": "开发中（无法提交）",
            "4": "凹开局",
            "5": "刷坍缩范式（仅 Sami）",
            "6": "刷月度小队",
            "7": "刷深入调查",
            "10001": "快速通过第一层（仅 Sarkaz）",
            "20001": "刷常乐节点（仅 JieGarden）",
            "30001": "刷襁褓动物（仅 BlackFlow）",
        },
        description="肉鸽的玩法模式（默认刷分/奖励点数）",
    )
    squad: str | None = MaaField(
        default=None,
        label="开局分队名",
        description="开局用哪个分队；不填则由内核自动选择",
    )
    roles: str | None = MaaField(
        default=None,
        label="开局职业组",
        description="开局职业组；不填则由内核自动选择",
    )
    core_char: str | None = MaaField(
        default=None,
        label="开局干员名",
        group="开局",
        description="开局要借的干员中文名；不填由内核按练度选择",
    )
    core_char_list: list[RoguelikeCoreChar] | None = MaaField(
        default=None,
        label="开局干员顺位",
        group="开局",
        description="按顺位指定开局干员，最多三个，职业不能相同",
    )
    use_support: bool | None = MaaField(
        default=False,
        label="开局干员为助战干员",
        group="开局",
        description="开局干员换成助战干员（默认关闭）",
    )
    use_nonfriend_support: bool | None = MaaField(
        default=False,
        label="允许非好友助战",
        group="开局",
        depends_on={"use_support": True},
        description="允许借非好友的助战干员（默认关闭）",
    )
    starts_count: int | None = MaaField(
        default=None,
        label="开始探索次数",
        group="开局",
        description="开始探索多少次后停止；不填则不限制",
    )
    difficulty: int | None = MaaField(
        default=None,
        label="指定难度等级",
        group="开局",
        ge=-1,
        description="指定难度等级；-1 表示不指定（默认不指定）",
    )
    start_with_elite_two: bool | None = MaaField(
        default=False,
        label="凹干员精二直升",
        group="开局",
        depends_on={"mode": 4},
        description="凹开局时顺便凹精二直升（仅凹开局模式）",
    )
    only_start_with_elite_two: bool | None = MaaField(
        default=False,
        label="只凹精二直升",
        group="开局",
        depends_on={"mode": 4, "start_with_elite_two": True},
        description="只凹精二直升，忽略其他开局条件（默认关闭）",
    )
    first_floor_foldartal: str | None = MaaField(
        default=None,
        label="第一层远见密文板",
        group="开局",
        depends_on={"theme": "Sami"},
        description="第一层远见想要的密文板；凹到就停止任务",
    )
    start_foldartal_list: list[str] | None = MaaField(
        default=None,
        label="开局奖励密文板列表",
        group="开局",
        widget="tags",
        depends_on={"theme": "Sami", "mode": 4},
        description="开局奖励想要的密文板；需搭配「生活至上分队」",
    )
    collectible_mode_shopping: bool | None = MaaField(
        default=False,
        label="凹开局启用购物",
        group="凹开局",
        depends_on={"mode": 4},
        description="凹开局时也购物（默认关闭）",
    )
    collectible_mode_squad: str | None = MaaField(
        default=None,
        label="凹开局分队",
        group="凹开局",
        depends_on={"mode": 4},
        description="凹开局使用的分队；不填与开局分队相同",
    )
    collectible_mode_start_list: RoguelikeCollectibleStartList | None = MaaField(
        default=None,
        label="凹开局期望的奖励",
        group="凹开局",
        depends_on={"mode": 4},
        description="凹开局期望的奖励，默认全部关闭",
    )
    use_foldartal: bool | None = MaaField(
        default=None,
        label="使用密文板",
        group="主题专属",
        depends_on={"theme": "Sami"},
        description="是否使用密文板；不填由内核按模式决定",
    )
    stop_at_final_boss: bool | None = MaaField(
        default=False,
        label="第 5 层险路恶敌前停止",
        group="主题专属",
        description="第 5 层险路恶敌前停止；不适用于 Phantom",
    )
    stop_at_max_level: bool | None = MaaField(
        default=False,
        label="等级刷满后停止",
        group="主题专属",
        description="肉鸽等级刷满后停止任务（默认关闭）",
    )
    refresh_trader_with_dice: bool | None = MaaField(
        default=False,
        label="用骰子刷新商店",
        group="主题专属",
        depends_on={"theme": "Mizuki"},
        description="用骰子刷新商店，用于刷指路鳞（默认关闭）",
    )
    start_with_seed: str | None = MaaField(
        default=None,
        label="固定种子",
        group="主题专属",
        description="用固定种子刷钱；留空则不启用",
    )
    blackflow_strategy: ROGUELIKE_BLACKFLOW_STRATEGIES | None = MaaField(
        default=None,
        label="黑流树海策略",
        group="主题专属",
        widget="select",
        depends_on={"theme": "BlackFlow"},
        enum_labels={
            "baby_animal": "培育襁褓动物",
            "investment": "第一层投资",
            "burn_with_investment": "投资后速通第三层",
            "burn": "速通第三层",
        },
        description="黑流树海的策略；不填按模式与投资推断",
    )
    blackflow_cultivation_target: ROGUELIKE_CULTIVATION_TARGETS | None = MaaField(
        default=None,
        label="襁褓动物目标",
        group="主题专属",
        widget="select",
        depends_on={"blackflow_strategy": "baby_animal"},
        enum_labels={
            "swaddled_cat": "襁褓猫",
            "swaddled_feathered_serpent": "襁褓羽蛇",
            "swaddled_dog": "襁褓狗",
            "swaddled_cerberus": "襁褓三头犬",
        },
        description="刷襁褓动物的目标（默认襁褓猫）",
    )
    find_playTime_target: Literal[1, 2, 3] | None = MaaField(
        default=None,
        label="常乐节点目标",
        group="主题专属",
        widget="select",
        depends_on={"mode": 20001},
        enum_labels={"1": "令（掷地有声）", "2": "黍（种因得果）", "3": "年（三缺一）"},
        description="想刷的常乐节点（刷常乐节点模式必填）",
    )
    investment_enabled: bool | None = MaaField(
        default=True,
        label="投资源石锭",
        group="投资",
        risk=RISK_CONSUME,
        description="把源石锭投进商店（默认开启）",
    )
    investments_count: int | None = MaaField(
        default=None,
        label="投资次数",
        group="投资",
        description="投多少次后停止；不填则不限制",
    )
    stop_when_investment_full: bool | None = MaaField(
        default=False,
        label="投资满后停止任务",
        group="投资",
        description="投资满后自动停止任务（默认关闭）",
    )
    investment_with_more_score: bool | None = MaaField(
        default=False,
        label="投资后再购物",
        group="投资",
        depends_on={"mode": 1},
        description="投资后顺便购物（仅刷源石锭模式）",
    )
    monthly_squad_auto_iterate: bool | None = MaaField(
        default=None,
        label="月度小队自动切换",
        group="月度与调查",
        depends_on={"mode": 6},
        description="月度小队自动切换（仅刷月度小队模式）",
    )
    monthly_squad_check_comms: bool | None = MaaField(
        default=None,
        label="把月度小队通信作为切换依据",
        group="月度与调查",
        depends_on={"mode": 6},
        description="把月度小队通信也作为切换依据（默认关闭）",
    )
    deep_exploration_auto_iterate: bool | None = MaaField(
        default=None,
        label="深入调查自动切换",
        group="月度与调查",
        depends_on={"mode": 7},
        description="深入调查自动切换（仅刷深入调查模式）",
    )
    check_collapsal_paradigms: bool | None = MaaField(
        default=None,
        label="检测坍缩范式",
        group="坍缩范式",
        depends_on={"theme": "Sami"},
        description="检测拿到的坍缩范式；不填由内核按模式决定",
    )
    double_check_collapsal_paradigms: bool | None = MaaField(
        default=None,
        label="坍缩范式检测防漏",
        group="坍缩范式",
        depends_on={"check_collapsal_paradigms": True},
        description="多一道坍缩范式检测，避免漏检（不填由内核决定）",
    )
    expected_collapsal_paradigms: list[str] | None = MaaField(
        default=None,
        label="希望触发的坍缩范式",
        group="坍缩范式",
        widget="tags",
        depends_on={"theme": "Sami", "mode": 5},
        description="希望刷到的坍缩范式（仅刷坍缩范式模式）",
    )

    @model_validator(mode="after")
    def _check_cross_field(self) -> "RoguelikeInput":
        """主题／模式相关的跨字段规则：一律 fail loud，不静默忽略。"""
        # mode 必须收在 Literal 之内（2/3 由字段类型放行，这里给具体错误码）。
        if self.mode == 2:
            raise AppError(
                ErrorCode.TASK_PARAM_DEPRECATED,
                "Roguelike.mode=2 已弃用：该模式兼顾刷分与刷源石锭，不再维护；"
                "请改用 mode=0 或 mode=1",
                {"field": "mode", "value": 2},
            )
        if self.mode == 3:
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "Roguelike.mode=3 尚在开发中，当前不可用",
                {"field": "mode", "value": 3},
            )

        # 内核默认主题是 Phantom、默认模式是 0；「没传」不等于「不生效」，按默认值判定。
        theme = self.theme if self.theme is not None else "Phantom"
        mode = self.mode if self.mode is not None else 0

        allowed = ROGUELIKE_THEME_MODES[theme]
        if mode not in allowed:
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "Roguelike.mode=%d 不适用于 %s 主题；该主题允许的模式：%s"
                % (mode, theme, "、".join(str(item) for item in sorted(allowed))),
                {"field": "mode", "theme": theme, "allowed_modes": sorted(allowed)},
            )

        if self.find_playTime_target is None and mode == 20001:
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "mode=20001（刷常乐节点）必须给 find_playTime_target（1–3）",
                {"field": "find_playTime_target", "depends_on": {"mode": 20001}},
            )
        if self.find_playTime_target is not None and mode != 20001:
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "find_playTime_target 仅在 mode=20001（刷常乐节点）时有效",
                {"field": "find_playTime_target", "depends_on": {"mode": 20001}},
            )

        if self.stop_at_final_boss is True and theme == "Phantom":
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "stop_at_final_boss 仅在除 Phantom 以外的主题下生效",
                {"field": "stop_at_final_boss", "theme": theme},
            )
        if self.refresh_trader_with_dice is True and theme != "Mizuki":
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                'refresh_trader_with_dice 仅适用于 Mizuki 主题（当前 theme="%s"）' % theme,
                {"field": "refresh_trader_with_dice", "depends_on": {"theme": "Mizuki"}},
            )
        if self.first_floor_foldartal is not None and theme != "Sami":
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                'first_floor_foldartal 仅适用于 Sami 主题（当前 theme="%s"）' % theme,
                {"field": "first_floor_foldartal", "depends_on": {"theme": "Sami"}},
            )
        if self.use_foldartal is not None and theme != "Sami":
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                'use_foldartal 仅适用于 Sami 主题（当前 theme="%s"）' % theme,
                {"field": "use_foldartal", "depends_on": {"theme": "Sami"}},
            )
        if self.start_foldartal_list and not (theme == "Sami" and mode == 4):
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "start_foldartal_list 仅主题为 Sami 且模式为 4 时有效",
                {"field": "start_foldartal_list", "depends_on": {"theme": "Sami", "mode": 4}},
            )
        if self.check_collapsal_paradigms is True and theme != "Sami":
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                'check_collapsal_paradigms 仅适用于 Sami 主题（当前 theme="%s"）' % theme,
                {"field": "check_collapsal_paradigms", "depends_on": {"theme": "Sami"}},
            )
        if self.double_check_collapsal_paradigms is True:
            effective_check = (
                self.check_collapsal_paradigms
                if self.check_collapsal_paradigms is not None
                else mode == 5
            )
            if not (theme == "Sami" and effective_check):
                raise AppError(
                    ErrorCode.TASK_PARAM_INVALID,
                    "double_check_collapsal_paradigms 仅在主题为 Sami 且"
                    " check_collapsal_paradigms 为 True 时有效",
                    {
                        "field": "double_check_collapsal_paradigms",
                        "depends_on": {"theme": "Sami", "check_collapsal_paradigms": True},
                    },
                )
        if self.expected_collapsal_paradigms and not (theme == "Sami" and mode == 5):
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "expected_collapsal_paradigms 仅在主题为 Sami 且模式为 5 时有效",
                {
                    "field": "expected_collapsal_paradigms",
                    "depends_on": {"theme": "Sami", "mode": 5},
                },
            )
        if self.start_with_elite_two is True and mode != 4:
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "start_with_elite_two 仅在 mode=4（凹开局）时有效，当前 mode=%d" % mode,
                {"field": "start_with_elite_two", "depends_on": {"mode": 4}},
            )
        if self.only_start_with_elite_two is True and not (
            mode == 4 and self.start_with_elite_two is True
        ):
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "only_start_with_elite_two 要求 mode=4 且 start_with_elite_two=true",
                {
                    "field": "only_start_with_elite_two",
                    "depends_on": {"mode": 4, "start_with_elite_two": True},
                },
            )
        if self.investment_with_more_score is True and mode != 1:
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "investment_with_more_score 仅在 mode=1（刷源石锭）时有效",
                {"field": "investment_with_more_score", "depends_on": {"mode": 1}},
            )
        if mode != 4:
            for field, value in (
                ("collectible_mode_shopping", self.collectible_mode_shopping),
                ("collectible_mode_squad", self.collectible_mode_squad),
                ("collectible_mode_start_list", self.collectible_mode_start_list),
            ):
                if value is not None and value is not False:
                    raise AppError(
                        ErrorCode.TASK_PARAM_INVALID,
                        "%s 仅在 mode=4（凹开局）时有效" % field,
                        {"field": field, "depends_on": {"mode": 4}},
                    )
        if mode != 6:
            for field, value in (
                ("monthly_squad_auto_iterate", self.monthly_squad_auto_iterate),
                ("monthly_squad_check_comms", self.monthly_squad_check_comms),
            ):
                if value is not None:
                    raise AppError(
                        ErrorCode.TASK_PARAM_INVALID,
                        "%s 仅在 mode=6（刷月度小队）时有效" % field,
                        {"field": field, "depends_on": {"mode": 6}},
                    )
        if self.deep_exploration_auto_iterate is not None and mode != 7:
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "deep_exploration_auto_iterate 仅在 mode=7（刷深入调查）时有效",
                {"field": "deep_exploration_auto_iterate", "depends_on": {"mode": 7}},
            )
        if self.blackflow_strategy is not None and theme != "BlackFlow":
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                'blackflow_strategy 仅适用于 BlackFlow 主题（当前 theme="%s"）' % theme,
                {"field": "blackflow_strategy", "depends_on": {"theme": "BlackFlow"}},
            )
        if self.blackflow_cultivation_target is not None and theme != "BlackFlow":
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                'blackflow_cultivation_target 仅适用于 BlackFlow 主题（当前 theme="%s"）' % theme,
                {
                    "field": "blackflow_cultivation_target",
                    "depends_on": {"theme": "BlackFlow"},
                },
            )
        if self.use_nonfriend_support is True and self.use_support is not True:
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "use_nonfriend_support 要求 use_support=true",
                {"field": "use_nonfriend_support", "depends_on": {"use_support": True}},
            )
        return self


class ReclamationInput(TaskInputBase):
    """生息演算：自动刷分、制造与建造。"""

    model_config = _task_config(
        {
            "name": "Reclamation",
            "theme": "Tales",
            "mode": 1,
            "tools_to_craft": ["荧光棒"],
            "increment_mode": 0,
            "num_craft_batches": 16,
        }
    )

    name: Literal["Reclamation"] = MaaField(
        default="Reclamation",
        label="任务类型",
        description="由任务类型选择器决定，不用填",
    )
    theme: RECLAMATION_THEMES | None = MaaField(
        default="Tales",
        label="主题",
        widget="select",
        enum_labels={"Fire": "沙中之火", "Tales": "沙洲遗闻"},
        description="生息演算主题；沙中之火已下线（默认沙洲遗闻）",
    )
    mode: Literal[0, 1] | None = MaaField(
        default=0,
        label="模式",
        widget="select",
        enum_labels={
            "0": "刷分与建造点（进入战斗直接退出）",
            "1": "沙中之火：刷赤金；沙洲遗闻：自动制造物品并读档刷货币",
        },
        description="运行模式（默认刷分与建造点）",
    )
    tools_to_craft: list[str] | None = MaaField(
        default=[],
        label="自动制造的物品",
        group="制造",
        widget="tags",
        description="要自动制造的物品；不填则不制造",
    )
    increment_mode: Literal[0, 1] | None = MaaField(
        default=0,
        label="点击类型",
        group="制造",
        widget="select",
        enum_labels={"0": "连点", "1": "长按"},
        description="制造时点击屏幕的方式（默认连点）",
    )
    num_craft_batches: int | None = MaaField(
        default=16,
        label="单次最大制造轮数",
        group="制造",
        ge=1,
        description="单次最多制造几轮（默认 16）",
    )


class OperBoxInput(TaskInputBase):
    """干员识别：读一遍账号里有哪些干员、练到什么程度（规格 §7.2 T1）。

    没有参数：``AsstAppendTask("OperBox", {})`` 就能入队（docs/ENVIRONMENT.md
    「MaaCore 内核」节实测）。识别结果经 ``SubTaskExtraInfo`` 回调推给
    :mod:`maa_api.services.account.capture`，识别不了技能等级、专精与模组。
    """

    model_config = _task_config({"name": "OperBox"})

    name: Literal["OperBox"] = MaaField(
        default="OperBox",
        label="任务类型",
        description="由任务类型选择器决定，不用填",
    )


class DepotInput(TaskInputBase):
    """仓库识别：读一遍账号仓库「养成材料」页签的数量（规格 §7.2 T1）。

    没有参数，空参数即可入队。识别结果经 ``SubTaskExtraInfo`` 回调整表替换仓库
    数量；龙门币与作战记录不在该页签内。
    """

    model_config = _task_config({"name": "Depot"})

    name: Literal["Depot"] = MaaField(
        default="Depot",
        label="任务类型",
        description="由任务类型选择器决定，不用填",
    )


class CopilotInput(BaseModel):
    """执行已校验并上传的 Copilot 作业。"""

    model_config = _task_config(
        {"name": "Copilot", "asset_id": "asset-1", "formation": False}
    )

    name: Literal["Copilot"] = MaaField(
        default="Copilot",
        label="任务类型",
        description="由任务类型选择器决定，不用填",
    )
    asset_id: str = MaaField(
        label="Copilot 作业",
        min_length=1,
        max_length=36,
        description="要执行的已上传作业（不支持运行中设置）",
    )
    formation: bool = MaaField(
        default=False,
        label="重新编队",
        description="执行作业前按作业重新编队（默认关闭）",
    )

    def to_core_params(self) -> dict[str, Any]:
        """Keep the uploaded id in persisted input params; runner resolves it at append."""
        return self.model_dump(
            exclude={"name"},
            exclude_none=True,
            by_alias=True,
        )


#: MaaCore 内置任务与 Copilot 任务输入顺序，共用同一判别联合。
_TASK_MODEL_TYPES: tuple[type[BaseModel], ...] = (
    StartUpInput,
    CloseDownInput,
    FightInput,
    RecruitInput,
    InfrastInput,
    MallInput,
    AwardInput,
    RoguelikeInput,
    ReclamationInput,
    OperBoxInput,
    DepotInput,
    CopilotInput,
)

#: 判别联合：discriminator 沿用现有 ``name`` 字段，前端请求体结构基本不变。
TaskInput = Annotated[
    StartUpInput
    | CloseDownInput
    | FightInput
    | RecruitInput
    | InfrastInput
    | MallInput
    | AwardInput
    | RoguelikeInput
    | ReclamationInput
    | OperBoxInput
    | DepotInput
    | CopilotInput,
    Field(discriminator="name"),
]

#: ``name`` → 模型，供 schema 导出与按类型取模型使用。
TASK_MODELS: dict[str, type[BaseModel]] = {
    model.model_fields["name"].default: model for model in _TASK_MODEL_TYPES
}

#: ``name`` → UI 展示标签；内置任务沿用 MaaCore 的中文任务名。
TASK_LABELS: dict[str, str] = {
    "StartUp": "开始唤醒",
    "CloseDown": "关闭游戏",
    "Fight": "刷理智",
    "Recruit": "自动公招",
    "Infrast": "基建换班",
    "Mall": "获取信用及购物",
    "Award": "领取奖励",
    "Roguelike": "自动肉鸽",
    "Reclamation": "生息演算",
    "OperBox": "干员识别",
    "Depot": "仓库识别",
    "Copilot": "Copilot 作业",
}

#: 标注「不支持运行中设置」的参数字段：编辑运行中的流水线时前端置灰（docs/09 §7.2）。
RUNTIME_IMMUTABLE: dict[str, list[str]] = {
    "StartUp": [],
    "CloseDown": [],
    "Fight": ["stage"],
    "Recruit": [],
    "Infrast": ["facility", "filename", "plan_index"],
    "Mall": ["shopping", "buy_first", "blacklist"],
    "Award": [],
    "Roguelike": [],
    "Reclamation": [],
    "OperBox": [],
    "Depot": [],
    "Copilot": ["asset_id"],
}

#: 消耗类参数清单（docs/13 §4 + docs/09 §7.2）。
#:
#: agent 侧 ``PolicyEngine`` 直接 import 这份定义做人工确认判定，前端则读 schema 里的
#: ``x-risk`` 给出醒目提示：两处必须来自同一份清单，否则会出现「前端不提示但 agent
#: 要确认」或反之的不一致。成员**精确**限定为以下六个字段。
RISK_FIELDS: dict[str, frozenset[str]] = {
    "StartUp": frozenset(),
    "CloseDown": frozenset(),
    "Fight": frozenset({"stone", "medicine", "expiring_medicine"}),
    "Recruit": frozenset({"expedite"}),
    "Infrast": frozenset(),
    "Mall": frozenset({"shopping"}),
    "Award": frozenset(),
    "Roguelike": frozenset({"investment_enabled"}),
    "Reclamation": frozenset(),
    "OperBox": frozenset(),
    "Depot": frozenset(),
    "Copilot": frozenset(),
}


class PipelineCreate(BaseModel):
    """流水线提交请求体（docs/05 §7.4）。``source`` 不在请求体里，由服务端按入口判定。"""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "title": "日常",
                    "tasks": [
                        {"name": "StartUp", "start_game_enabled": True},
                        {
                            "name": "Infrast",
                            "mode": 0,
                            "facility": [
                                "Mfg",
                                "Trade",
                                "Control",
                                "Power",
                                "Reception",
                                "Office",
                                "Dorm",
                            ],
                        },
                        {"name": "Fight", "stage": "1-7", "series": 0},
                        {
                            "name": "Recruit",
                            "select": [4, 5],
                            "confirm": [3, 4, 5],
                            "times": 4,
                        },
                        {"name": "Mall", "shopping": True, "blacklist": ["加急许可", "家具零件"]},
                        {"name": "Award", "award": True},
                        {"name": "CloseDown"},
                    ],
                }
            ]
        },
    )

    # Keep request-level limits in JSON Schema for clients, while letting the
    # service map empty/oversized submissions to PIPELINE_EMPTY and
    # PIPELINE_TOO_MANY_TASKS instead of generic 422 validation errors.
    tasks: list[TaskInput] = Field(
        json_schema_extra={"minItems": 1, "maxItems": 32}
    )
    title: str | None = Field(default=None, max_length=64)
    priority: int | None = Field(default=None, ge=0, le=2)
    notify_on_finish: bool = True


class ChannelDefaults(BaseModel):
    """全局渠道默认值；由 ``SettingService`` 的 ``channel.client_type`` / ``channel.server`` 构造。

    :meth:`normalize` 只把它注入「用户完全没提」的字段。
    """

    model_config = ConfigDict(extra="forbid")

    client_type: CLIENT_TYPES = "Bilibili"
    server: SERVERS = "CN"


class NormalizedTask(BaseModel):
    """校验之后、落库之前的最终任务形态（docs/05 §9）。

    ``params`` 是投递给内核的参数字典（已注入全局默认值、已剔除 None）；
    ``raw_params`` 是用户原始提交的按别名序列化快照，用于审计与回显。
    """

    model_config = ConfigDict(extra="forbid")

    type_name: str
    task_name: str
    params: dict[str, Any]
    raw_params: dict[str, Any]


def normalize(task: TaskInput, defaults: ChannelDefaults) -> NormalizedTask:
    """注入全局渠道默认值并生成 :class:`NormalizedTask`。

    三态语义（``model_fields_set`` 区分，docs/05 §9）：

    - 字段未出现在请求 JSON 里 → 注入 ``defaults`` 里的全局默认值；
    - 显式传 ``null`` → 不注入、不下发，由内核决定，``raw_params`` 保留 null；
    - 显式传具体值 → 原样使用。

    注入不放在 validator 里：默认值来自数据库（validator 取不到异步会话），
    而且模型必须如实表达「用户没设这个字段」，否则 ``model_fields_set`` 与
    ``raw_params`` 都会失真。
    """
    raw_params = task.model_dump(exclude={"name"}, exclude_unset=True, by_alias=True)
    params = task.to_core_params()

    if "client_type" in type(task).model_fields and "client_type" not in task.model_fields_set:
        params["client_type"] = defaults.client_type
    if "server" in type(task).model_fields and "server" not in task.model_fields_set:
        params["server"] = defaults.server

    return NormalizedTask(
        type_name=task.name,
        task_name=TASK_LABELS[task.name],
        params=params,
        raw_params=raw_params,
    )
