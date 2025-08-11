# -*- coding: utf-8 -*-

"""
MoviePilot v2 Plugin: Disk Space Monitor
File: disk_monitor.py
Description: This plugin monitors the disk space of a specified path and sends a notification if it falls below a threshold.
"""

import shutil
import os
from moviepilot.plugin import Plugin
from moviepilot.utils.system import get_human_readable_size

# ----------------------------------------
# Do not edit the class name
# ----------------------------------------
class DiskMonitor(Plugin):
    """
    Disk Space Monitor Plugin for MoviePilot v2.
    """

    # 插件的唯一識別名稱
    name = "disk_monitor"
    # 顯示在 UI 上的標題
    title = "磁碟空間監控"
    # 插件的簡短描述
    description = "監控指定路徑的磁碟剩餘空間，當低於設定的閾值時發送通知。"
    # 插件版本
    version = "1.0.1"
    # 插件作者
    author = "Gemini"

    # 插件的默認設定值
    # enabled: 是否啟用插件
    # run_interval: 運行間隔（秒），例如 3600 秒代表每小時運行一次
    # path: 需要監控的磁碟路徑，請務必修改為你的實際路徑
    # threshold_gb: 剩餘空間的警告閾值（單位：GB）
    default_config = {
        "enabled": True,
        "run_interval": 3600,
        "path": "/path/to/your/media/folder",  # 示例路徑，請務必修改
        "threshold_gb": 50
    }

    def __init__(self, context):
        """
        初始化插件。
        """
        super().__init__(context)
        # 設置日誌記錄器
        self.log = self.plugin_context.log

    def run(self):
        """
        插件的主執行方法，MoviePilot 會定時調用此方法。
        """
        if not self.config.get("enabled"):
            self.log.debug("插件 %s 已被禁用，跳過執行。", self.name)
            return

        path = self.config.get("path")
        threshold_gb = self.config.get("threshold_gb")

        # 檢查路徑是否有效
        if not path or path == "/path/to/your/media/folder":
            self.log.error("請在插件設定中為 '%s' 提供一個有效的監控路徑 (path)。", self.title)
            return
            
        if not os.path.exists(path):
            self.log.error("設定的監控路徑 '%s' 不存在，請檢查插件設定。", path)
            return

        try:
            # 使用 shutil.disk_usage 獲取磁碟使用情況
            total, used, free = shutil.disk_usage(path)
            
            # 將剩餘空間從字節轉換為 GB
            free_gb = free / (1024 ** 3)

            # 記錄當前磁碟空間信息
            self.log.info(
                "磁碟路徑: %s | 總空間: %s | 已用空間: %s | 剩餘空間: %s (%.2f GB)",
                path,
                get_human_readable_size(total),
                get_human_readable_size(used),
                get_human_readable_size(free),
                free_gb
            )

            # 檢查剩餘空間是否低於閾值
            if free_gb < threshold_gb:
                title = "🚨 磁碟空間不足警告"
                message = (
                    f"監控的路徑 '{path}' 剩餘空間嚴重不足！\n\n"
                    f"剩餘空間: **{free_gb:.2f} GB**\n"
                    f"警告閾值: **{threshold_gb} GB**\n\n"
                    f"請及時清理磁碟空間，以免影響 MoviePilot 正常下載與整理。"
                )
                
                # 透過 MoviePilot 的通知器發送通知
                self.plugin_context.notifier.send(title, message)
                self.log.warning("磁碟空間低於閾值，已發送通知。")
            else:
                self.log.info("磁碟空間充足。")

        except Exception as e:
            self.log.error("檢查磁碟空間時發生未預期的錯誤: %s", str(e))

    def get_status(self):
        """
        返回插件的當前狀態，顯示在 UI 上。
        """
        if not self.config.get("enabled"):
            return "已禁用"
            
        path = self.config.get("path")
        if not path or path == "/path/to/your/media/folder" or not os.path.exists(path):
            return "路徑未配置或無效"

        try:
            _, _, free = shutil.disk_usage(path)
            return f"剩餘空間: {get_human_readable_size(free)}"
        except Exception:
            return "狀態獲取失敗"
