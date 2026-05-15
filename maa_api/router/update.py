import platform
import os
import pathlib
import shutil
from datetime import datetime
from fastapi import APIRouter, Depends, BackgroundTasks

from maa_api.dependence.auth import token_auth
from maa_api.service.update_service import GameUpdateService
from maa_api.config.config import Config, LIB_PATH
from maa_api.model.request.response import Response
from maa_api.model.util.updater import Updater
from maa_api.model.util.utils import Version
from maa_api.model.core.scheduler import task_scheduler
from maa_api.model.core.asst_manager import AssistManager
from maa_api.log import logger

router = APIRouter()

def get_maa_path():
    system = platform.system()
    maa_core_path = Config.get_config('app', 'maa_core_path')
    if not maa_core_path:
        maa_core_path = LIB_PATH / 'maa' / system
    return pathlib.Path(os.path.expanduser(maa_core_path)).resolve()

def do_update_game():
    try:
        adb_address = Config.get_config("adb", "address", "127.0.0.1:5555")
        client_type = Config.get_config("app", "game_client_type", "Official")
        svc = GameUpdateService(adb_address=adb_address, client_type=client_type)
        svc.update_game()
    except Exception as e:
        logger.error(f"游戏更新失败: {e}")

def do_update_maa():
    try:
        path = get_maa_path()
        updater = Updater(path, Version.Stable)
        updater.update()
        
        if task_scheduler.is_running():
            task_scheduler.stop()
            logger.info("MAA流水线已挂起，准备热重载...")
            
        task_scheduler.asst = AssistManager(task_scheduler.callback_handler).load_asst(check_update=False)
        logger.info("MAA 核心库更新及重载完成！")
    except Exception as e:
        logger.error(f"MAA热重载失败: {e}")

def do_update_resource():
    try:
        path = get_maa_path()
        logger.info("开始获取并加载最新版本活动资源...")
        ota_tasks_url = 'https://api.maa.plus/MaaAssistantArknights/api/resource/tasks.json'
        ota_tasks_path = path / 'cache' / 'resource' / 'tasks.json'
        ota_tasks_path.parent.mkdir(parents=True, exist_ok=True)
        
        from maa_api.model.util.utils import HttpUtils
        resp = HttpUtils.get(ota_tasks_url)
        resp.raise_for_status()

        with open(ota_tasks_path, 'w', encoding='utf-8') as f:
            f.write(resp.text)
            
        logger.info("版本活动资源文件落盘成功，准备重载环境配置...")
        from maa_api.model.core.asst import Asst
        Asst.load(path=path, incremental_path=path / 'cache')
        # 如果当前正处于运行状态可能需要暂停/释放引擎重新加载
        if task_scheduler.is_running():
            task_scheduler.stop()
            task_scheduler.asst = AssistManager(task_scheduler.callback_handler).load_asst(check_update=False)

        logger.info("MAA 活动资源增量热拉取及装载完毕！")
    except Exception as e:
        logger.error(f"MAA活动资源加载失败: {e}")

def do_reinstall_maa():
    try:
        path = get_maa_path()
        logger.info(f"开始重装 MAA 内核，将清空目录: {path}")
        if task_scheduler.is_running():
            task_scheduler.stop()
            
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
        
        updater = Updater(path, Version.Stable)
        updater.update()
        
        task_scheduler.asst = AssistManager(task_scheduler.callback_handler).load_asst(check_update=False)
        logger.info("MAA 内核重装并重载完成！")
    except Exception as e:
        logger.error(f"MAA内核重装失败: {e}")


@router.get("/api/update/versions", dependencies=[Depends(token_auth)])
async def get_update_versions(client_type: str = "Bilibili"):
    """获取各包的版本和时间信息"""
    from maa_api.model.util.apk_info import get_installed_apk_info, get_latest_apk_info
    
    res = {
        "game": { "current": "未知", "latest": "未知", "update_time": "未知", "summary": "", "can_update": True },
        "maa_core": { "current": "未知", "latest": "未知", "update_time": "未知", "can_update": True },
        "maa_resource": { "current": "未知", "latest": "未知", "update_time": "未知", "can_update": True }
    }
    try:
        # 获取 Game Apk 信息
        try:
            adb_addr = Config.get_config("adb", "address", "127.0.0.1:5555")
            # 优先使用前端传入的client_type探测，否则 fallback 过去
            c_type = client_type if client_type else Config.get_config("app", "game_client_type", "Bilibili")
            apk_info = get_installed_apk_info(adb_addr, c_type)
            res["game"]["current"] = apk_info.get("current", "未知")
            res["game"]["update_time"] = apk_info.get("update_time", "未知")
            
            latest_info = get_latest_apk_info(c_type)
            # get_latest_apk_info 可能返回字典/带有 summary 字段
            if isinstance(latest_info, dict):
                res["game"]["latest"] = latest_info.get("version", "未知")
                res["game"]["summary"] = latest_info.get("summary", "")
            else:
                res["game"]["latest"] = latest_info
            
            # 基础比对：如果拿到了一致的版本号，则关闭更新
            if res["game"]["current"] != "未知" and res["game"]["current"] == res["game"]["latest"]:
                res["game"]["can_update"] = False
        except Exception as e:
            logger.error(f"获取游戏APK状态失败: {e}")

        path = get_maa_path()
        if path.exists():
            updater = Updater(path, Version.Stable)
            try:
                cur_ver = updater.get_cur_version()
                logger.info(f"本地获取当前版本结果：{cur_ver}")
                res["maa_core"]["current"] = cur_ver or "未知"
            except Exception as e:
                logger.error(f"获取当前版本失败: {e}")
            
            try:
                latest_ver, _ = updater.get_latest_version()
                logger.info(f"远程获取最新版本结果：{latest_ver}")
                res["maa_core"]["latest"] = latest_ver or "未知"
            except Exception as e:
                logger.error(f"获取最新版本失败: {e}")
            
            try:
                mtime = os.path.getmtime(path)
                res["maa_core"]["update_time"] = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            except: pass
            
            # 若 current 与 latest 获取到且相同，则置为不需要更新
            if res["maa_core"]["current"] != "未知" and res["maa_core"]["current"] == res["maa_core"]["latest"]:
                res["maa_core"]["can_update"] = False

        # 活动资源模拟：后续对接实际的版本判断逻辑
        resource_path = path / "resource"
        if resource_path.exists():
            try:
                mtime_res = os.path.getmtime(resource_path)
                res["maa_resource"]["update_time"] = datetime.fromtimestamp(mtime_res).strftime("%Y-%m-%d %H:%M:%S")
            except: pass

    except Exception as e:
        logger.error(f"获取版本信息出错: {e}")
        
    return Response.success(data=res)


@router.post("/api/update/game", dependencies=[Depends(token_auth)])
async def update_game_api(background_tasks: BackgroundTasks):
    background_tasks.add_task(do_update_game)
    return Response.success(message="游戏热更新任务已后台启动...")

@router.post("/api/update/maa", dependencies=[Depends(token_auth)])
async def update_maa_api(background_tasks: BackgroundTasks):
    background_tasks.add_task(do_update_maa)
    return Response.success(message="MAA 核心热更新任务已启动...")

@router.post("/api/update/resource", dependencies=[Depends(token_auth)])
async def update_resource_api(background_tasks: BackgroundTasks):
    background_tasks.add_task(do_update_resource)
    return Response.success(message="MAA 资源热更新任务已启动...")

@router.post("/api/update/maa_reinstall", dependencies=[Depends(token_auth)])
async def reinstall_maa_api(background_tasks: BackgroundTasks):
    background_tasks.add_task(do_reinstall_maa)
    return Response.success(message="MAA 内核重装任务已启动...")
