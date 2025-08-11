# -*- coding: utf-8 -*-
import os
import shutil
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# MoviePilot 內建
from app.plugins import PluginBase  # 你的環境若基類名為 Plugin，請替換為 from app.plugins import Plugin
from app.log import logger

# 可選：提供聊天命令支持（若不需要，可刪除下方兩行與對應方法）
from app.core.event import eventmanager, EventType  # 用於遠端命令與消息推送等

class DiskMonitor(PluginBase):
    # 基本資訊
    plugin_name = "磁碟空間監控"
    plugin_desc = "定時檢測指定路徑的剩餘空間，低於門檻時推送告警，可手動查詢狀態"
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/icons/harddrive.png"
    plugin_version = "v5.0"
    plugin_author = "K"
    author_url = ""
    plugin_config_prefix = "diskmonitor"

    def __init__(self):
        super().__init__()
        # 運行內部狀態
        self._last_alert_at: Dict[str, float] = {}  # path -> epoch seconds
        self._alerted_under_threshold: Dict[str, bool] = {}  # path -> bool

        # 預設配置
        self._paths: List[str] = ["/"]  # 要監控的路徑（Windows 可填 C:\\、D:\\ 等）
        self._threshold_pct: float = 10.0  # 低於此剩餘百分比告警
        self._interval_minutes: int = 5  # 定時任務執行間隔
        self._cooldown_minutes: int = 60  # 告警冷卻（避免重複騷擾）
        self._only_once_until_recover: bool = True  # 低於門檻只提醒一次，恢復上門檻後再解鎖提醒
        self._ignore_missing_path: bool = True  # 路徑不存在是否忽略

    # 當插件載入或配置改變時，MoviePilot 會把配置傳進來
    def init_plugin(self, config: Optional[dict] = None):
        cfg = config or {}
        self._paths = self._parse_paths(cfg.get("paths", self._paths))
        self._threshold_pct = float(cfg.get("threshold_pct", self._threshold_pct))
        self._interval_minutes = int(cfg.get("interval_minutes", self._interval_minutes))
        self._cooldown_minutes = int(cfg.get("cooldown_minutes", self._cooldown_minutes))
        self._only_once_until_recover = bool(cfg.get("only_once_until_recover", self._only_once_until_recover))
        self._ignore_missing_path = bool(cfg.get("ignore_missing_path", self._ignore_missing_path))

        # 清理不存在的狀態
        for p in list(self._last_alert_at.keys()):
            if p not in self._paths:
                self._last_alert_at.pop(p, None)
                self._alerted_under_threshold.pop(p, None)

        logger.info(f"[DiskMonitor] 初始化完成：paths={self._paths}, "
                    f"threshold={self._threshold_pct}%, interval={self._interval_minutes}m, "
                    f"cooldown={self._cooldown_minutes}m, once={self._only_once_until_recover}, "
                    f"ignore_missing={self._ignore_missing_path}")

    # 配置表單（會顯示在插件配置頁），使用 Vuetify JSON 組件，props 中的 model 等同 v-model
    def get_form(self) -> Optional[List[dict]]:
        return [
            {
                "component": "VTextarea",
                "props": {
                    "label": "監控路徑（每行一個）",
                    "rows": 4,
                    "hint": "例如 /, /data, C:\\\\, D:\\\\",
                    "persistentHint": True,
                    "model": "\n".join(self._paths)
                },
                "name": "paths"
            },
            {
                "component": "VSlider",
                "props": {
                    "label": "告警門檻（剩餘百分比）",
                    "min": 1,
                    "max": 50,
                    "step": 1,
                    "thumbLabel": True,
                    "model": self._threshold_pct
                },
                "name": "threshold_pct"
            },
            {
                "component": "VTextField",
                "props": {
                    "label": "檢查間隔（分鐘）",
                    "type": "number",
                    "model": self._interval_minutes
                },
                "name": "interval_minutes"
            },
            {
                "component": "VTextField",
                "props": {
                    "label": "告警冷卻（分鐘）",
                    "type": "number",
                    "hint": "同一路徑在冷卻期內不重複提醒",
                    "persistentHint": True,
                    "model": self._cooldown_minutes
                },
                "name": "cooldown_minutes"
            },
            {
                "component": "VSwitch",
                "props": {
                    "label": "低於門檻只提醒一次（恢復後再提醒）",
                    "model": self._only_once_until_recover
                },
                "name": "only_once_until_recover"
            },
            {
                "component": "VSwitch",
                "props": {
                    "label": "忽略不存在的路徑",
                    "model": self._ignore_missing_path
                },
                "name": "ignore_missing_path"
            }
        ]

    # 公共定時服務註冊（在 設定-服務 中可見、可手動啟動/停止）
    def get_service(self) -> Optional[List[Dict[str, Any]]]:
        return [{
            "id": "diskmonitor.schedule",
            "name": "磁碟空間監控",
            "trigger": "interval",            # APScheduler 觸發器：cron/interval/date 等
            "func": self._run_check,
            "kwargs": {"minutes": self._interval_minutes}
        }]

    # 提供外部 API（立即檢查、查詢狀態）
    def get_api(self) -> Optional[List[Dict[str, Any]]]:
        return [
            {
                "path": "/check_now",
                "endpoint": self.api_check_now,
                "methods": ["GET"],
                "summary": "立即執行一次檢查",
                "description": "立刻檢測所有設定的路徑並返回結果"
            },
            {
                "path": "/status",
                "endpoint": self.api_status,
                "methods": ["GET"],
                "summary": "取得目前狀態",
                "description": "返回各路徑的容量與剩餘百分比"
            }
        ]

    # 可選：提供聊天命令（如 Telegram/Slack）以遠端觸發
    def get_command(self) -> Optional[List[Dict[str, Any]]]:
        return [
            {
                "cmd": "/disk_status",
                "event": EventType.PluginAction,
                "desc": "查看磁碟空間狀態",
                "category": "系統工具",
                "data": {"action": "disk_status"}
            },
            {
                "cmd": "/disk_check",
                "event": EventType.PluginAction,
                "desc": "立即執行一次磁碟檢查",
                "category": "系統工具",
                "data": {"action": "disk_check"}
            }
        ]

    # ========== 服務與 API 具體實作 ==========

    def _run_check(self):
        problems = []
        for path in self._paths:
            try:
                if not os.path.exists(path):
                    if self._ignore_missing_path:
                        continue
                    problems.append(f"路徑不存在：{path}")
                    continue

                total, used, free = self._disk_usage(path)
                free_pct = (free / total) * 100 if total else 0.0

                # 門檻與冷卻
                now = time.time()
                last = self._last_alert_at.get(path, 0)
                in_cooldown = (now - last) < (self._cooldown_minutes * 60)
                was_under = self._alerted_under_threshold.get(path, False)

                if free_pct < self._threshold_pct:
                    if self._only_once_until_recover and was_under:
                        # 已經提醒過，且尚未恢復，不再重複
                        continue
                    if in_cooldown and not self._only_once_until_recover:
                        # 冷卻期內不再提醒
                        continue

                    self._send_alert(
                        title="⚠️ 磁碟空間不足",
                        path=path,
                        total=total,
                        used=used,
                        free=free,
                        free_pct=free_pct
                    )
                    self._last_alert_at[path] = now
                    self._alerted_under_threshold[path] = True
                else:
                    # 若從不足恢復，且之前有標記，發送恢復通知
                    if was_under:
                        self._send_recovered(
                            title="✅ 磁碟空間恢復",
                            path=path,
                            total=total,
                            used=used,
                            free=free,
                            free_pct=free_pct
                        )
                    self._alerted_under_threshold[path] = False

            except Exception as e:
                logger.error(f"[DiskMonitor] 檢查失敗：{path}，{e}", exc_info=True)
                problems.append(f"檢查失敗：{path} -> {e}")

        return {"ok": len(problems) == 0, "errors": problems}

    def api_check_now(self):
        return self._run_check()

    def api_status(self):
        items = []
        for path in self._paths:
            try:
                if not os.path.exists(path):
                    items.append({"path": path, "exists": False})
                    continue
                total, used, free = self._disk_usage(path)
                free_pct = (free / total) * 100 if total else 0.0
                items.append({
                    "path": path,
                    "exists": True,
                    "total": total,
                    "used": used,
                    "free": free,
                    "free_pct": round(free_pct, 2),
                    "human": {
                        "total": self._fmt_bytes(total),
                        "used": self._fmt_bytes(used),
                        "free": self._fmt_bytes(free),
                    }
                })
            except Exception as e:
                items.append({"path": path, "exists": True, "error": str(e)})
        return {"items": items, "threshold_pct": self._threshold_pct}

    # ========== 遠端命令回應（可選） ==========

    @eventmanager.register(EventType.PluginAction)
    def command_action(self, event):
        event_data = getattr(event, "event_data", None)
        if not event_data:
            return
        action = event_data.get("action")
        if action not in {"disk_status", "disk_check"}:
            return

        channel = event_data.get("channel")
        userid = event_data.get("userid")

        if action == "disk_check":
            result = self._run_check()
            text = "已執行一次檢查。\n\n" + self._render_status_text()
        else:
            text = self._render_status_text()

        self.post_message(
            channel=channel,
            title="💽 磁碟空間狀態",
            text=text,
            userid=userid
        )

    # ========== 工具方法 ==========

    def _parse_paths(self, value: Any) -> List[str]:
        if isinstance(value, list):
            paths = value
        elif isinstance(value, str):
            # 支援以換行或逗號分隔
            parts = [p.strip() for p in value.replace(",", "\n").splitlines()]
            paths = [p for p in parts if p]
        else:
            paths = []
        # 去重且保持順序
        seen, result = set(), []
        for p in paths:
            if p not in seen:
                seen.add(p)
                result.append(p)
        return result or ["/"]

    def _disk_usage(self, path: str):
        # 兼容多平台
        usage = shutil.disk_usage(path)
        total = int(usage.total)
        free = int(usage.free)
        used = total - free
        return total, used, free

    def _fmt_bytes(self, size: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB", "PB"]
        if size <= 0:
            return "0 B"
        i = 0
        s = float(size)
        while s >= 1024 and i < len(units) - 1:
            s /= 1024.0
            i += 1
        return f"{s:.2f} {units[i]}"

    def _render_status_text(self) -> str:
        lines = []
        for path in self._paths:
            if not os.path.exists(path):
                lines.append(f"• {path}: 路徑不存在")
                continue
            total, used, free = self._disk_usage(path)
            free_pct = (free / total) * 100 if total else 0.0
            lines.append(
                f"• {path}\n"
                f"  - 總量: {self._fmt_bytes(total)}\n"
                f"  - 已用: {self._fmt_bytes(used)}\n"
                f"  - 剩餘: {self._fmt_bytes(free)} ({free_pct:.2f}%)\n"
            )
        if not lines:
            return "未配置任何監控路徑。"
        return "\n".join(lines)

    def _send_alert(self, title: str, path: str, total: int, used: int, free: int, free_pct: float):
        text = (
            f"路徑：{path}\n"
            f"總量：{self._fmt_bytes(total)}\n"
            f"已用：{self._fmt_bytes(used)}\n"
            f"剩餘：{self._fmt_bytes(free)}（{free_pct:.2f}%）\n"
            f"門檻：{self._threshold_pct:.0f}%\n"
        )
        logger.warning(f"[DiskMonitor] 空間不足告警 -> {path}, 剩餘 {free_pct:.2f}%")
        self.post_message(title=title, text=text)

    def _send_recovered(self, title: str, path: str, total: int, used: int, free: int, free_pct: float):
        text = (
            f"路徑：{path}\n"
            f"目前剩餘：{self._fmt_bytes(free)}（{free_pct:.2f}%）\n"
            f"已高於門檻：{self._threshold_pct:.0f}%"
        )
        logger.info(f"[DiskMonitor] 空間恢復 -> {path}, 剩餘 {free_pct:.2f}%")
        self.post_message(title=title, text=text)
