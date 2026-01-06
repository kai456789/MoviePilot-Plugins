
import os
import time
from datetime import datetime, timedelta
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.utils.http import RequestUtils
from app.core.config import settings
from app.plugins import _PluginBase
from typing import Any, List, Dict, Tuple, Optional
from app.log import logger
import xml.dom.minidom
from app.utils.dom import DomUtils
from urllib.parse import quote  # ★ 修正：缺失的匯入

def retry(ExceptionToCheck: Any,
          tries: int = 3, delay: int = 3, backoff: int = 1, logger: Any = None, ret: Any = None):
    """
    :param ExceptionToCheck: 需要捕获的异常
    :param tries: 重试次数
    :param delay: 延迟时间
    :param backoff: 延迟倍数
    :param logger: 日志对象
    :param ret: 默认返回
    """
    def deco_retry(f):
        def f_retry(*args, **kwargs):
            mtries, mdelay = tries, delay
            while mtries > 0:
                try:
                    return f(*args, **kwargs)
                except ExceptionToCheck as e:
                    msg = f"未获取到文件信息，{mdelay}秒后重试 ..."
                    if logger:
                        logger.warn(msg)
                    else:
                        print(msg)
                    time.sleep(mdelay)
                    mtries -= 1
                    mdelay *= backoff
            if logger:
                logger.warn('请确保当前季度番剧文件夹存在或检查网络问题')
            return ret
        return f_retry
    return deco_retry


class ANiStrm(_PluginBase):
    # 插件名称
    plugin_name = "ANiStrm-AI"
    # 插件描述
    plugin_desc = "自动获取当季所有番剧，免去下载，轻松拥有一个番剧媒体库"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/honue/MoviePilot-Plugins/main/icons/anistrm.png"
    # 插件版本（★ 版本號小幅提升）
    plugin_version = "1.0"
    # 插件作者
    plugin_author = "honue"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "anistrm_"
    # 加载顺序
    plugin_order = 15
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _enabled = False
    # 任务执行间隔
    _cron = None
    _onlyonce = False
    _fulladd = False
    _storageplace = None
    _date = None

    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        # 停止现有任务
        self.stop_service()
        if config:
            self._enabled = config.get("enabled")
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce")
            self._fulladd = config.get("fulladd")
            self._storageplace = config.get("storageplace")

        # 加载模块
        if self._enabled or self._onlyonce:
            # 定时服务
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)

            if self._enabled and self._cron:
                try:
                    self._scheduler.add_job(
                        func=self.__task,
                        trigger=CronTrigger.from_crontab(self._cron),
                        name="ANiStrm文件创建"
                    )
                    logger.info(f'ANi-Strm定时任务创建成功：{self._cron}')
                except Exception as err:
                    logger.error(f"定时任务配置错误：{str(err)}")

            if self._onlyonce:
                logger.info(f"ANi-Strm服务启动，立即运行一次")
                self._scheduler.add_job(
                    func=self.__task, args=[self._fulladd], trigger='date',
                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                    name="ANiStrm文件创建"
                )
                # 关闭一次性开关 全量转移
                self._onlyonce = False
                self._fulladd = False
                self.__update_config()

            # 启动任务
            if self._scheduler.get_jobs():
                self._scheduler.print_jobs()
                self._scheduler.start()

    def __get_ani_season(self, idx_month: int = None) -> str:
        """
        按 1/4/7/10 分季，返回例如 '2026-1'
        保留 idx_month 兼容，但默认为系统月份
        """
        current_date = datetime.now()
        year = current_date.year
        month = idx_month if idx_month else current_date.month

        if month in [1, 2, 3]:
            season = 1
        elif month in [4, 5, 6]:
            season = 4
        elif month in [7, 8, 9]:
            season = 7
        else:
            season = 10

        self._date = f'{year}-{season}'
        return self._date

    @retry(Exception, tries=3, logger=logger, ret=[])
    def get_current_season_list(self) -> List[str]:
        """
        兼容新舊回傳結構：既可能是 {'files': [...]} 也可能是 {'list': [...]}
        """
        url = f'https://openani.an-i.workers.dev/{self.__get_ani_season()}/'
        rep = RequestUtils(
            ua=settings.USER_AGENT if settings.USER_AGENT else None,
            proxies=settings.PROXY if settings.PROXY else None
        ).post(url=url)

        logger.debug(getattr(rep, "text", ""))

        try:
            data = rep.json()
        except Exception as e:
            logger.error(f'获取当季文件列表失败：返回非 JSON，{e}')
            return []

        files_json = data.get('files') or data.get('list') or []
        names: List[str] = []
        for item in files_json:
            # item 可能是 dict 或純字串
            name = item.get('name') if isinstance(item, dict) else item
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
        return names

    @retry(Exception, tries=3, logger=logger, ret=[])
    def get_latest_list(self) -> List[Dict[str, str]]:
        """
        解析 ani-download.xml，並將連結主機替換為 openani，且立即正規化為 .mp4?d=true
        """
        addr = 'https://api.ani.rip/ani-download.xml'
        ret = RequestUtils(
            ua=settings.USER_AGENT if settings.USER_AGENT else None,
            proxies=settings.PROXY if settings.PROXY else None
        ).get_res(addr)

        ret_xml = ret.text
        ret_array: List[Dict[str, str]] = []

        # 解析XML
        dom_tree = xml.dom.minidom.parseString(ret_xml)
        rootNode = dom_tree.documentElement
        items = rootNode.getElementsByTagName("item")

        for item in items:
            rss_info: Dict[str, str] = {}
            # 标题
            title = DomUtils.tag_value(item, "title", default="")
            # 链接
            link = DomUtils.tag_value(item, "link", default="")
            fixed = link.replace("resources.ani.rip", "openani.an-i.workers.dev")
            rss_info['title'] = title
            rss_info['link'] = self._normalize_openani_url(fixed)
            ret_array.append(rss_info)

        return ret_array

    def _normalize_openani_url(self, url: str) -> str:
        """
        將 openani URL 統一轉成 Emby / Jellyfin 可播放格式
        強制輸出：xxx.mp4?d=true
        """
        if not url:
            return ""
        if url.endswith(".mp4?d=true"):
            return url
        if "?d=mp4" in url:
            return url.replace("?d=mp4", ".mp4?d=true")
        if url.endswith(".mp4"):
            return f"{url}?d=true"
        return f"{url}.mp4?d=true"

    def __touch_strm_file(self, file_name: str, file_url: str = None) -> bool:
        """
        建立 .strm 檔；若提供 file_url 則正規化為 .mp4?d=true，
        若不提供則使用季路徑 + 檔名（進行百分號編碼）
        """
        # 確保目錄存在
        try:
            if self._storageplace and not os.path.exists(self._storageplace):
                os.makedirs(self._storageplace, exist_ok=True)
        except Exception as e:
            logger.error(f"创建存储目录失败：{self._storageplace}, {e}")
            return False

        if not file_url:
            encoded_filename = quote(file_name, safe='')
            src_url = f'https://openani.an-i.workers.dev/{self._date}/{encoded_filename}.mp4?d=true'
        else:
            src_url = self._normalize_openani_url(file_url)

        file_path = f'{self._storageplace}/{file_name}.strm'
        if os.path.exists(file_path):
            logger.debug(f'{file_name}.strm 文件已存在')
            return False

        try:
            with open(file_path, 'w') as file:
                file.write(src_url)
            logger.debug(f'创建 {file_name}.strm 文件成功')
            return True
        except Exception as e:
            logger.error('创建strm源文件失败：' + str(e))
            return False

    def __task(self, fulladd: bool = False):
        cnt = 0
        # 增量添加更新（Top15）
        if not fulladd:
            rss_info_list = self.get_latest_list()
            logger.info(f'本次处理 {len(rss_info_list)} 个文件')
            for rss_info in rss_info_list:
                if self.__touch_strm_file(file_name=rss_info['title'], file_url=rss_info['link']):
                    cnt += 1
        # 全量添加当季
        else:
            name_list = self.get_current_season_list()
            logger.info(f'本次处理 {len(name_list)} 个文件')
            for file_name in name_list:
                if self.__touch_strm_file(file_name=file_name):
                    cnt += 1

        logger.info(f'新创建了 {cnt} 个strm文件')

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'fulladd',
                                            'label': '下次创建当前季度所有番剧strm',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '执行周期',
                                            'placeholder': '0 0 ? ? ?'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'storageplace',
                                            'label': 'Strm存储地址',
                                            'placeholder': '/downloads/strm'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                },
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '自动从open ANi抓取下载直链生成strm文件，免去人工订阅下载' + '\n' +
                                                    '配合目录监控使用，strm文件创建在/downloads/strm' + '\n' +
                                                    '通过目录监控转移到link媒体库文件夹 如/downloads/link/strm mp会完成刮削',
                                            'style': 'white-space: pre-line;'
                                        }
                                    },
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': 'emby容器需要设置代理，docker的环境变量必须要有http_proxy代理变量，大小写敏感，具体见readme.' + '\n' +
                                                    'https://github.com/honue/MoviePilot-Plugins',
                                            'style': 'white-space: pre-line;'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "onlyonce": False,
            "fulladd": False,
            "storageplace": '/downloads/strm',
            "cron": "*/20 22,23,0,1 * * *",
        }

    def __update_config(self):
        self.update_config({
            "onlyonce": self._onlyonce,
            "cron": self._cron,
            "enabled": self._enabled,
            "fulladd": self._fulladd,
            "storageplace": self._storageplace,
        })

    def get_page(self) -> List[dict]:
        return []

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
            if self._scheduler and self._scheduler.running:
                self._scheduler.shutdown()
            self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s" % str(e))


if __name__ == "__main__":
    # 僅供快速檢查，不在 MP 正式環境中使用
    anistrm = ANiStrm()
    name_list = anistrm.get_latest_list()
    print(name_list)
