"""9 种任务类型的 pydantic v2 模型、判别联合、``x-*`` 参数元信息与 ``normalize``。

权威来源与取舍
--------------

字段名、类型与说明文字逐字段迁移自 ``maa_api/model/core/task.py`` 中 9 个任务类
``__init__`` docstring 的「参数:」段。docstring 是参数的权威来源，但它只把取值范围、
枚举域与跨字段规则记在注释里；凡是 docstring 与 docs/05 §7.5／§7.6 冲突或过时之处，
**一律以 docs/05 为准**。本模块落实的五处修正：

1. ``Reclamation`` 的参数键由 :meth:`TaskInputBase.to_core_params` 的 ``model_dump``
   生成，不再出现旧实现里「变量当键」（``{enable: enable, ...}``）；
2. 统一使用 ``filename``，不为旧实现的 ``failename`` 拼写错误保留兼容别名；
3. ``Award`` 的 ``mail``/``recruit``/``orundum``/``mining``/``specialaccess`` 一律
   ``default=None``，不预设 True/False —— 不下发则由内核用自己的默认值；
4. ``Infrast.threshold`` 同样不设服务端默认值（``default=None``），内核当前默认值
   ``0.3`` 只出现在 ``description`` 文案里（docs/13 §7）；
5. ``Fight.times`` 与 ``Recruit.times`` 是两个独立字段，语义（战斗次数／招募次数）
   不混用；旧 ``TaskRequest`` 里重复声明的 ``times`` 问题结构性消失。

docstring 中其它与 docs/05 有出入、按 05 处理的点：``Recruit.recruitment_time`` 声明为
``dict[str, int]``（JSON 对象键只能是字符串，旧签名 ``dict[int, int]`` 在 JSON 层面不
成立）；``Roguelike.theme``/``mode``、``Infrast.threshold`` 等「内核默认值」一律不进模型
默认值，而由内核在收到缺省参数时自行决定。

跨字段规则与抛码
----------------

主题／模式相关的跨字段规则一律实现为 ``@model_validator(mode="after")`` 并抛
``AppError``，而不是静默忽略——「设了但不生效」是用户最常见的困惑来源。
抛码接法采用 ``tests/fixtures/api_probe_findings.md`` §必读结论 2 的实测结论 A：
``model_validator`` 里抛非 ``ValueError`` 的自定义异常时 pydantic 2.11 不做包装，
``AppError`` 会穿过 ``TypeAdapter`` / FastAPI 请求体校验直达 ``api/errors.py`` 的
``AppError`` 处理器。能靠字段类型、``ge``/``le``、``Literal`` 拦住的错误留给 pydantic
原生 ``ValidationError``（422 ``VALIDATION_ERROR``，可定位到具体字段）；只有
**跨字段**语义才由这里抛 ``TASK_PARAM_INVALID``（422）或 ``TASK_PARAM_DEPRECATED``
（400 ``Roguelike.mode=2``）。领域层不读数据库、不做 IO。
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
    "FightInput",
    "InfrastInput",
    "MaaField",
    "MallInput",
    "NormalizedTask",
    "PipelineCreate",
    "RECLAMATION_THEMES",
    "RISK_CONSUME",
    "RISK_FIELDS",
    "ROGUELIKE_THEMES",
    "RUNTIME_IMMUTABLE",
    "RecruitInput",
    "ReclamationInput",
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

#: 肉鸽主题枚举（4 值），来自 ``RoguelikeTask`` docstring。
ROGUELIKE_THEMES = Literal["Phantom", "Mizuki", "Sami", "Sarkaz"]

#: 生息演算主题枚举（2 值）。
RECLAMATION_THEMES = Literal["Fire", "Tales"]

#: 基建换班设施枚举（7 值，顺序即换班顺序）。
INFRAST_FACILITIES = Literal["Mfg", "Trade", "Power", "Control", "Reception", "Office", "Dorm"]

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
    （实测见 ``tests/fixtures/api_probe_findings.md`` §2.7）。
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

    示例必须能被模型自身校验通过（测试逐个 ``model_validate`` 钉住）——从
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
    """9 种任务输入模型的公共基类。

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
        default=None,
        label="启用本任务",
        description="是否启用本任务；不传则由内核使用默认值（True）",
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
# 9 种任务类型
# --------------------------------------------------------------------------- #


class StartUpInput(TaskInputBase):
    """开始唤醒：启动客户端并进入游戏。"""

    model_config = _task_config(
        {"name": "StartUp", "client_type": "Official", "start_game_enabled": True}
    )

    name: Literal["StartUp"] = MaaField(
        default="StartUp",
        label="任务类型",
        description="判别联合 discriminator，固定为 StartUp；由任务类型选择器决定，用户不需要填写",
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
        description=(
            "客户端版本；未指定时套用全局渠道默认值（默认 Bilibili）。"
            "可选值：Official / Bilibili / txwy / YoStarEN / YoStarJP / YoStarKR"
        ),
    )
    start_game_enabled: bool | None = MaaField(
        default=None,
        label="自动启动客户端",
        group="账号",
        description="是否自动启动客户端；不传则由内核使用默认值（True，即自动启动）",
    )
    account_name: str | None = MaaField(
        default=None,
        label="切换账号",
        group="账号",
        description=(
            "切换账号；仅支持切换至已登录的账号，使用登录名进行查找，"
            "保证输入内容在所有已登录账号中唯一即可。"
            "官服示例 123****4567（可输入 123****4567、4567、123、3****4567）；"
            "B 服示例 张三（可输入 张三、张、三）"
        ),
        examples=["123****4567", "4567", "张三"],
    )


class CloseDownInput(TaskInputBase):
    """关闭游戏：任务结束后关闭客户端。"""

    model_config = _task_config({"name": "CloseDown", "client_type": "Bilibili"})

    name: Literal["CloseDown"] = MaaField(
        default="CloseDown",
        label="任务类型",
        description="判别联合 discriminator，固定为 CloseDown；由任务类型选择器决定，用户不需要填写",
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
        description=(
            "客户端版本；docstring 标注「必选，填空则不执行」，实际上由全局渠道默认值"
            "注入保证非空（未指定时套用默认 Bilibili）。"
            "可选值：Official / Bilibili / txwy / YoStarEN / YoStarJP / YoStarKR"
        ),
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
        description="判别联合 discriminator，固定为 Fight；由任务类型选择器决定，用户不需要填写",
    )
    stage: str | None = MaaField(
        default=None,
        label="关卡名",
        description=(
            "关卡名；留空则识别当前/上次的关卡。支持全部主线关卡（如 1-7、S3-2），"
            "可在关卡结尾输入 Normal/Hard 表示需要切换标准与磨难难度；"
            "剿灭作战必须输入 Annihilation；当期 SS 活动后三关必须输入完整关卡编号。"
            "不支持运行中设置"
        ),
        examples=["1-7", "S3-2", "Annihilation", "CE-6Hard"],
    )
    times: int | None = MaaField(
        default=None,
        label="指定次数",
        ge=0,
        description="指定战斗次数；不传则由内核使用默认值（无穷大）",
    )
    series: int | None = MaaField(
        default=None,
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
        description=(
            "连战次数；-1 为禁用切换，0 为自动切换为当前可用的最大次数"
            "（如当前理智不够 6 次，则选择最低可用次数），1~6 为指定连战次数"
        ),
    )
    medicine: int | None = MaaField(
        default=None,
        label="最大理智药数",
        group="资源消耗",
        ge=0,
        risk=RISK_CONSUME,
        description="最大使用理智药数量；不传则由内核使用默认值（0，即不吃药）",
    )
    expiring_medicine: int | None = MaaField(
        default=None,
        label="最大 48 小时内过期理智药数",
        group="资源消耗",
        ge=0,
        risk=RISK_CONSUME,
        description="最大使用 48 小时内过期理智药数量；不传则由内核使用默认值（0）",
    )
    stone: int | None = MaaField(
        default=None,
        label="最大碎石数",
        group="资源消耗",
        ge=0,
        risk=RISK_CONSUME,
        description="最大吃石头（源石）数量；不传则由内核使用默认值（0，即不碎石）",
    )
    dr_grandet: bool | None = MaaField(
        default=None,
        label="节省理智碎石模式",
        group="资源消耗",
        description=(
            "节省理智碎石模式；仅在可能产生碎石效果时生效：在碎石确认界面等待，"
            "直到当前的 1 点理智恢复完成后再立刻碎石。不传则由内核使用默认值（False）"
        ),
        validation_alias=AliasChoices("DrGrandet", "dr_grandet"),
        serialization_alias="DrGrandet",
    )
    server: SERVERS | None = MaaField(
        default=None,
        label="服务器",
        group="账号",
        widget="select",
        enum_labels={"CN": "国服", "US": "美服", "JP": "日服", "KR": "韩服"},
        description=(
            "服务器，会影响掉落识别及上传；未指定时套用全局默认值（CN）。"
            "可选值：CN / US / JP / KR"
        ),
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
        description=(
            "客户端版本；用于游戏崩溃时重启并连回去继续刷，若为空则不启用该功能。"
            "未指定时套用全局渠道默认值（默认 Bilibili）"
        ),
    )
    report_to_penguin: bool | None = MaaField(
        default=None,
        label="汇报企鹅数据",
        group="数据上报",
        description="是否汇报企鹅数据；不传则由内核使用默认值（False）",
    )
    penguin_id: str | None = MaaField(
        default=None,
        label="企鹅数据 ID",
        group="数据上报",
        depends_on={"report_to_penguin": True},
        description="企鹅数据汇报 id；仅在 report_to_penguin 为 True 时有效",
    )
    drops: dict[str, int] | None = MaaField(
        default=None,
        label="指定掉落数量",
        group="高级",
        widget="item-count-map",
        description=(
            "指定掉落数量；key 为 item_id（见 resource/item_index.json），value 为数量。"
            "是或的关系，即任一达到即停止任务"
        ),
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
        description="判别联合 discriminator，固定为 Recruit；由任务类型选择器决定，用户不需要填写",
    )
    refresh: bool | None = MaaField(
        default=None,
        label="刷新三星 Tags",
        description="是否刷新三星 Tags；不传则由内核使用默认值（False）",
    )
    select: list[Annotated[int, Field(ge=1, le=6)]] | None = MaaField(
        default=None,
        label="点击的 Tag 等级",
        widget="tags",
        description="会去点击标签的 Tag 等级，必选；元素取值范围 1~6",
    )
    confirm: list[Annotated[int, Field(ge=1, le=6)]] | None = MaaField(
        default=None,
        label="确认的 Tag 等级",
        widget="tags",
        description="会去点击确认的 Tag 等级，必选；元素取值范围 1~6。若仅公招计算，可设置为空数组",
    )
    first_tags: list[str] | None = MaaField(
        default=None,
        label="首选 Tags",
        group="公招标签",
        widget="tags",
        description=(
            "首选 Tags，仅在 Tag 等级为 3 时有效；会尽可能多地选择这里的 Tags（如果有）。"
            "属于强制选择，会忽略所有「让 3 星 Tag 不被选择」的设置"
        ),
    )
    extra_tags_mode: Literal[0, 1, 2] | None = MaaField(
        default=None,
        label="选择更多的 Tags",
        widget="select",
        enum_labels={
            "0": "默认行为",
            "1": "选 3 个 Tags（即使可能冲突）",
            "2": "尽可能同时选择更多高星 Tag 组合（即使可能冲突）",
        },
        description=(
            "选择更多的 Tags；0 为默认行为，1 为选 3 个 Tags（即使可能冲突），"
            "2 为如果可能则同时选择更多的高星 Tag 组合（即使可能冲突）。"
            "不传则由内核使用默认值（0）"
        ),
    )
    times: int | None = MaaField(
        default=None,
        label="招募次数",
        ge=0,
        description="招募多少次；不传则由内核使用默认值（0）。若仅公招计算，可设置为 0",
    )
    set_time: bool | None = MaaField(
        default=None,
        label="设置招募时限",
        depends_on={"times": 0},
        description="是否设置招募时限；仅在 times 为 0 时生效。不传则由内核使用默认值（True）",
    )
    expedite: bool | None = MaaField(
        default=None,
        label="使用加急许可",
        group="加急与跳过",
        risk=RISK_CONSUME,
        description="是否使用加急许可；不传则由内核使用默认值（False）",
    )
    expedite_times: int | None = MaaField(
        default=None,
        label="加急次数",
        group="加急与跳过",
        ge=0,
        depends_on={"expedite": True},
        description=(
            "加急次数；仅在 expedite 为 True 时有效。可选，不传则由内核使用默认值"
            "（无限使用，直到 times 达到上限）"
        ),
    )
    skip_robot: bool | None = MaaField(
        default=None,
        label="跳过小车词条",
        group="加急与跳过",
        description="是否在识别到小车词条时跳过；不传则由内核使用默认值（True，即跳过）",
    )
    recruitment_time: dict[str, int] | None = MaaField(
        default=None,
        label="招募时限映射",
        group="公招标签",
        widget="duration-map",
        description=(
            "Tag 等级（大于等于 3，JSON 键为字符串）和对应的希望招募时限，单位为分钟；"
            "内核默认值都为 540（即 09:00:00）"
        ),
    )
    report_to_penguin: bool | None = MaaField(
        default=None,
        label="汇报企鹅数据",
        group="数据上报",
        description="是否汇报企鹅数据；不传则由内核使用默认值（False）",
    )
    penguin_id: str | None = MaaField(
        default=None,
        label="企鹅数据 ID",
        group="数据上报",
        depends_on={"report_to_penguin": True},
        description="企鹅数据汇报 id；仅在 report_to_penguin 为 True 时有效",
    )
    report_to_yituliu: bool | None = MaaField(
        default=None,
        label="汇报一图流数据",
        group="数据上报",
        description="是否汇报一图流数据；不传则由内核使用默认值（False）",
    )
    yituliu_id: str | None = MaaField(
        default=None,
        label="一图流 ID",
        group="数据上报",
        depends_on={"report_to_yituliu": True},
        description="一图流汇报 id；仅在 report_to_yituliu 为 True 时有效",
    )
    server: SERVERS | None = MaaField(
        default=None,
        label="服务器",
        group="账号",
        widget="select",
        enum_labels={"CN": "国服", "US": "美服", "JP": "日服", "KR": "韩服"},
        description=(
            "服务器，会影响上传；未指定时套用全局默认值（CN）。"
            "可选值：CN / US / JP / KR"
        ),
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
        description="判别联合 discriminator，固定为 Infrast；由任务类型选择器决定，用户不需要填写",
    )
    mode: Literal[0, 10000, 20000] | None = MaaField(
        default=None,
        label="换班工作模式",
        widget="select",
        enum_labels={
            "0": "默认换班（单设施最优解）",
            "10000": "自定义换班（读取用户配置）",
            "20000": "一键轮换",
        },
        description=(
            "换班工作模式；0 为默认换班模式（单设施最优解），10000 为自定义换班模式"
            "（读取用户配置），20000 为一键轮换模式（会跳过控制中枢、发电站、宿舍以及"
            "办公室，其余设施不进行换班但保留基本操作，如使用无人机、会客室逻辑）。"
            "不传则由内核使用默认值（0）"
        ),
    )
    facility: list[INFRAST_FACILITIES] | None = MaaField(
        default=None,
        label="要换班的设施（有序）",
        widget="ordered-list",
        description=(
            "要换班的设施（有序，顺序即换班顺序），必选；"
            '设施名选项包括 "Mfg", "Trade", "Power", "Control", "Reception", "Office", "Dorm"。'
            "不支持运行中设置"
        ),
    )
    drones: INFRAST_DRONES | None = MaaField(
        default=None,
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
        description=(
            "无人机用途；mode == 10000 时该字段无效（会被忽略）。"
            '可选值包括 "_NotUse", "Money", "SyntheticJade", "CombatRecord", '
            '"PureGold", "OriginStone", "Chip"；不传则由内核使用默认值（_NotUse）'
        ),
    )
    threshold: float | None = MaaField(
        default=None,
        label="工作心情阈值",
        ge=0.0,
        le=1.0,
        description=(
            "工作心情阈值，取值范围 [0, 1.0]；内核当前默认值为 0.3（服务端不预设，"
            "不传则由内核决定）。mode == 10000 时该字段仅针对 autofill 有效"
        ),
    )
    replenish: bool | None = MaaField(
        default=None,
        label="源石碎片自动补货",
        description='贸易站「源石碎片」是否自动补货；不传则由内核使用默认值（False）',
    )
    dorm_notstationed_enabled: bool | None = MaaField(
        default=None,
        label='启用宿舍「未进驻」选项',
        description='是否启用宿舍「未进驻」选项；不传则由内核使用默认值（False）',
    )
    dorm_trust_enabled: bool | None = MaaField(
        default=None,
        label="剩余位置填入信赖未满干员",
        description="是否将宿舍剩余位置填入信赖未满干员；不传则由内核使用默认值（False）",
    )
    filename: str | None = MaaField(
        default=None,
        label="自定义配置路径",
        group="自定义换班",
        description=(
            "自定义配置路径，仅 mode == 10000 时生效且必填，否则会被忽略。"
            "不支持运行中设置"
        ),
    )
    plan_index: int | None = MaaField(
        default=None,
        label="方案序号",
        group="自定义换班",
        ge=0,
        description=(
            "使用配置中的方案序号，仅 mode == 10000 时生效且必填，否则会被忽略。"
            "不支持运行中设置"
        ),
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
        description="判别联合 discriminator，固定为 Mall；由任务类型选择器决定，用户不需要填写",
    )
    shopping: bool | None = MaaField(
        default=None,
        label="是否购物",
        risk=RISK_CONSUME,
        description="是否购物；不支持运行中设置。不传则由内核使用默认值（False）",
    )
    buy_first: list[str] | None = MaaField(
        default=None,
        label="优先购买列表",
        group="购买清单",
        widget="tags",
        description=(
            "优先购买列表（商品名，如「招聘许可」「龙门币」等）；"
            "不支持运行中设置，可选"
        ),
    )
    blacklist: list[str] | None = MaaField(
        default=None,
        label="黑名单列表",
        group="购买清单",
        widget="tags",
        description=(
            "黑名单列表（商品名，如「加急许可」「家具零件」等）；"
            "不支持运行中设置，可选"
        ),
    )
    force_shopping_if_credit_full: bool | None = MaaField(
        default=None,
        label="信用溢出时无视黑名单",
        description="是否在信用溢出时无视黑名单；不传则由内核使用默认值（True）",
    )
    only_buy_discount: bool | None = MaaField(
        default=None,
        label="只购买折扣物品",
        description=(
            "是否只购买折扣物品，只作用于第二轮购买；不传则由内核使用默认值（False）"
        ),
    )
    reserve_max_credit: bool | None = MaaField(
        default=None,
        label="信用点低于 300 时停止购买",
        description=(
            "是否在信用点低于 300 时停止购买，只作用于第二轮购买；"
            "不传则由内核使用默认值（False）"
        ),
    )


class AwardInput(TaskInputBase):
    """领取各种奖励。

    按 docs/05 §7.6：``mail``/``recruit``/``orundum``/``mining``/``specialaccess``
    一律 ``default=None``，不预设 True/False —— 旧 ``TaskRequest`` 把它们写成 True
    与 docstring 冲突，且服务端预设默认值会在内核调整默认行为时产生偏差。
    """

    model_config = _task_config({"name": "Award", "award": True, "mail": True})

    name: Literal["Award"] = MaaField(
        default="Award",
        label="任务类型",
        description="判别联合 discriminator，固定为 Award；由任务类型选择器决定，用户不需要填写",
    )
    award: bool | None = MaaField(
        default=None,
        label="领取每日/每周任务奖励",
        description="领取每日/每周任务奖励；不传则由内核使用默认值（True）",
    )
    mail: bool | None = MaaField(
        default=None,
        label="领取邮件奖励",
        group="奖励项",
        description="领取所有邮件奖励；不传则由内核使用默认值（False）",
    )
    recruit: bool | None = MaaField(
        default=None,
        label="领取每日免费单抽",
        group="奖励项",
        description="领取限定池子赠送的每日免费单抽；不传则由内核使用默认值（False）",
    )
    orundum: bool | None = MaaField(
        default=None,
        label="领取幸运墙合成玉",
        group="奖励项",
        description="领取幸运墙的合成玉奖励；不传则由内核使用默认值（False）",
    )
    mining: bool | None = MaaField(
        default=None,
        label="领取限时开采许可奖励",
        group="奖励项",
        description="领取限时开采许可的合成玉奖励；不传则由内核使用默认值（False）",
    )
    specialaccess: bool | None = MaaField(
        default=None,
        label="领取五周年月卡奖励",
        group="奖励项",
        description="领取五周年赠送的月卡奖励；不传则由内核使用默认值（False）",
    )


class RoguelikeInput(TaskInputBase):
    """无限刷肉鸽（集成战略）。

    主题／模式相关的跨字段规则全部实现为 ``model_validator`` 并抛 ``AppError``
    （``TASK_PARAM_INVALID``；``mode=2`` 是已弃用值，抛 ``TASK_PARAM_DEPRECATED``）
    ——「设了但不生效」必须在提交时就告诉用户，而不是静默忽略。
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
    )

    name: Literal["Roguelike"] = MaaField(
        default="Roguelike",
        label="任务类型",
        description="判别联合 discriminator，固定为 Roguelike；由任务类型选择器决定，用户不需要填写",
    )
    theme: ROGUELIKE_THEMES | None = MaaField(
        default=None,
        label="主题",
        widget="select",
        enum_labels={
            "Phantom": "傀影与猩红孤钻",
            "Mizuki": "水月与深蓝之树",
            "Sami": "探索者的银凇止境",
            "Sarkaz": "萨卡兹的无终奇语",
        },
        description=(
            '主题；可选值包括 "Phantom", "Mizuki", "Sami", "Sarkaz"。'
            "不传则由内核使用默认值（Phantom）"
        ),
    )
    mode: Literal[0, 1, 2, 3, 4, 5] | None = MaaField(
        default=None,
        label="模式",
        widget="select",
        enum_labels={
            "0": "刷分/奖励点数",
            "1": "刷源石锭",
            "2": "已弃用",
            "3": "开发中",
            "4": "凹开局",
            "5": "刷坍缩范式（仅 Sami）",
        },
        description=(
            "模式；0 为刷分/奖励点数（尽可能稳定地打更多层数），1 为刷源石锭"
            "（第一层投资完就退出），2 已弃用（兼顾模式 0 与 1，投资过后再退出，"
            "没有投资就继续往后打），3 开发中，4 为凹开局（先在 0 难度下到达第三层后"
            "重开，再到指定难度下凹开局奖励；Phantom 主题下不切换难度），"
            "5 为刷坍缩范式（仅适用于 Sami 主题）。不传则由内核使用默认值（0）"
        ),
    )
    squad: str | None = MaaField(
        default=None,
        label="开局分队名",
        description='开局分队名；不传则由内核使用默认值（"指挥分队"）',
    )
    roles: str | None = MaaField(
        default=None,
        label="开局职业组",
        description='开局职业组；不传则由内核使用默认值（"取长补短"）',
    )
    core_char: str | None = MaaField(
        default=None,
        label="开局干员名",
        group="开局",
        description=(
            "开局干员名；仅支持单个干员中文名（无论区服）；"
            '若留空或设置为空字符串 "" 则根据练度自动选择'
        ),
    )
    use_support: bool | None = MaaField(
        default=None,
        label="开局干员为助战干员",
        group="开局",
        description="开局干员是否为助战干员；不传则由内核使用默认值（False）",
    )
    use_nonfriend_support: bool | None = MaaField(
        default=None,
        label="允许非好友助战",
        group="开局",
        depends_on={"use_support": True},
        description=(
            "是否可以是非好友助战干员；仅在 use_support 为 True 时有效。"
            "不传则由内核使用默认值（False）"
        ),
    )
    starts_count: int | None = MaaField(
        default=None,
        label="开始探索次数",
        group="开局",
        description=(
            "开始探索的次数；达到后自动停止任务。"
            "不传则由内核使用默认值（INT_MAX，即不限制）"
        ),
    )
    difficulty: int | None = MaaField(
        default=None,
        label="指定难度等级",
        group="开局",
        ge=0,
        description=(
            "指定难度等级；仅适用于除 Phantom 以外的主题，若未解锁难度则会选择"
            "当前已解锁的最高难度。不传则由内核使用默认值（0）"
        ),
    )
    start_with_elite_two: bool | None = MaaField(
        default=None,
        label="凹干员精二直升",
        group="开局",
        description=(
            "是否在凹开局的同时凹干员精二直升；仅适用于模式 4。"
            "不传则由内核使用默认值（False）"
        ),
    )
    only_start_with_elite_two: bool | None = MaaField(
        default=None,
        label="只凹精二直升",
        group="开局",
        depends_on={"mode": 4, "start_with_elite_two": True},
        description=(
            "是否只凹开局干员精二直升而忽视其他开局条件；"
            "仅在模式为 4 且 start_with_elite_two 为 True 时有效。"
            "不传则由内核使用默认值（False）"
        ),
    )
    first_floor_foldartal: str | None = MaaField(
        default=None,
        label="第一层远见密文板",
        group="开局",
        depends_on={"theme": "Sami"},
        description=(
            "希望在第一层远见阶段得到的密文板；仅适用于 Sami 主题，不限模式；"
            "若成功凹到则停止任务"
        ),
    )
    start_foldartal_list: list[str] | None = MaaField(
        default=None,
        label="开局奖励密文板列表",
        group="开局",
        widget="tags",
        depends_on={"theme": "Sami", "mode": 4},
        description=(
            "凹开局时希望在开局奖励阶段得到的密文板；仅主题为 Sami 且模式为 4 时有效。"
            "仅当开局拥有列表中所有的密文板时才算凹开局成功；"
            "此参数须与「生活至上分队」同时使用，其他分队在开局奖励阶段不会获得密文板"
        ),
    )
    use_foldartal: bool | None = MaaField(
        default=None,
        label="使用密文板",
        group="主题专属",
        depends_on={"theme": "Sami"},
        description=(
            "是否使用密文板；仅适用于 Sami 主题。模式 5 下内核默认值为 False，"
            "其他模式下为 True；不传则由内核决定"
        ),
    )
    stop_at_final_boss: bool | None = MaaField(
        default=None,
        label="第 5 层险路恶敌前停止",
        group="主题专属",
        description=(
            "是否在第 5 层险路恶敌节点前停止任务；仅适用于除 Phantom 以外的主题。"
            "不传则由内核使用默认值（False）"
        ),
    )
    investment_enabled: bool | None = MaaField(
        default=None,
        label="投资源石锭",
        group="投资",
        risk=RISK_CONSUME,
        description="是否投资源石锭；不传则由内核使用默认值（True）",
    )
    investments_count: int | None = MaaField(
        default=None,
        label="投资次数",
        group="投资",
        description=(
            "投资源石锭的次数；达到后自动停止任务。"
            "不传则由内核使用默认值（INT_MAX，即不限制）"
        ),
    )
    stop_when_investment_full: bool | None = MaaField(
        default=None,
        label="投资满后停止任务",
        group="投资",
        description=(
            "是否在投资到达上限后自动停止任务；不传则由内核使用默认值（False）"
        ),
    )
    refresh_trader_with_dice: bool | None = MaaField(
        default=None,
        label="用骰子刷新商店",
        group="主题专属",
        depends_on={"theme": "Mizuki"},
        description=(
            "是否用骰子刷新商店购买特殊商品；仅适用于 Mizuki 主题，用于刷指路鳞。"
            "不传则由内核使用默认值（False）"
        ),
    )
    check_collapsal_paradigms: bool | None = MaaField(
        default=None,
        label="检测坍缩范式",
        group="坍缩范式",
        depends_on={"theme": "Sami"},
        description=(
            "是否检测获取的坍缩范式；仅适用于 Sami 主题。模式 5 下内核默认值为 True，"
            "其他模式下为 False；不传则由内核决定"
        ),
    )
    double_check_collapsal_paradigms: bool | None = MaaField(
        default=None,
        label="坍缩范式检测防漏",
        group="坍缩范式",
        depends_on={"check_collapsal_paradigms": True},
        description=(
            "是否执行坍缩范式检测防漏措施；仅在主题为 Sami 且"
            " check_collapsal_paradigms 为 True 时有效。模式 5 下内核默认值为 True，"
            "其他模式下为 False；不传则由内核决定"
        ),
    )
    expected_collapsal_paradigms: list[str] | None = MaaField(
        default=None,
        label="希望触发的坍缩范式",
        group="坍缩范式",
        widget="tags",
        depends_on={"theme": "Sami", "mode": 5},
        description=(
            "希望触发的坍缩范式；仅在主题为 Sami 且模式为 5 时有效。"
            '内核默认值为 ["目空一些", "睁眼瞎", "图像损坏", "一抹黑"]'
        ),
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

        if self.mode == 5 and theme != "Sami":
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                'Roguelike.mode=5 仅适用于 Sami 主题（当前 theme="%s"）' % theme,
                {"field": "mode", "depends_on": {"theme": "Sami"}},
            )
        if self.difficulty is not None and theme == "Phantom":
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "difficulty 仅在除 Phantom 以外的主题下生效；"
                "Phantom 主题请去掉 difficulty 或改选其他主题",
                {"field": "difficulty", "theme": theme},
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
        if self.start_foldartal_list and not (theme == "Sami" and self.mode == 4):
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
                else self.mode == 5
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
        if self.expected_collapsal_paradigms and not (theme == "Sami" and self.mode == 5):
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "expected_collapsal_paradigms 仅在主题为 Sami 且模式为 5 时有效",
                {
                    "field": "expected_collapsal_paradigms",
                    "depends_on": {"theme": "Sami", "mode": 5},
                },
            )
        if self.only_start_with_elite_two is True and not (
            self.mode == 4 and self.start_with_elite_two is True
        ):
            raise AppError(
                ErrorCode.TASK_PARAM_INVALID,
                "only_start_with_elite_two 要求 mode=4 且 start_with_elite_two=true",
                {
                    "field": "only_start_with_elite_two",
                    "depends_on": {"mode": 4, "start_with_elite_two": True},
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
        description="判别联合 discriminator，固定为 Reclamation；由任务类型选择器决定，用户不需要填写",
    )
    theme: RECLAMATION_THEMES | None = MaaField(
        default=None,
        label="主题",
        widget="select",
        enum_labels={"Fire": "沙中之火", "Tales": "沙洲遗闻"},
        description='主题；Fire 为「沙中之火」，Tales 为「沙洲遗闻」',
    )
    mode: Literal[0, 1] | None = MaaField(
        default=None,
        label="模式",
        widget="select",
        enum_labels={
            "0": "刷分与建造点（进入战斗直接退出）",
            "1": "沙中之火：刷赤金；沙洲遗闻：自动制造物品并读档刷货币",
        },
        description=(
            "模式；0 为刷分与建造点（进入战斗直接退出），1 为沙中之火刷赤金、"
            "联络员买水后基地锻造／沙洲遗闻自动制造物品并读档刷货币。"
            "不传则由内核使用默认值（0）"
        ),
    )
    tools_to_craft: list[str] | None = MaaField(
        default=None,
        label="自动制造的物品",
        group="制造",
        widget="tags",
        description="自动制造的物品；不传则由内核使用默认值（荧光棒）",
    )
    increment_mode: Literal[0, 1] | None = MaaField(
        default=None,
        label="点击类型",
        group="制造",
        widget="select",
        enum_labels={"0": "连点", "1": "长按"},
        description="点击类型；0 为连点，1 为长按。不传则由内核使用默认值（0）",
    )
    num_craft_batches: int | None = MaaField(
        default=None,
        label="单次最大制造轮数",
        group="制造",
        ge=1,
        description="单次最大制造轮数；不传则由内核使用默认值（16）",
    )


#: 9 个任务输入模型，顺序即 docs/05 §7.4 判别联合的定义顺序。
_TASK_MODEL_TYPES: tuple[type[TaskInputBase], ...] = (
    StartUpInput,
    CloseDownInput,
    FightInput,
    RecruitInput,
    InfrastInput,
    MallInput,
    AwardInput,
    RoguelikeInput,
    ReclamationInput,
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
    | ReclamationInput,
    Field(discriminator="name"),
]

#: ``name`` → 模型，供 schema 导出与按类型取模型使用（M3-08）。
TASK_MODELS: dict[str, type[TaskInputBase]] = {
    model.model_fields["name"].default: model for model in _TASK_MODEL_TYPES
}

#: ``name`` → 中文任务名，与 ``maa_api/model/core/task.py`` 各类的 ``task_name`` 一致。
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
}

#: 消耗类参数清单（docs/13 §4 + docs/09 §7.2）。
#:
#: agent 侧 ``PolicyEngine`` 直接 import 这份定义做人工确认判定，前端则读 schema 里的
#: ``x-risk`` 给出醒目提示——两处必须来自同一份清单，否则会出现「前端不提示但 agent
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
