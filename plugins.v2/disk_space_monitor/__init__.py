import shutil
from typing import Any, List, Dict, Tuple

from apscheduler.triggers.interval import IntervalTrigger

from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType

class DiskSpaceMonitor(_PluginBase):
    # 插件基本資訊
    plugin_name = "硬盘空间监控"
    plugin_desc = "定时监控指定路径的可用硬盘空间，并在低于阈值时发送通知。"
    plugin_version = "4.0"
    plugin_config_prefix = "diskmonitor_"
    plugin_order = 99  # 加載順序，數字越大越靠後
    auth_level = 2     # 可使用的用戶級別

    # 私有屬性，由 init_plugin 初始化
    _enabled = False
    _paths = []
    _threshold_gb = 20
    _interval_hours = 6
    _notify = True

    def init_plugin(self, config: dict = None):
        """
        初始化插件，從保存的設定中讀取值。
        """
        if config:
            self._enabled = config.get("enabled", False)
            # 將 textarea 的字串轉換為路徑列表
            paths_str = config.get("paths", "")
            self._paths = [path.strip() for path in paths_str.split('\n') if path.strip()]
            self._threshold_gb = int(config.get("threshold_gb", 20))
            self._interval_hours = int(config.get("interval_hours", 6))
            self._notify = config.get("notify", True)

    def get_service(self) -> List[Dict[str, Any]]:
        """
        向 MoviePilot 核心註冊定時服務。
        """
        # 只有在啟用時才註冊服務
        if self._enabled and self._interval_hours > 0:
            return [
                {
                    "id": "disk_space_monitor_check",
                    "name": "硬盘空间监控服务",
                    # 使用 IntervalTrigger 來實現每隔 N 小時執行
                    "trigger": IntervalTrigger(hours=self._interval_hours),
                    "func": self.__check_disk_space,
                }
            ]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        定義並回傳在 Web UI 上顯示的設定表單結構和預設值。
        """
        form_structure = [
            {
                'component': 'VForm',
                'content': [
                    # 第一行：開關
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'enabled',
                                        'label': '启用插件',
                                        'hint': '控制是否执行定期的硬盘空间检查。',
                                        'persistent-hint': True,
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VSwitch',
                                    'props': {
                                        'model': 'notify',
                                        'label': '发送通知',
                                        'hint': '当空间不足时，是否发送通知。',
                                        'persistent-hint': True,
                                    }
                                }]
                            }
                        ]
                    },
                    # 第二行：閾值和間隔
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'threshold_gb',
                                        'label': '警告阈值 (GB)',
                                        'type': 'number',
                                        'placeholder': '例如：50',
                                        'hint': '当可用空间低于此值时发送警告。',
                                        'persistent-hint': True,
                                    }
                                }]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{
                                    'component': 'VTextField',
                                    'props': {
                                        'model': 'interval_hours',
                                        'label': '检查间隔 (小时)',
                                        'type': 'number',
                                        'placeholder': '例如：4',
                                        'hint': '每隔多少小时检查一次。',
                                        'persistent-hint': True,
                                    }
                                }]
                            }
                        ]
                    },
                    # 第三行：監控路徑
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [{
                                    'component': 'VTextarea',
                                    'props': {
                                        'model': 'paths',
                                        'label': '监控路径',
                                        'rows': 4,
                                        'placeholder': '每行一个路径，例如：\n/mnt/media/movies\n/downloads',
                                        'hint': '插件将检查这些路径所在的磁盘分区。',
                                        'persistent-hint': True,
                                    }
                                }]
                            }
                        ]
                    }
                ]
            }
        ]
        
        # 表單的預設數據
        default_data = {
            "enabled": False,
            "notify": True,
            "threshold_gb": 20,
            "interval_hours": 6,
            "paths": ""
        }
        
        return form_structure, default_data

    def __check_disk_space(self):
        """
        執行硬碟空間檢查的核心方法。
        """
        if not self._enabled:
            return

        logger.debug(f"[{self.plugin_name}] 开始检查硬盘空间...")

        if not self._paths:
            logger.warning(f"[{self.plugin_name}] 未设定任何监控路径，检查已跳过。")
            return

        for path in self._paths:
            try:
                # 使用 shutil.disk_usage 獲取硬碟使用情況 (total, used, free) in bytes
                total, used, free = shutil.disk_usage(path)
                
                # 將可用空間從 bytes 轉換為 GB
                free_gb = free / (1024 ** 3)
                
                logger.info(f"[{self.plugin_name}] 路径 '{path}' 所在分区剩余空间: {free_gb:.2f} GB")

                # 檢查可用空間是否低於閾值
                if free_gb < self._threshold_gb:
                    logger.warning(f"[{self.plugin_name}] 警告：路径 '{path}' 的剩余空间 ({free_gb:.2f} GB) 已低于阈值 ({self._threshold_gb} GB)！")
                    
                    if self._notify:
                        title = "🚨 硬盘空间不足警告"
                        message = (
                            f"监控的路径 '{path}' 所在分区空间即将用尽！\n\n"
                            f"▫️ 剩余空间: **{free_gb:.2f} GB**\n"
                            f"▫️ 警告阈值: {self._threshold_gb} GB\n\n"
                            "请及时清理硬盘空间。"
                        )
                        # 使用 MoviePilot v2 的標準通知方法
                        self.post_message(mtype=NotificationType.System, title=title, text=message)
                        logger.info(f"[{self.plugin_name}] 已就 '{path}' 的空间不足问题发送通知。")

            except FileNotFoundError:
                logger.error(f"[{self.plugin_name}] 错误：设定的监控路径 '{path}' 不存在，请检查插件设定。")
            except Exception as e:
                logger.error(f"[{self.plugin_name}] 检查路径 '{path}' 时发生未知错误: {e}")
        
        logger.debug(f"[{self.plugin_name}] 硬盘空间检查完成。")

    def stop_service(self):
        """
        停止插件。由於服務已交由 MoviePilot 核心管理，此處無需額外操作。
        """
        pass
