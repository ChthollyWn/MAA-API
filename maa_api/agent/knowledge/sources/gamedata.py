"""拆包实体卡来源（M77 spec §4.1、§4.5）。

只读三张语料表（``character_table`` / ``item_table`` / ``enemy_handbook_table``），
每张表解析完立刻生成卡片并丢掉原始 JSON，卡片暂存在 :attr:`_pending`；同步服务按
20 个一批来取 ``fetch``，所以 ``fetch`` 只弹出请求到的那几张。卡片正文的 sha256
前 16 位就是 ``version``，内容没变时第二次同步不会重建分块。

卡片只写模板里的字段，不写属性数值（``phases`` / ``attributes`` 之类），富文本标记
``<@…>``、``<$…>``、``</>`` 去掉，字面量 ``\\n`` 换成换行。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any

from maa_api.agent.knowledge.index.indexer import DocumentRef, SourceDocument
from maa_api.db.models import utcnow
from maa_api.domain.enums import KnowledgeSourceKind
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.services.external.gamedata import GamedataClient
from maa_api.services.game.operators import POSITION_CN, PROFESSION_CN
from maa_api.services.game.pages import make_page_url

__all__ = ["GamedataCorpusSource"]

#: 配置里的表名 → 文件名。
TABLE_FILES: dict[str, str] = {
    "character": "character_table.json",
    "item": "item_table.json",
    "enemy": "enemy_handbook_table.json",
}

#: 默认表与顺序。
DEFAULT_TABLES: tuple[str, ...] = ("character", "item", "enemy")

#: 道具收录的 ``classifyType``。
ITEM_CLASSES = {"MATERIAL": "材料", "CONSUME": "消耗品"}

#: 敌人级别的中文名；未登记的取值原样写。
ENEMY_LEVELS = {"NORMAL": "普通", "ELITE": "精英", "BOSS": "领袖"}

#: 伤害类型的中文名；未登记的取值原样写。
DAMAGE_TYPES = {
    "PHYSIC": "物理",
    "MAGIC": "法术",
    "HEAL": "治疗",
    "NO_DAMAGE": "不造成伤害",
}

_RICH_TAG = re.compile(r"<[@$][^>]*>")
_RARITY = re.compile(r"([0-9]+)")


class GamedataCorpusSource:
    """拆包实体卡来源；三张表的卡片一次生成、按批交出。"""

    source_id = "gamedata"
    kind = KnowledgeSourceKind.GAMEDATA
    title = "拆包实体卡"
    trusted = False
    license = "版权属于鹰角网络；镜像自 Kengxxiao/ArknightsGameData"
    searchable_by_default = True
    setting_key = "rag.gamedata_enabled"
    full_listing = True
    scheduled_refresh = True

    def __init__(self, client: GamedataClient) -> None:
        self._client = client
        self._pending: dict[str, SourceDocument] = {}

    # -- 配置 -----------------------------------------------------------
    def validate_config(self, config: Mapping[str, Any]) -> dict[str, Any]:
        """``tables`` 必须是非空的已知表子集；重复项按首次出现去重。"""
        values = dict(config or {})
        tables = values.get("tables", list(DEFAULT_TABLES))
        if not isinstance(tables, (list, tuple)) or not all(
            isinstance(item, str) for item in tables
        ):
            raise _invalid("tables", "tables 必须是字符串列表")
        cleaned: list[str] = []
        for table in tables:
            if table not in TABLE_FILES:
                raise _invalid("tables", f"未知的表：{table}")
            if table not in cleaned:
                cleaned.append(table)
        if not cleaned:
            raise _invalid("tables", "tables 不能为空")
        return {"tables": cleaned}

    # -- 清单与正文 -----------------------------------------------------
    async def list_refs(self, config: Mapping[str, Any]) -> list[DocumentRef]:
        """按 ``tables`` 取表、生成卡片并暂存；返回的 version 是卡片 sha 前 16 位。"""
        values = self.validate_config(config)
        self._pending.clear()
        now = utcnow()
        refs: list[DocumentRef] = []
        for table in values["tables"]:
            filename = TABLE_FILES[table]
            fetched = await self._client.corpus_table(filename)
            for external_id, title, text in _table_documents(table, fetched.data):
                version = _digest(text)
                self._pending[external_id] = SourceDocument(
                    external_id=external_id,
                    title=title,
                    text=text,
                    url=make_page_url(_display_name(table, title)),
                    version=version,
                    fetched_at=now,
                    meta={"table": table, "entity_id": external_id.split(":", 1)[1]},
                )
                refs.append(
                    DocumentRef(
                        external_id=external_id, title=title, version=version
                    )
                )
        return refs

    async def fetch(
        self, refs: Sequence[DocumentRef], config: Mapping[str, Any]
    ) -> list[SourceDocument]:
        """弹出请求到的暂存卡片；没被请求的留着（下次 ``list_refs`` 会重置）。"""
        del config
        documents: list[SourceDocument] = []
        for ref in refs:
            document = self._pending.pop(ref.external_id, None)
            if document is not None:
                documents.append(document)
        return documents

    async def prune(self, config: Mapping[str, Any]) -> int:
        """同步成功后的收尾：丢掉本批没被取走的暂存卡片（不删任何文档）。"""
        del config
        self._pending.clear()
        return 0


def _table_documents(
    table: str, data: Any
) -> list[tuple[str, str, str]]:
    """把一张表变成 ``(external_id, 文档标题, 卡片正文)``；结构不符报错。"""
    entries = _entries_of(table, data)
    documents: list[tuple[str, str, str]] = []
    if table == "character":
        for char_id, raw in entries:
            if not isinstance(raw, Mapping) or not str(char_id).startswith("char_"):
                continue
            if bool(raw.get("isNotObtainable")):
                continue
            name = _text(raw.get("name")) or str(char_id)
            documents.append(
                (
                    f"char:{char_id}",
                    f"干员 {name}",
                    _operator_card(raw, name),
                )
            )
    elif table == "item":
        for item_id, raw in entries:
            if not isinstance(raw, Mapping):
                continue
            category = raw.get("classifyType")
            if category not in ITEM_CLASSES:
                continue
            name = _text(raw.get("name")) or str(item_id)
            documents.append(
                (
                    f"item:{item_id}",
                    f"道具 {name}",
                    _item_card(raw, name, ITEM_CLASSES[category]),
                )
            )
    else:
        for enemy_id, raw in entries:
            if not isinstance(raw, Mapping) or bool(raw.get("hideInHandbook")):
                continue
            name = _text(raw.get("name")) or str(enemy_id)
            documents.append(
                (
                    f"enemy:{enemy_id}",
                    f"敌人 {name}",
                    _enemy_card(raw, name),
                )
            )
    return documents


def _entries_of(table: str, data: Any) -> list[tuple[Any, Any]]:
    if not isinstance(data, Mapping):
        raise _invalid_data(f"{TABLE_FILES[table]} 返回的不是对象")
    if table == "character":
        return list(data.items())
    key = "items" if table == "item" else "enemyData"
    inner = data.get(key)
    if not isinstance(inner, Mapping):
        raise _invalid_data(f"{TABLE_FILES[table]} 缺少 {key}")
    return list(inner.items())


def _operator_card(raw: Mapping[str, Any], name: str) -> str:
    appellation = _clean(raw.get("appellation"))
    lines = [f"# {name}" + (f"（{appellation}）" if appellation else "")]
    rarity = _rarity(raw.get("rarity"))
    if rarity:
        lines.append(f"- 稀有度：{rarity}★")
    profession = _mapped(raw.get("profession"), PROFESSION_CN)
    branch = _clean(raw.get("subProfessionId"))
    parts = []
    if profession:
        parts.append(f"职业：{profession}")
    if branch:
        parts.append(f"分支：{branch}")
    if parts:
        lines.append("- " + "；".join(parts))
    position = _mapped(raw.get("position"), POSITION_CN)
    if position:
        lines.append(f"- 位置：{position}")
    tags = raw.get("tagList")
    tag_text = "、".join(
        _clean(tag) for tag in tags if _clean(tag)
    ) if isinstance(tags, list) else ""
    if tag_text:
        lines.append(f"- 标签：{tag_text}")
    for label, key in (
        ("特性", "description"),
        ("获取方式", "itemObtainApproach"),
        ("简介", "itemUsage"),
    ):
        value = _clean(raw.get(key))
        if value:
            lines.append(f"- {label}：{value}")
    return "\n".join(lines)


def _item_card(raw: Mapping[str, Any], name: str, category: str) -> str:
    lines = [f"# {name}"]
    rarity = _rarity(raw.get("rarity"))
    if rarity:
        lines.append(f"- 稀有度：{rarity}★")
    lines.append(f"- 分类：{category}")
    for label, key in (
        ("用途", "usage"),
        ("说明", "description"),
        ("获取方式", "obtainApproach"),
    ):
        value = _clean(raw.get(key))
        if value:
            lines.append(f"- {label}：{value}")
    return "\n".join(lines)


def _enemy_card(raw: Mapping[str, Any], name: str) -> str:
    enemy_index = _clean(raw.get("enemyIndex"))
    lines = [f"# {name}" + (f"（{enemy_index}）" if enemy_index else "")]
    level = _mapped(raw.get("enemyLevel"), ENEMY_LEVELS)
    if level:
        lines.append(f"- 级别：{level}")
    damage = raw.get("damageType")
    values = [damage] if isinstance(damage, str) else list(damage or ())
    damage_types = [
        _mapped(value, DAMAGE_TYPES) for value in values if isinstance(value, str)
    ]
    damage_text = "、".join(item for item in damage_types if item)
    if damage_text:
        lines.append(f"- 伤害类型：{damage_text}")
    attack = _clean(raw.get("attackType"))
    if attack:
        lines.append(f"- 攻击方式：{attack}")
    abilities = raw.get("abilityList")
    ability_texts = []
    if isinstance(abilities, list):
        for ability in abilities:
            if isinstance(ability, Mapping):
                text = _clean(ability.get("text"))
                if text:
                    ability_texts.append(text)
    if ability_texts:
        lines.append("- 能力：" + "；".join(ability_texts))
    description = _clean(raw.get("description"))
    if description:
        lines.append(f"- 描述：{description}")
    return "\n".join(lines)


def _clean(value: Any) -> str:
    """去富文本标记、字面量 ``\\n`` 转换行，并去掉两端空白。"""
    if value is None:
        return ""
    text = str(value)
    text = _RICH_TAG.sub("", text).replace("</>", "")
    return text.replace("\\n", "\n").strip()


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _mapped(value: Any, mapping: Mapping[str, str]) -> str:
    text = _clean(value)
    return mapping.get(text, text) if text else ""


def _rarity(value: Any) -> str:
    match = _RARITY.search(str(value or ""))
    return match.group(1) if match else ""


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _display_name(table: str, title: str) -> str:
    """文档标题形如 ``干员 名``；URL 用名字部分。"""
    prefix = {"character": "干员 ", "item": "道具 ", "enemy": "敌人 "}[table]
    return title[len(prefix) :] if title.startswith(prefix) else title


def _invalid(field: str, reason: str) -> AppError:
    return AppError(
        ErrorCode.VALIDATION_ERROR,
        f"拆包实体卡来源的 {field} 不合法",
        {"field": field, "reason": reason},
    )


def _invalid_data(reason: str) -> AppError:
    """上游表结构不符（不是用户配置错），按外部响应无效处理。"""
    return AppError(
        ErrorCode.EXTERNAL_RESPONSE_INVALID,
        "拆包语料表结构不符",
        {"kind": "gamedata", "reason": reason},
    )
