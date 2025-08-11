import shutil
import logging

from moviepilot.plugin import Plugin

class DiskSpaceMonitor(Plugin):
    """
    監控硬碟空間，當可用空間低於設定的閾值時發送通知。
    支援在 Web UI 中進行設定，並包含獨立的啟用開關。
    """
    # 插件基本資訊
    name = "DiskSpaceMonitor"
    version = "1.0.0"
    description = "定期監控指定路徑的硬碟空間，並在低於閾值時發送通知。可在 Web UI 中啟用/禁用及設定。"
    
    # 定義定時任務的唯一 ID
    JOB_ID = f"plugin_disk_space_monitor_check"

    def __init__(self, config, app):
        super().__init__(config, app)
        self.log = logging.getLogger(self.name)
        # 插件初始化時，根據當前設定來設定或移除定時任務
        self.schedule_check()

    def get_ui_config(self):
        """
        定義並回傳在 Web UI 上顯示的設定表單。
        """
        # 從目前設定中獲取值，若不存在則使用預設值
        is_enabled = self.config.get("enabled", False)
        paths_str = "\n".join(self.config.get("paths", []))
        threshold = self.config.get("threshold_gb", 20)
        interval = self.config.get("check_interval_hours", 6)

        # 定義表單欄位，將開關放在最前面
        return [
            {
                "name": "enabled",
                "label": "啟用監控",
                "type": "switch",
                "value": is_enabled,
                "description": "控制是否執行定期的硬碟空間檢查。關閉後，將不會執行任何檢查或發送通知。",
            },
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
                "attrs": {"min": 1, "step": 1},
            },
            {
                "name": "check_interval_hours",
                "label": "檢查時間間隔 (小時)",
                "type": "number",
                "value": interval,
                "description": "每隔多少小時檢查一次硬碟空間。",
                "attrs": {"min": 1, "step": 1},
            },
        ]

    def update_ui_config(self, new_config):
        """
        當使用者在 Web UI 中儲存設定時，此方法會被呼叫。
        """
        try:
            # 更新設定值
            self.config["enabled"] = new_config.get("enabled", False)
            paths_str = new_config.get("paths", "")
            self.config["paths"] = [path.strip() for path in paths_str.split('\n') if path.strip()]
            self.config["threshold_gb"] = int(new_config.get("threshold_gb", 20))
            self.config["check_interval_hours"] = int(new_config.get("check_interval_hours", 6))

            # 儲存更新後的設定到檔案中
            self.app.plugin_manager.save_plugin_config(self.name, self.config)
            
            # 根據新的啟用狀態，重新設定或移除排程任務
            self.schedule_check()
            
            status_text = "啟用" if self.config["enabled"] else "停用"
            self.log.info(f"插件設定已透過 Web UI 更新，當前狀態：{status_text}。")
            return {"success": True, "message": f"設定已成功儲存！插件當前狀態：{status_text}。"}
        except Exception as e:
            self.log.error(f"更新插件設定時出錯: {e}")
            return {"success": False, "message": f"儲存失敗: {e}"}

    def schedule_check(self):
        """
        根據插件的啟用狀態，設定或移除 APScheduler 定時任務。
        """
        # 檢查 UI 中的啟用開關
        if not self.config.get("enabled", False):
            # 如果開關是關閉的，就移除定時任務
            try:
                self.app.scheduler.remove_job(self.JOB_ID)
                self.log.info("插件已停用，已移除定時空間檢查任務。")
            except Exception:
                # 任務可能本來就不存在，忽略錯誤
                pass
            return

        # 如果開關是開啟的，就新增或更新定時任務
        interval_hours = self.config.get("check_interval_hours", 6)
        self.log.info(f"硬碟空間監控任務已啟用，每 {interval_hours} 小時檢查一次。")
        self.app.scheduler.add_job(
            self.check_disk_space,
            "interval",
            hours=interval_hours,
            id=self.JOB_ID,
            replace_existing=True
        )

    def check_disk_space(self):
        """
        執行硬碟空間檢查的核心方法。
        """
        # 雙重保險：再次確認插件是否啟用
        if not self.config.get("enabled", False):
            return
            
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
