"""角色到模型档案的路由（M67 规格 §4.2.4）。

九个角色（:class:`maa_api.domain.enums.ModelRole`）各由 ``llm.role_<role>`` 指向
一个模型档案；设置为空时用 ``default`` 档案（``embedding`` 例外：空表示「没有远端
向量」）。``legacy_chat`` / ``legacy_incident`` 不走档案路由：继续按会话快照里的
``model``、``base_url`` 调用，布局与能力探查保持 v2 行为。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Literal

from maa_api.agent.llm.client import OpenAICompatibleClient
from maa_api.agent.llm.profiles import (
    DEFAULT_PROFILE_NAME,
    ModelProfileService,
    ResolvedProfile,
)
from maa_api.agent.specs.schema import LEGACY_SPEC_NAMES, AgentSpec
from maa_api.domain.enums import ModelRole
from maa_api.domain.errors import AppError, ErrorCode
from maa_api.settings import Settings, get_settings

__all__ = [
    "ROLE_SETTING_PREFIX",
    "ClientHandle",
    "ModelRouter",
    "RoleBinding",
]

ROLE_SETTING_PREFIX = "llm.role_"


@dataclass(frozen=True, slots=True)
class RoleBinding:
    """一个角色当前指向的档案（设置值、解析出的模型与来源）。"""

    role: str
    profile: str | None
    model: str | None
    #: ``setting`` = 设置里显式写了档案名；``default`` = 空设置回落到默认口径。
    source: Literal["setting", "default"]


@dataclass(slots=True)
class ClientHandle:
    """一次模型调用要用的客户端；``owned`` 为真时由调用方负责关闭。"""

    client: Any
    owned: bool

    async def aclose(self) -> None:
        if self.owned:
            await self.client.aclose()


class ModelRouter:
    """按角色解析档案、按档案创建客户端。"""

    def __init__(
        self,
        profiles: ModelProfileService,
        *,
        settings_provider: Callable[[], Settings] = get_settings,
        client_factory_provider: Callable[[], Callable[[Settings], Any] | None] = lambda: None,
    ) -> None:
        self._profiles = profiles
        self._settings_provider = settings_provider
        self._client_factory_provider = client_factory_provider

    def _current_settings(self) -> Settings:
        return self._settings_provider()

    def _role_setting(self, role: str | ModelRole) -> str:
        settings = self._current_settings()
        return str(getattr(settings.llm, f"role_{role}", "") or "").strip()

    async def resolve(self, role: str) -> ResolvedProfile:
        """角色 → 档案；空设置回落 ``default``，类型不符按「不存在」处理。"""
        setting = self._role_setting(role)
        if not setting:
            if str(role) == ModelRole.EMBEDDING.value:
                # 空 = 不使用远端向量，不是「回落到 default」。路由层据此返回 null。
                raise AppError(
                    ErrorCode.MODEL_PROFILE_NOT_FOUND,
                    "没有配置向量模型档案",
                    {"role": ModelRole.EMBEDDING.value},
                )
            return await self._profiles.ensure_probed(
                await self._profiles.resolve(DEFAULT_PROFILE_NAME)
            )

        profile = await self._profiles.resolve(setting)
        wants_embedding = str(role) == ModelRole.EMBEDDING.value
        if wants_embedding != (profile.kind == "embedding"):
            raise AppError(
                ErrorCode.MODEL_PROFILE_NOT_FOUND,
                f"模型档案「{setting}」的类型与角色 {role} 不符",
                {"reason": "kind_mismatch"},
            )
        if wants_embedding:
            return profile
        return await self._profiles.ensure_probed(profile)

    async def resolve_for_spec(
        self,
        spec: AgentSpec,
        *,
        session_model: str | None = None,
        session_base_url: str | None = None,
    ) -> ResolvedProfile:
        """按代理定义取档案；legacy 定义用会话快照里的连接目标，且不探查。"""
        if spec.name not in LEGACY_SPEC_NAMES:
            return await self.resolve(spec.model_role)

        profile = await self._profiles.resolve(DEFAULT_PROFILE_NAME)
        model = (session_model or "").strip()
        if not model or model.casefold() == "unconfigured":
            return profile
        base_url = (session_base_url or "").strip() or profile.base_url
        return replace(profile, model=model, base_url=base_url)

    def client_for(
        self, profile: ResolvedProfile, *, timeout_seconds: float | None = None
    ) -> ClientHandle:
        """为档案建客户端；注入了工厂时用工厂，并交出所有权。"""
        factory = self._client_factory_provider()
        if factory is not None:
            settings = self._current_settings()
            settings = settings.model_copy(
                update={
                    "llm": settings.llm.model_copy(
                        update={
                            "base_url": profile.base_url,
                            "api_key": profile.api_key,
                            "model": profile.model,
                        }
                    )
                }
            )
            return ClientHandle(client=factory(settings), owned=False)
        options: dict[str, Any] = {}
        if timeout_seconds is not None:
            options["timeout_seconds"] = timeout_seconds
        return ClientHandle(
            client=OpenAICompatibleClient(
                base_url=profile.base_url, api_key=profile.api_key, **options
            ),
            owned=True,
        )

    async def list_roles(self) -> list[RoleBinding]:
        """九个角色各一行，顺序与 :class:`ModelRole` 一致。"""
        settings = self._current_settings()
        models = {
            view.name: view.model
            for view in await self._profiles.list_views()
        }
        bindings: list[RoleBinding] = []
        for role in ModelRole:
            setting = str(getattr(settings.llm, f"role_{role}", "") or "").strip()
            if setting:
                bindings.append(
                    RoleBinding(
                        role=str(role),
                        profile=setting,
                        model=models.get(setting),
                        source="setting",
                    )
                )
            elif str(role) == ModelRole.EMBEDDING.value:
                bindings.append(
                    RoleBinding(
                        role=str(role),
                        profile=None,
                        model=None,
                        source="default",
                    )
                )
            else:
                bindings.append(
                    RoleBinding(
                        role=str(role),
                        profile=DEFAULT_PROFILE_NAME,
                        model=models.get(DEFAULT_PROFILE_NAME),
                        source="default",
                    )
                )
        return bindings
