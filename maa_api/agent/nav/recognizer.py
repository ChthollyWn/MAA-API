"""界面识别：dHash 感知哈希、样本近邻与视觉分类/定位（M86 spec §4.5.2）。

识别是**只读**的：:meth:`Recognizer.recognize` 不写库、不写文件、不加样本；学习由
``Navigator`` 在 ``navigate`` / ``remember`` 里显式调用 ``add_exemplar``。

匹配顺序（spec §4.5.2）：

1. 每个节点的每个样本按该节点 ``recognizer["rois"]`` 的签名比距离；每个 ROI 都不
   超过 ``max_distance`` 才算这个样本匹配，节点得分取匹配样本里平均距离最小的那个。
2. 得分最小的节点 A；若另有节点 B 也匹配且 ``B.score - A.score < AMBIGUITY_MARGIN``
   就算拿不准，交给视觉分类。例外：A 与 B 里只有一个不是 ``generic`` 时取非 generic
   的那个。
3. 视觉分类只在 ``allow_vision=True`` 且视觉客户端可用时进行；候选是得分最小的
   ``VISION_CANDIDATES`` 个节点加上 ``home``，答案必须在候选里且置信度
   ≥ ``VISION_MIN_CONFIDENCE``。
4. 其余情况 ``node_id=None``、``method="none"``。
"""

from __future__ import annotations

import base64
import io
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from PIL import Image

from maa_api.agent.llm import schemas as _schemas  # noqa: F401 - 触发视觉 schema 注册
from maa_api.agent.llm.structured import complete_structured
from maa_api.db.models import NavNode
from maa_api.domain.enums import ModelRole
from maa_api.domain.errors import AppError

__all__ = [
    "AMBIGUITY_MARGIN",
    "CLASSIFY_PROMPT",
    "DEFAULT_MAX_DISTANCE",
    "DEFAULT_ROIS",
    "HASH_SIZE",
    "LOCATE_MIN_CONFIDENCE",
    "LOCATE_PROMPT",
    "UNKNOWN_RETRY_MS",
    "VISION_CANDIDATES",
    "VISION_MIN_CONFIDENCE",
    "ModelVisionClient",
    "Recognition",
    "Recognizer",
    "VisionClient",
    "dhash",
    "distances",
    "signature",
]

logger = logging.getLogger(__name__)

#: dHash 的长宽：8×8 的相邻列比较，共 64 位。
HASH_SIZE = 8
#: 节点没写 ``recognizer.rois`` 时的默认 ROI：整屏。
DEFAULT_ROIS: tuple[tuple[float, float, float, float], ...] = ((0.0, 0.0, 1.0, 1.0),)
#: 每个 ROI 的汉明距离上限（节点 ``recognizer.max_distance`` 的默认值）。
DEFAULT_MAX_DISTANCE = 10
#: 第一名与另一个节点的平均距离差小于它就算拿不准（spec §4.5.2 第 2 步）。
AMBIGUITY_MARGIN = 2.0
#: 视觉分类结果被采纳所需的最低置信度。
VISION_MIN_CONFIDENCE = 0.7
#: 视觉定位结果被采纳所需的最低置信度。
LOCATE_MIN_CONFIDENCE = 0.6
#: 视觉分类最多给模型几个候选界面。
VISION_CANDIDATES = 8
#: 认出未知界面后等待再认一次的时长（导航执行器用）。
UNKNOWN_RETRY_MS = 1500

#: 隐式的「回主界面」目标；种子节点固定用这个 id（spec §4.5.6）。
_HOME_NODE_ID = "home"
#: 送给视觉模型的截图尺寸与 JPEG 质量（spec §4.5.2）。
_VISION_IMAGE_SIZE = (1280, 720)
_VISION_JPEG_QUALITY = 80

#: 分类提示词：候选行格式 ``id｜中文名｜特征描述``（spec §4.5.2 逐字）。
CLASSIFY_PROMPT = (
    "下面是明日方舟游戏的一张截图。从候选界面里选出它属于哪一个；都不像就给 null。\n"
    "只看界面结构与固定的按钮和标题，不看角色立绘与背景。\n"
    "候选：\n"
    "{candidates}"
)
#: 定位提示词（spec §4.5.2 逐字；``query`` 是动作里给视觉模型的中文描述）。
LOCATE_PROMPT = (
    "下面是明日方舟游戏的一张截图。找到「{query}」的位置，给出它中心点的归一化坐标"
    "（左上角是 0,0，右下角是 1,1）。找不到就 found=false。"
)


def dhash(image: Image.Image, roi: Sequence[float]) -> int:
    """按归一化 ROI 算 64 位 dHash。

    ROI 是 ``[x, y, w, h]``；裁剪框 ``(round(x·W), round(y·H), round((x+w)·W),
    round((y+h)·H))``，左闭右开、越界裁剪、退化矩形至少 1px。转灰度后缩到 9×8，
    逐行比较相邻两列，左大于右记 1（spec §4.5.2）。
    """
    width, height = image.size
    x, y, roi_width, roi_height = roi
    left = min(max(round(x * width), 0), width - 1)
    top = min(max(round(y * height), 0), height - 1)
    right = min(max(round((x + roi_width) * width), left + 1), width)
    bottom = min(max(round((y + roi_height) * height), top + 1), height)
    region = (
        image.convert("L")
        .crop((left, top, right, bottom))
        .resize((HASH_SIZE + 1, HASH_SIZE), Image.Resampling.BILINEAR)
    )
    pixels = region.tobytes()
    bits = 0
    stride = HASH_SIZE + 1
    for row in range(HASH_SIZE):
        offset = row * stride
        for column in range(HASH_SIZE):
            bits = (bits << 1) | (
                1 if pixels[offset + column] > pixels[offset + column + 1] else 0
            )
    return bits


def signature(image: Image.Image, rois: Sequence[Sequence[float]]) -> str:
    """各 ROI 的 ``f"{bits:016x}"`` 用 ``:`` 连接；ROI 顺序就是样本 ``phash`` 的顺序。"""
    return ":".join(f"{dhash(image, roi):016x}" for roi in rois)


def distances(sig_a: str, sig_b: str) -> list[int] | None:
    """逐段异或后数 1 的个数；ROI 数不同（段数不同）返回 ``None``。"""
    segments_a = sig_a.split(":")
    segments_b = sig_b.split(":")
    if len(segments_a) != len(segments_b):
        return None
    return [
        (int(segment_a, 16) ^ int(segment_b, 16)).bit_count()
        for segment_a, segment_b in zip(segments_a, segments_b, strict=True)
    ]


@dataclass(frozen=True, slots=True)
class Recognition:
    """一次识别结论；``candidates`` 是平均距离升序的前 3 个 ``(节点 id, 平均距离)``。

    ``candidates`` 与哈希是否过阈值无关（spec §4.3.1），供 ``where_am_i`` 展示诊断。
    """

    node_id: str | None
    method: Literal["hash", "vision", "none"]
    distance: float | None
    confidence: float
    candidates: tuple[tuple[str, float], ...]


class VisionClient(Protocol):
    """视觉客户端：可用性、界面分类与按描述定位。"""

    async def available(self) -> bool: ...

    async def classify(
        self, image: Image.Image, candidates: Sequence[NavNode]
    ) -> tuple[str | None, float, str] | None: ...

    async def locate(
        self, image: Image.Image, query: str
    ) -> tuple[float, float, float] | None: ...


def _node_rois(node: NavNode) -> tuple[tuple[float, float, float, float], ...]:
    """节点 ``recognizer.rois``；缺失或形状不对时退回整屏。"""
    raw = node.recognizer.get("rois") if isinstance(node.recognizer, dict) else None
    if not isinstance(raw, (list, tuple)) or not raw:
        return DEFAULT_ROIS
    rois: list[tuple[float, float, float, float]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 4:
            return DEFAULT_ROIS
        rois.append((float(item[0]), float(item[1]), float(item[2]), float(item[3])))
    return tuple(rois)


def _node_max_distance(node: NavNode) -> int:
    """节点 ``recognizer.max_distance``；缺失或非法时用默认值（至少 1）。"""
    raw = node.recognizer.get("max_distance", DEFAULT_MAX_DISTANCE) if isinstance(
        node.recognizer, dict
    ) else DEFAULT_MAX_DISTANCE
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_MAX_DISTANCE


def _is_generic(node: NavNode) -> bool:
    return (
        isinstance(node.recognizer, dict) and node.recognizer.get("generic") is True
    )


def _node_description(node: NavNode) -> str:
    """候选行第三列：``recognizer.description``（spec §4.3.2 的模型定义）。"""
    raw = node.recognizer.get("description") if isinstance(node.recognizer, dict) else None
    return str(raw or "").strip()


def _candidate_line(node: NavNode) -> str:
    return f"{node.id}｜{node.title}｜{_node_description(node)}"


def _image_data_url(image: Image.Image) -> str:
    """缩到 1280×720 的 JPEG（质量 80）并编码成 data URL。"""
    converted = image.convert("RGB")
    if converted.size != _VISION_IMAGE_SIZE:
        converted = converted.resize(_VISION_IMAGE_SIZE, Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    converted.save(buffer, format="JPEG", quality=_VISION_JPEG_QUALITY)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _vision_messages(prompt: str, image: Image.Image) -> list[dict[str, Any]]:
    """一条 user 多模态消息：文字在前、截图 data URL 在后。"""
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": _image_data_url(image)}},
            ],
        }
    ]


class ModelVisionClient:
    """用 M67 的 ``vision`` 角色做分类与定位（spec §4.2、§4.5.2）。

    依赖固定从 ``app_state.model_router`` / ``app_state.cost_ledger`` 取；任一为空时
    ``available()`` 为 False、``classify``/``locate`` 返回 None。``available()`` 每次
    现解析一次 vision 档案，不跨调用缓存。结构化调用失败（``STRUCTURED_OUTPUT_INVALID``
    或模型请求错误）一律当作没有结论，只记 warning，不抛给调用方。
    """

    def __init__(
        self,
        app_state: Any,
        *,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self._app_state = app_state
        self._run_id = run_id
        self._session_id = session_id

    def _dependencies(self) -> tuple[Any, Any]:
        return (
            getattr(self._app_state, "model_router", None),
            getattr(self._app_state, "cost_ledger", None),
        )

    async def available(self) -> bool:
        router, ledger = self._dependencies()
        if router is None or ledger is None:
            return False
        try:
            profile = await router.resolve(ModelRole.VISION.value)
        except AppError:
            return False
        except Exception:  # noqa: BLE001 - 档案解析失败按不可用处理
            logger.warning("解析视觉档案失败，按不可用处理", exc_info=True)
            return False
        return profile.supports_vision is True

    async def classify(
        self, image: Image.Image, candidates: Sequence[NavNode]
    ) -> tuple[str | None, float, str] | None:
        router, ledger = self._dependencies()
        if router is None or ledger is None:
            return None
        prompt = CLASSIFY_PROMPT.format(
            candidates="\n".join(_candidate_line(node) for node in candidates)
        )
        try:
            result = await complete_structured(
                router=router,
                ledger=ledger,
                role=ModelRole.VISION.value,
                schema_name="nav_classification",
                messages=_vision_messages(prompt, image),
                agent_name="navigator",
                run_id=self._run_id,
                session_id=self._session_id,
            )
            value = result.value
            return (value.node_id, float(value.confidence), str(value.reason))
        except Exception:  # noqa: BLE001 - 视觉失败只当没有结论
            logger.warning("视觉分类调用失败，按没有结论处理", exc_info=True)
            return None

    async def locate(
        self, image: Image.Image, query: str
    ) -> tuple[float, float, float] | None:
        router, ledger = self._dependencies()
        if router is None or ledger is None:
            return None
        prompt = LOCATE_PROMPT.format(query=query)
        try:
            result = await complete_structured(
                router=router,
                ledger=ledger,
                role=ModelRole.VISION.value,
                schema_name="nav_locate",
                messages=_vision_messages(prompt, image),
                agent_name="navigator",
                run_id=self._run_id,
                session_id=self._session_id,
            )
            value = result.value
            if not value.found:
                return None
            return (float(value.x), float(value.y), float(value.confidence))
        except Exception:  # noqa: BLE001 - 视觉失败只当没有结论
            logger.warning("视觉定位调用失败，按没有结论处理", exc_info=True)
            return None


class Recognizer:
    """只读的界面识别器：先比样本哈希，拿不准再问视觉模型。"""

    def __init__(self, graph: Any, vision: VisionClient) -> None:
        self._graph = graph
        self._vision = vision

    async def recognize(
        self, image: Image.Image, *, allow_vision: bool = True
    ) -> Recognition:
        nodes, _edges = await self._graph.graph()
        matches, ranked = _score_nodes(nodes, image)
        candidates = tuple((node.id, score) for node, score in ranked[:3])
        matched = _pick_hash(matches)
        if matched is not None:
            node, score = matched
            confidence = 1.0 - score / (2.0 * _node_max_distance(node))
            return Recognition(
                node_id=node.id,
                method="hash",
                distance=float(score),
                confidence=max(0.0, min(1.0, confidence)),
                candidates=candidates,
            )

        classified = await self._classify(image, nodes, ranked, allow_vision=allow_vision)
        if classified is not None:
            node_id, confidence = classified
            return Recognition(
                node_id=node_id,
                method="vision",
                distance=None,
                confidence=confidence,
                candidates=candidates,
            )
        return Recognition(
            node_id=None, method="none", distance=None, confidence=0.0, candidates=candidates
        )

    async def _classify(
        self,
        image: Image.Image,
        nodes: Sequence[NavNode],
        ranked: Sequence[tuple[NavNode, float]],
        *,
        allow_vision: bool,
    ) -> tuple[str, float] | None:
        if not allow_vision:
            return None
        try:
            available = await self._vision.available()
        except Exception:  # noqa: BLE001 - 视觉不可用不能打断识别
            logger.warning("视觉可用性检查失败，按不可用处理", exc_info=True)
            return None
        if not available:
            return None
        candidates = await self._vision_candidates(nodes, ranked)
        if not candidates:
            return None
        try:
            result = await self._vision.classify(image, candidates)
        except Exception:  # noqa: BLE001 - 视觉失败只当没有结论
            logger.warning("视觉分类失败，按未认出处理", exc_info=True)
            return None
        if result is None:
            return None
        node_id, confidence, _reason = result
        confidence = float(confidence)
        if node_id is None or confidence < VISION_MIN_CONFIDENCE:
            return None
        if node_id not in {node.id for node in candidates}:
            return None
        return node_id, confidence

    async def _vision_candidates(
        self,
        nodes: Sequence[NavNode],
        ranked: Sequence[tuple[NavNode, float]],
    ) -> list[NavNode]:
        """平均距离最小的 ``VISION_CANDIDATES`` 个节点，再保证含 ``home``。"""
        chosen = [node for node, _score in ranked[:VISION_CANDIDATES]]
        chosen_ids = {node.id for node in chosen}
        home = next((node for node in nodes if node.id == _HOME_NODE_ID), None)
        if home is None:
            home = await self._graph.get_node(_HOME_NODE_ID)
        if home is not None and home.id not in chosen_ids:
            chosen.append(home)
        return chosen


def _score_nodes(
    nodes: Sequence[NavNode], image: Image.Image
) -> tuple[list[tuple[NavNode, float]], list[tuple[NavNode, float]]]:
    """给每个节点算两种得分，均按平均距离升序（稳定排序，同分保留 graph() 顺序）。

    - ``matches``：只含「每个 ROI 都 ≤ max_distance」的样本，用于哈希判定与歧义裁决；
    - ``ranked``：含所有能算出距离的样本（不论是否过阈值），用于诊断候选与视觉候选。
      §4.3.1 的 ``candidates`` 与判定是否过阈值无关，所以两者必须分开算。
    """
    matches: list[tuple[NavNode, float]] = []
    ranked: list[tuple[NavNode, float]] = []
    for node in nodes:
        rois = _node_rois(node)
        node_signature = signature(image, rois)
        max_distance = _node_max_distance(node)
        best_match: float | None = None
        best_any: float | None = None
        for exemplar in node.exemplars or []:
            phash = exemplar.get("phash") if isinstance(exemplar, dict) else None
            if not isinstance(phash, str) or not phash:
                continue
            distance = distances(node_signature, phash)
            if distance is None:
                continue
            score = sum(distance) / len(distance)
            if best_any is None or score < best_any:
                best_any = score
            if any(value > max_distance for value in distance):
                continue
            if best_match is None or score < best_match:
                best_match = score
        if best_any is not None:
            ranked.append((node, best_any))
        if best_match is not None:
            matches.append((node, best_match))
    matches.sort(key=lambda pair: pair[1])
    ranked.sort(key=lambda pair: pair[1])
    return matches, ranked


def _pick_hash(
    matches: Sequence[tuple[NavNode, float]],
) -> tuple[NavNode, float] | None:
    """按 §4.5.2 第 2 步裁决歧义；返回 None 表示该交给视觉分类。"""
    if not matches:
        return None
    best_node, best_score = matches[0]
    contenders = [(best_node, best_score)]
    for node, score in matches[1:]:
        if score - best_score >= AMBIGUITY_MARGIN:
            break
        contenders.append((node, score))
    if len(contenders) == 1:
        return contenders[0]
    # generic 例外：候选里只有一个不是 generic 时取它，不算拿不准。
    specific = [pair for pair in contenders if not _is_generic(pair[0])]
    if len(specific) == 1:
        return specific[0]
    return None
