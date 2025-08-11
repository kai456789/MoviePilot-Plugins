import shutil
import logging

from moviepilot.plugin import Plugin

class DiskSpaceMonitor(Plugin):
    """
    監控硬碟空間，當可用空間低於設定的閾值時發送通知。
    支援在 Web UI 中進行設定。
    """
    # 插件基本資訊
    name = "DiskSpaceMonitor"
    version = "1.0.0"
    description = "定期監控指定路徑的硬碟空間，並在低於閾值時發送通知。可在 Web UI 中設定。"

    def __init__(self, config, app):
        super().__init__(config, app)
        self.log = logging.getLogger(self.name)
        # 插件初始化時，設定定時任務
        self.schedule_check()

    def get_ui_config(self):
        """
        定義並回傳在 Web UI 上顯示的設定表單。
        """
        # 從目前設定中獲取值，若不存在則使用預設值
        paths_str = "\n".join(self.config.get("paths", []))
        threshold = self.config.get("threshold_gb", 20)
        interval = self.config.get("check_interval_hours", 6)

        # 定義表單欄位
        return [
            {
                "name": "paths",
                "label": "監控路徑",
                "type": "textarea",
                "value": paths_str,
                "description": "需要監控的路徑列表，每行一個路徑。例如：/mnt/media 或 D:\\downloads",
                "required": True,
            },
            {
                "name": "threshold_gb",
                "label": "警告閾值 (GB)",
                "type": "number",
                "value": threshold,
                "description": "當可用空間低於此值時 (單位 GB)，將會發送通知。",
                "attrs": {"min": 1, "step": 1}, # HTML 屬性，限制最小值为 1
            },
            {
                "name": "check_interval_hours",
                "label": "檢查時間間隔 (小時)",
                "type": "number",
                "value": interval,
                "description": "每隔多少小時檢查一次硬碟空間。",
                "attrs": {"min": 1, "step": 1}, # HTML 屬性，限制最小值为 1
            },
        ]

    def update_ui_config(self, new_config):
        """
        當使用者在 Web UI 中儲存設定時，此方法會被呼叫。
        """
        try:
            # 處理路徑列表（將 textarea 的字串轉換回列表）
            paths_str = new_config.get("paths", "")
            self.config["paths"] = [path.strip() for path in paths_str.split('\n') if path.strip()]

            # 更新其他設定，並轉換為正確的數字類型
            self.config["threshold_gb"] = int(new_config.get("threshold_gb", 20))
            self.config["check_interval_hours"] = int(new_config.get("check_interval_hours", 6))

            # 儲存更新後的設定到檔案中
            self.app.plugin_manager.save_plugin_config(self.name, self.config)
            
            # 重新排程任務以應用新的時間間隔
            self.schedule_check()
            
            self.log.info("插件設定已透過 Web UI 更新。")
            return {"success": True, "message": "設定已成功儲存並應用！"}
        except Exception as e:
            self.log.error(f"更新插件設定時出錯: {e}")
            return {"success": False, "message": f"儲存失敗: {e}"}

    def schedule_check(self):
        """
        設定或更新 APScheduler 定時任務。
        """
        # 插件總開關由 config.yaml 中的 enabled 控制
        if not self.config.get("enabled", False):
            # 如果插件被禁用，嘗試移除現有任務
            if self.app.scheduler.get_job(f"plugin_{self.name}_check"):
                self.app.scheduler.remove_job(f"plugin_{self.name}_check")
            return

        interval_hours = self.config.get("check_interval_hours", 6)
        
        self.log.info(f"硬碟空間監控任務已設定，每 {interval_hours} 小時檢查一次。")

        # 使用 MoviePilot 的全局調度器新增或更新任務
        self.app.scheduler.add_job(
            self.check_disk_space,
            "interval",
            hours=interval_hours,
            id=f"plugin_{self.name}_check",
            replace_existing=True  # 關鍵：這允許我們在不重啟的情況下更新任務
        )

    def check_disk_space(self):
        """
        執行硬碟空間檢查的核心方法。（此方法與 V1 版本相同）
        """
        self.log.debug("開始檢查硬碟空間...")
        paths_to_check = self.config.get("paths", [])
        if not paths_to_check:
            self.log.warning("未在插件設定中指定任何監控路徑 (paths)，檢查已跳過。")
            return

        threshold_gb = self.config.get("threshold_gb", 20)
        
        for path in paths_to_check:
            try:
                total, used, free = shutil.disk_usage(path)
                free_gb = free / (1024 ** 3)
                self.log.info(f"路徑 '{path}' 剩餘空間: {free_gb:.2f} GB")

                if free_gb < threshold_gb:
                    title = "🚨 硬碟空間不足警告"
                    message = (
                        f"監控的路徑 '{path}' 空間即將用盡！\n"
                        f"剩餘空間: {free_gb:.2f} GB\n"
                        f"警告閾值: {threshold_gb} GB\n"
                        f"請及時清理硬碟空間。"
                    )
                    self.app.notifier.send(title, message)
                    self.log.warning(f"警告：路徑 '{path}' 的剩餘空間 ({free_gb:.2f} GB) 已低於閾值 ({threshold_gb} GB)，已發送通知。")

            except FileNotFoundError:
                self.log.error(f"錯誤：設定的監控路徑 '{path}' 不存在，請在 Web UI 中檢查設定。")
            except Exception as e:
                self.log.error(f"檢查路徑 '{path}' 時發生未知錯誤: {e}")
        
        self.log.debug("硬碟空間檢查完成。")
