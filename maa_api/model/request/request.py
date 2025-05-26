from pydantic import BaseModel
from typing import Optional

from maa_api.model.core.task import Task, StartUpTask, CloseDownTask, FightTask, RecruitTask, InfrastTask, MallTask, AwardTask, RoguelikeTask, ReclamationTask
from maa_api.exception.response_exception import ResponseException

class TaskRequest(BaseModel):
    name: str

    # 通用参数
    enable: Optional[bool] = None
    times: Optional[int] = None

    # StartUpTask 和 CloseDownTask 参数
    client_type: Optional[str] = None
    account_name: Optional[str] = None
    start_game_enabled: Optional[bool] = None

    # Fight 参数
    stage: Optional[str] = None
    medicine: Optional[int] = None
    expiring_medicine: Optional[int] = None
    stone: Optional[int] = None
    times: Optional[int] = None
    series: Optional[int] = None
    drops: Optional[dict[str, int]] = None
    report_to_penguin: Optional[bool] = None
    penguin_id: Optional[str] = None
    server: Optional[str] = None
    DrGrandet: Optional[bool] = None

    # Recruit 参数
    refresh: Optional[bool] = None
    select: Optional[list[int]] = None
    confirm: Optional[list[int]] = None
    first_tags: Optional[list[str]] = None
    extra_tags_mode: Optional[int] = None
    set_time: Optional[bool] = None
    expedite: Optional[bool] = None
    expedite_times: Optional[int] = None
    skip_robot: Optional[bool] = None
    recruitment_time: Optional[dict[int, int]] = None
    report_to_yituliu: Optional[bool] = None
    yituliu_id: Optional[str] = None

    # InfrastTask 参数
    mode: Optional[int] = 2000
    facility: Optional[list[str]] = None
    drones: Optional[str] = None
    threshold: Optional[float] = None
    replenish: Optional[bool] = None
    dorm_notstationed_enabled: Optional[bool] = None
    dorm_trust_enabled: Optional[bool] = None
    failename: Optional[str] = None
    plan_index: Optional[int] = None

    # Mall 参数
    shopping: Optional[bool] = None
    buy_first: Optional[list[str]] = None
    blacklist: Optional[list[str]] = None
    force_shopping_if_credit_full: Optional[bool] = None
    only_buy_discount: Optional[bool] = None
    reserve_max_credit: Optional[bool] = None

    # Award 参数
    award: Optional[bool] = None
    mail: Optional[bool] = None
    recruit: Optional[bool] = None
    orundum: Optional[bool] = None
    mining: Optional[bool] = None
    specialaccess: Optional[bool] = None

    # Roguelike 参数
    theme: Optional[str] = None
    squad: Optional[str] = None
    roles: Optional[str] = None
    core_char: Optional[str] = None
    use_support: Optional[bool] = None
    use_nonfriend_support: Optional[bool] = None
    starts_count: Optional[int] = None
    difficulty: Optional[int] = None
    stop_at_final_boss: Optional[bool] = None
    investment_enabled: Optional[bool] = None
    investments_count: Optional[int] = None
    stop_when_investment_full: Optional[bool] = None
    start_with_elite_two: Optional[bool] = None
    only_start_with_elite_two: Optional[bool] = None
    refresh_trader_with_dice: Optional[bool] = None
    first_floor_foldartal: Optional[str] = None
    start_foldartal_list: Optional[list[str]] = None
    use_foldartal: Optional[bool] = None
    check_collapsal_paradigms: Optional[bool] = None
    double_check_collapsal_paradigms: Optional[bool] = None
    expected_collapsal_paradigms: Optional[list[str]] = None

    # Reclamation 参数
    tools_to_craft: Optional[list[str]] = None,
    increment_mode: Optional[int] = None,
    num_craft_batches: Optional[int] = None

    def to_task(self) -> Task:
        if not self.name:
            raise ResponseException(message="任务名不能为空")

        if self.name == "StartUp":
            return StartUpTask(
                enable=self.enable,
                client_type=self.client_type,
                start_game_enabled=self.start_game_enabled,
                account_name=self.account_name
            )

        if self.name == "CloseDown":
            return CloseDownTask(
                enable=self.enable,
                client_type=self.client_type
            )

        if self.name == "Fight":
            return FightTask(
                enable=self.enable,
                stage=self.stage,
                medicine=self.medicine,
                expiring_medicine=self.expiring_medicine,
                stone=self.stone,
                times=self.times,
                series=self.series,
                drops=self.drops,
                report_to_penguin=self.report_to_penguin,
                penguin_id=self.penguin_id,
                server=self.server,
                client_type=self.client_type,
                DrGrandet=self.DrGrandet
            )

        if self.name == "Recruit":
            return RecruitTask(
                enable=self.enable,
                refresh=self.refresh,
                select=self.select,
                confirm=self.confirm,
                first_tags=self.first_tags,
                extra_tags_mode=self.extra_tags_mode,
                times=self.times,
                set_time=self.set_time,
                expedite=self.expedite,
                expedite_times=self.expedite_times,
                skip_robot=self.skip_robot,
                recruitment_time=self.recruitment_time,
                report_to_penguin=self.report_to_penguin,
                penguin_id=self.penguin_id,
                report_to_yituliu=self.report_to_yituliu,
                yituliu_id=self.yituliu_id,
                server=self.server
            )

        if self.name == "Infrast":
            return InfrastTask(
                enable=self.enable,
                mode=self.mode,
                facility=self.facility,
                drones=self.drones,
                threshold=self.threshold,
                replenish=self.replenish,
                dorm_notstationed_enabled=self.dorm_notstationed_enabled,
                dorm_trust_enabled=self.dorm_trust_enabled,
                failename=self.failename,
                plan_index=self.plan_index
            )

        if self.name == "Mall":
            return MallTask(
                enable=self.enable,
                shopping=self.shopping,
                buy_first=self.buy_first,
                blacklist=self.blacklist,
                force_shopping_if_credit_full=self.force_shopping_if_credit_full,
                only_buy_discount=self.only_buy_discount,
                reserve_max_credit=self.reserve_max_credit
            )

        if self.name == "Award":
            return AwardTask(
                enable=self.enable,
                award=self.award,
                mail=self.mail,
                recruit=self.recruit,
                orundum=self.orundum,
                mining=self.mining,
                specialaccess=self.specialaccess
            )

        if self.name == "Roguelike":
            return RoguelikeTask(
                enable=self.enable,
                theme=self.theme,
                mode=self.mode,
                squad=self.squad,
                roles=self.roles,
                core_char=self.core_char,
                use_support=self.use_support,
                use_nonfriend_support=self.use_nonfriend_support,
                starts_count=self.starts_count,
                difficulty=self.difficulty,
                stop_at_final_boss=self.stop_at_final_boss,
                investment_enabled=self.investment_enabled,
                investments_count=self.investments_count,
                stop_when_investment_full=self.stop_when_investment_full,
                start_with_elite_two=self.start_with_elite_two,
                only_start_with_elite_two=self.only_start_with_elite_two,
                refresh_trader_with_dice=self.refresh_trader_with_dice,
                first_floor_foldartal=self.first_floor_foldartal,
                start_foldartal_list=self.start_foldartal_list,
                use_foldartal=self.use_foldartal,
                check_collapsal_paradigms=self.check_collapsal_paradigms,
                double_check_collapsal_paradigms=self.double_check_collapsal_paradigms,
                expected_collapsal_paradigms=self.expected_collapsal_paradigms
            )
        
        if self.name == "Reclamation":
            return ReclamationTask(
                enable=self.enable,
                theme=self.theme,
                mode=self.mode,
                tools_to_craft=self.tools_to_craft,
                increment_mode=self.increment_mode,
                num_craft_batches=self.num_craft_batches
            )

        raise ResponseException(message=f"未知的任务名: {self.name}")
