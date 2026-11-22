#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B站大会员「大积分」自动获取脚本  v2.0
=============================================================
功能：本地部署，自动完成大会员大积分中所有「不含购买」的日常任务
      - 每日签到                      (+10 左右)
      - 浏览追番频道 animatetab        (+10)
      - 浏览影视频道 filmtab           (+10)
      - 浏览会员购页面10秒 vipmallview (+10)
      - 浏览装扮商城主页 dress-view     (+10)
      - 观看剧集内容 ogvwatchnew       (+40)   ← 见下方说明
      - 其它非购买类任务（通用兜底）

参考项目：
  - BiliBiliToolPro  https://github.com/RayWangQvQ/BiliBiliToolPro
  - BiliOutils       https://github.com/catlair/BiliOutils

关键接口（已实测，2026-10 线上有效）：
  任务列表  GET  /x/vip_point/task/combine
  签到      POST /pgc/activity/score/task/sign2
  领取任务  POST /pgc/activity/score/task/receive/v2    form: taskCode=<code>   ← 驼峰！
  完成任务  POST /pgc/activity/score/task/complete/v2   form: taskCode=<code>   ← 驼峰！
  频道浏览  POST /pgc/activity/deliver/task/complete    form: position=jp_channel|tv_channel
  会员购    POST https://show.bilibili.com/api/activity/fire/common/event/dispatch
  观看凭证  POST /pgc/activity/deliver/material/receive  → watch_count_down_cfg{task_id,token}
  观看完成  POST /pgc/activity/deliver/task/complete     form: task_id,token,task_sign,timestamp

「观看剧集内容」说明：
  该任务由服务端核验「单次连续观看番剧/剧集 ≥10 分钟」，客户端自报无效。
  实测（2026-10-11）可用链路（App 签名，纯 HTTP，config settings.watch_mode）：
    - "app_api" ：material/receive 领观看凭证 -> 等 10 分钟倒计时 -> 签名上报完成（默认，已实测 +40 到账）
    - "browser" ：用本机 Edge 真实播放 10 分钟（备用方案；注意 headless 不会发心跳）
    - "off"     ：跳过该任务
"""

import argparse
import hashlib
import json
import logging
import os
import random
import sys
import time
import urllib.parse
from datetime import datetime

import requests

# ============================================================
# 基础配置
# ============================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
STATE_PATH = os.path.join(SCRIPT_DIR, "run_state.json")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
APP_USER_AGENT = (
    "Mozilla/5.0 BiliDroid/9.13.0 (bbcallen@gmail.com) os/android model/Nexus "
    "mobi_app/android build/9130500 channel/yingyongbao version/9.13.0 osVer/10 sdkInt/29"
)

REF_TASK = "https://big.bilibili.com/mobile/bigPoint/task"

# 不计入「任务是否全部完成」判断的购买类关键词
BUY_KEYWORDS = ["购买", "付费", "线下门店", "实付"]

# state 语义
STATE_NOT_RECEIVED = 0   # 未领取
STATE_IN_PROGRESS = 1    # 已领取 / 待完成
STATE_COMPLETED = 3      # 已完成


def load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        print(f"[错误] 配置文件不存在: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


CONFIG = load_config()
SETTINGS = CONFIG.get("settings", {})

# ============================================================
# 日志
# ============================================================

LOG_FILE = os.path.join(SCRIPT_DIR, CONFIG.get("log", {}).get("log_file", "bilibili_vip_point.log"))
CONSOLE_LOG_FILE = os.path.join(SCRIPT_DIR, "bilibili_vip_point.console.log")

# Windows 控制台默认 GBK，日志里的 ✓ / × 等符号会导致 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def setup_logger() -> logging.Logger:
    logger = logging.getLogger("BiliVipPoint")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # 计划任务用 pythonw.exe 运行时没有控制台（sys.stdout 为 None），
    # 此时把 INFO 级输出镜像到 console.log，保持与旧版 cmd 重定向一致的日志文件。
    stream = sys.stdout or sys.stderr
    if stream is not None:
        sh = logging.StreamHandler(stream)
        sh.setLevel(logging.INFO)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    else:
        try:
            mh = logging.FileHandler(CONSOLE_LOG_FILE, encoding="utf-8")
            mh.setLevel(logging.INFO)
            mh.setFormatter(fmt)
            logger.addHandler(mh)
        except Exception:
            pass

    if CONFIG.get("log", {}).get("log_to_file", True):
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    return logger


log = setup_logger()


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"写入运行状态失败: {e}")


# ============================================================
# 客户端
# ============================================================

class BiliVipPointClient:
    API_NAV = "https://api.bilibili.com/x/web-interface/nav"
    API_COMBINE = "https://api.bilibili.com/x/vip_point/task/combine"
    API_POINT_LIST = "https://api.bilibili.com/x/vip_point/list"
    API_THREE_DAY_SIGN = "https://api.bilibili.com/x/vip/vip_center/sign_in/three_days_sign"
    API_SIGN2 = "https://api.bilibili.com/pgc/activity/score/task/sign2"
    API_RECEIVE_V2 = "https://api.bilibili.com/pgc/activity/score/task/receive/v2"
    API_COMPLETE_V2 = "https://api.bilibili.com/pgc/activity/score/task/complete/v2"
    API_DELIVER_COMPLETE = "https://api.bilibili.com/pgc/activity/deliver/task/complete"
    API_MATERIAL_RECEIVE = "https://api.bilibili.com/pgc/activity/deliver/material/receive"
    API_MALL_DISPATCH = "https://show.bilibili.com/api/activity/fire/common/event/dispatch"
    MALL_EVENT_ID = "hevent_oy4b7h3epeb"

    # 公开的 App appkey / appsec（用于 App 端接口签名）
    APP_KEY = "1d8b6e7d45233436"
    APP_SEC = "560c52ccd288fed045859ed18bffd973"

    def __init__(self, cookie: dict):
        self.cookie = cookie
        self.csrf = cookie.get("bili_jct", "")
        self.mid = cookie.get("DedeUserID", "")
        self.buvid = cookie.get("buvid3", "") or cookie.get("Buvid", "")
        self.device_id = SETTINGS.get("app_device_id", "f9abaee74692f9e9")

        self.timeout = SETTINGS.get("request_timeout", 30)
        self.retry_times = SETTINGS.get("retry_times", 3)
        self.retry_delay = SETTINGS.get("retry_delay", 5)

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": DEFAULT_USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        for k, v in cookie.items():
            if v and not str(v).startswith("在此填入"):
                self.session.cookies.set(k, v)

    # ---------------- 底层请求 ----------------

    def _request(self, method: str, url: str, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        last_err = None
        for attempt in range(1, self.retry_times + 1):
            try:
                r = self.session.request(method, url, **kwargs)
                try:
                    return r.json()
                except ValueError:
                    log.warning(f"[{method} 非JSON] status={r.status_code} url={url}")
                    return None
            except requests.RequestException as e:
                last_err = e
                log.warning(f"[{method} 网络错误] 第{attempt}次: {e}")
            if attempt < self.retry_times:
                time.sleep(self.retry_delay * attempt)
        log.error(f"[{method} 失败] {url} ({last_err})")
        return None

    def get(self, url, **kwargs):
        kwargs.setdefault("headers", {})
        kwargs["headers"].setdefault("Referer", REF_TASK)
        return self._request("GET", url, **kwargs)

    def post_form(self, url, data: dict, headers: dict = None, app: bool = False):
        h = {"Referer": REF_TASK}
        if app:
            # App 端接口不带 Origin（与真机抓包一致）
            h.update({
                "User-Agent": APP_USER_AGENT,
                "app-key": "android64",
                "env": "prod",
                "bili-http-engine": "cronet",
                "native_api_from": "h5",
                "buvid": self.buvid,
                "fp_local": self.device_id,
                "fp_remote": self.device_id,
                "session_id": SETTINGS.get("app_session_id", "e04d2e05"),
                "x-bili-mid": self.mid,
            })
        else:
            h["Origin"] = "https://big.bilibili.com"
        if headers:
            h.update(headers)
        return self._request("POST", url, data=data, headers=h)

    # ---------------- 基础信息 ----------------

    def check_login(self) -> bool:
        data = self.get(self.API_NAV)
        if not data or data.get("code") != 0:
            log.error("登录状态验证失败，请检查 config.json 中的 Cookie")
            return False
        d = data["data"]
        if not d.get("isLogin"):
            log.error("Cookie 无效 / 未登录")
            return False
        log.info(f"登录成功: 用户={d.get('uname')}  大会员={'已开通' if d.get('vipStatus') else '未开通'}")
        return bool(d.get("vipStatus"))

    def get_combine(self, quiet: bool = False) -> dict:
        data = self.get(self.API_COMBINE, params={"buvid": self.buvid, "csrf": self.csrf})
        if not data or data.get("code") != 0:
            if not quiet:
                log.warning(f"获取任务列表失败: {data.get('code') if data else '网络错误'}")
            return {}
        d = data.get("data", {})
        if not quiet:
            pi = d.get("point_info", {})
            log.info(f"当前大积分: {pi.get('point')}（本月已得 {d.get('task_info', {}).get('score_month', '?')}）")
        return d

    @staticmethod
    def daily_tasks(combine: dict) -> list:
        for m in (combine.get("task_info", {}) or {}).get("modules", []):
            if m.get("module_title") == "日常任务":
                return m.get("common_task_item", []) or []
        return []

    def signed_today(self, combine: dict) -> bool:
        """今日是否已签到：先看任务列表，再看积分流水（列表里的 signed 字段有时不刷新）"""
        st = combine.get("task_info", {}).get("sing_task_item", {}) or {}
        for h in st.get("histories", []) or []:
            if h.get("is_today") and h.get("signed"):
                return True
        today = datetime.now().strftime("%Y-%m-%d")
        data = self.get(self.API_POINT_LIST,
                        params={"csrf": self.csrf, "change_type": 1, "pn": 1, "ps": 10})
        for item in ((data or {}).get("data") or {}).get("big_point_list", []) or []:
            ts = item.get("change_time")
            if ts and datetime.fromtimestamp(ts).strftime("%Y-%m-%d") == today \
                    and "签到" in str(item.get("remark", "")):
                log.info(f"  积分流水显示今日已签到（+{item.get('point')}），跳过")
                return True
        return False

    # ---------------- 签到 ----------------

    def do_sign(self, combine: dict) -> bool:
        log.info("-" * 46)
        log.info("【每日签到】")
        if self.signed_today(combine):
            log.info("  今日已签到，跳过")
            return True
        # 注意：mobi_app / platform / csrf 需要放在 query 上
        data = self._request(
            "POST", self.API_SIGN2 + f"?mobi_app=android&platform=android&csrf={self.csrf}",
            data={"device": "phone", "t": str(int(time.time() * 1000))},
            headers={"Referer": "https://big.bilibili.com/mobile/index",
                     "Origin": "https://big.bilibili.com"},
        )
        if data and data.get("code") == 0:
            log.info("  签到成功 ✓")
            return True
        msg = str((data or {}).get("message", "未知错误"))
        if any(k in msg for k in ("已签", "重复", "已领取", "刷新")):
            log.info(f"  今日已签到（{msg}），跳过")
            return True
        log.warning(f"  签到失败: {msg}")
        return False

    # ---------------- 任务领取 / 完成 ----------------

    def receive_task(self, task_code: str) -> bool:
        data = self.post_form(self.API_RECEIVE_V2, data={"taskCode": task_code}, app=True)
        if data and data.get("code") == 0:
            log.info(f"  领取任务成功: {task_code}")
            return True
        log.debug(f"  领取任务返回: {data}")
        return False

    def complete_task_v2(self, task_code: str) -> bool:
        data = self.post_form(self.API_COMPLETE_V2, data={"taskCode": task_code}, app=True)
        if data and data.get("code") == 0:
            log.info(f"  上报完成成功: {task_code}")
            return True
        log.warning(f"  上报完成失败 {task_code}: {(data or {}).get('message')}")
        return False

    def complete_channel(self, position: str, task_code: str = "") -> bool:
        """浏览频道页任务：position = jp_channel(追番) / tv_channel(影视)"""
        log.info(f"  模拟浏览频道 {position} ...")
        time.sleep(SETTINGS.get("browse_wait_seconds", 10))
        data = self.post_form(
            self.API_DELIVER_COMPLETE,
            data={"position": position, "c_locale": "zh_CN", "channel": "bili",
                  "s_locale": "zh_CN", "csrf": self.csrf},
        )
        if data and data.get("code") == 0:
            log.info(f"  浏览频道完成 ✓ ({position})")
            return True
        log.warning(f"  浏览频道失败 {position}: {(data or {}).get('message')}")
        return False

    def complete_vip_mall(self) -> bool:
        """浏览会员购页面10秒：show 域事件派发"""
        data = self._request(
            "POST", self.API_MALL_DISPATCH,
            json={"csrf": self.csrf, "eventId": self.MALL_EVENT_ID},
            headers={"Referer": "https://show.bilibili.com/", "Origin": "https://show.bilibili.com",
                     "Content-Type": "application/json",
                     "User-Agent": APP_USER_AGENT},
        )
        if data and data.get("code") == 0:
            log.info("  会员购浏览完成 ✓")
            return True
        log.warning(f"  会员购浏览失败: {(data or {}).get('message')}")
        return False

    # ---------------- 观看剧集（App 签名链路） ----------------

    def _app_sign(self, params: dict, app_key: str = None, app_sec: str = None) -> dict:
        """App 接口签名：sign = md5(按key排序的query + appsec)"""
        p = dict(params)
        p["appkey"] = app_key or self.APP_KEY
        p["ts"] = str(int(time.time()))
        query = urllib.parse.urlencode(sorted(p.items()))
        p["sign"] = hashlib.md5((query + (app_sec or self.APP_SEC)).encode()).hexdigest()
        return p

    def material_receive(self, ep_id: int, season_id: int) -> dict:
        """开始观看：领取本次观看凭证 task_id / token"""
        body = {
            "csrf": self.csrf,
            "spmid": "united.player-video-detail.0.0",
            "season_id": season_id,
            "activity_code": "",
            "ep_id": ep_id,
            "from_spmid": "activity.h5.0.0",
            "channel": "bili",
            # App 设备参数（缺这些服务端不会下发观看凭证）
            "bili_local_id": self.device_id, "build": "9130500", "buvid": self.buvid,
            "containerName": "AbstractWebActivity", "device": "phone",
            "deviceId": self.device_id, "deviceName": "samsungNexus",
            "devicePlatform": "Android10samsungNexus", "device_id": self.device_id,
            "device_name": "samsungNexus", "device_platform": "Android10samsungNexus",
            "disable_rcmd": "0", "fingerprint": self.device_id, "isPad": "false",
            "localFingerprint": self.device_id, "local_id": self.buvid,
            "mobi_app": "android", "modelName": "Nexus", "networkState": "2",
            "networkstate": "2", "osVer": "10", "platform": "android",
            "sessionID": SETTINGS.get("app_session_id", "120548f6"), "statusBarHeight": "77",
        }
        data = self.post_form(self.API_MATERIAL_RECEIVE, self._app_sign(body), app=True)
        cfg = ((data or {}).get("data") or {}).get("watch_count_down_cfg") or {}
        if cfg.get("task_id") and cfg.get("token"):
            log.info(f"  已领取观看凭证: task_id={cfg['task_id']} "
                     f"倒计时={int(cfg.get('milliseconds', 600000)) / 1000:.0f}s")
            return cfg
        log.warning(f"  未获取到观看凭证: {data}")
        return {}

    def deliver_watch_complete(self, task_id, token: str) -> bool:
        """上报完成观看（task_sign 与 sign 同为 App 签名算法）"""
        body = {
            "build": "9130500", "c_locale": "zh_CN", "channel": "bili", "disable_rcmd": "0",
            "mobi_app": "android", "platform": "android", "s_locale": "zh_CN",
            "statistics": json.dumps({"appId": 1, "platform": 3, "version": "9.13.0",
                                      "abtest": "", "mobi_app": "android"}, separators=(",", ":")),
            "task_id": str(task_id), "token": token,
            "timestamp": str(int(time.time() * 1000)),
        }
        p = dict(body)
        p["appkey"] = self.APP_KEY
        p["ts"] = str(int(time.time()))
        # 先算 task_sign（排除 sign / task_sign），再算 sign（含 task_sign）
        base = {k: v for k, v in p.items() if k not in ("sign", "task_sign")}
        p["task_sign"] = hashlib.md5(
            (urllib.parse.urlencode(sorted(base.items())) + self.APP_SEC).encode()).hexdigest()
        p["sign"] = hashlib.md5(
            (urllib.parse.urlencode(sorted(p.items())) + self.APP_SEC).encode()).hexdigest()

        data = self.post_form(self.API_DELIVER_COMPLETE, p, app=True)
        if data and data.get("code") == 0:
            log.info("  观看任务上报成功 ✓")
            return True
        log.warning(f"  观看任务上报失败: {(data or {}).get('message')}")
        return False

    # ---------------- 观看剧集（浏览器真实播放） ----------------

    def watch_by_browser(self, seconds: int) -> bool:
        url = SETTINGS.get("bangumi_url", "https://www.bilibili.com/bangumi/play/ep286838")
        log.info(f"  启动浏览器真实播放: {url}（{seconds}s）")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.error("  未安装 playwright，无法使用 browser 模式（pip install playwright）")
            return False

        ok = False
        headless = bool(SETTINGS.get("browser_headless", False))
        with sync_playwright() as p:
            try:
                launch_args = ["--autoplay-policy=no-user-gesture-required", "--mute-audio",
                               "--disable-blink-features=AutomationControlled",
                               "--no-first-run", "--no-default-browser-check"]
                if not headless:
                    # 注意：headless 模式下播放器不会发送心跳（页面不可见），
                    # 因此用「有头 + 窗口移到屏幕外」的方式，既不影响使用又能正常上报。
                    launch_args += ["--window-position=-2400,-2400", "--window-size=1280,800"]
                browser = p.chromium.launch(
                    channel=SETTINGS.get("browser_channel", "msedge"), headless=headless,
                    args=launch_args,
                )
            except Exception as e:
                log.error(f"  启动浏览器失败: {e}")
                return False
            ctx = browser.new_context(user_agent=DEFAULT_USER_AGENT,
                                      viewport={"width": 1280, "height": 800}, locale="zh-CN")
            ctx.add_cookies([{"name": k, "value": v, "domain": ".bilibili.com", "path": "/"}
                             for k, v in self.cookie.items() if v])
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(5000)
                for _ in range(5):
                    st = page.evaluate("""() => { const v=document.querySelector('video');
                        if(!v) return {found:false};
                        v.muted=true; v.volume=0; try{ v.play(); }catch(e){}
                        return {found:true, paused:v.paused, t:v.currentTime}; }""")
                    if st.get("found") and not st.get("paused"):
                        break
                    try:
                        box = page.locator("video").first.bounding_box()
                        if box:
                            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    except Exception:
                        pass
                    page.wait_for_timeout(3000)

                # 保证剩余时长充足（避免续播到片尾导致播放中断）
                st = page.evaluate("""(need) => { const v=document.querySelector('video');
                    if(!v) return null;
                    if (v.duration && v.duration - v.currentTime < need + 60) {
                        v.currentTime = 0; v.muted = true; try { v.play(); } catch(e) {}
                    }
                    return {paused:v.paused, t:Math.round(v.currentTime), dur:Math.round(v.duration||0)}; }""",
                    seconds)
                log.info(f"  开始播放: {st}")

                deadline = time.time() + seconds
                while time.time() < deadline:
                    page.wait_for_timeout(15000)
                    st = page.evaluate("""() => { const v=document.querySelector('video');
                        if(!v) return {found:false};
                        if(v.paused){ v.muted=true; try{ v.play(); }catch(e){} }
                        return {found:true, paused:v.paused, t:Math.round(v.currentTime)}; }""")
                    log.debug(f"    播放中: {st} 剩余{int(deadline - time.time())}s")
                ok = True
            except Exception as e:
                log.error(f"  浏览器播放异常: {e}")
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
        return ok

    # ---------------- 观看剧集总入口 ----------------

    def watch_episode(self) -> bool:
        """观看剧集任务：服务端核验连续观看 10 分钟，两种本地实现"""
        mode = SETTINGS.get("watch_mode", "app_api")
        seconds = int(SETTINGS.get("watch_duration_seconds", 660))
        if mode == "off":
            log.info("  [跳过] watch_mode=off，未执行观看剧集任务")
            return False
        if mode == "browser":
            return self.watch_by_browser(seconds)
        if mode == "app_api":
            return self.watch_by_app_api(seconds)
        log.warning(f"  未知 watch_mode: {mode}")
        return False

    def watch_by_app_api(self, seconds: int) -> bool:
        """App 签名链路（纯 HTTP，实测 2026-10-11 成功，+40 到账）：
        receive 领取任务 -> material/receive 领观看凭证 -> 等倒计时(10min) -> deliver/task/complete 上报
        """
        # 保险：确保任务已领取（state=1），否则服务端不会下发观看凭证
        combine = self.get_combine(quiet=True)
        for t in self.daily_tasks(combine):
            if t.get("task_code") == "ogvwatchnew" and t.get("state") == STATE_NOT_RECEIVED:
                self.receive_task("ogvwatchnew")
                time.sleep(3)
                break

        ep_id = int(SETTINGS.get("bangumi_ep_id", 286838))
        season_id = int(SETTINGS.get("bangumi_season_id", 28735))
        cfg = self.material_receive(ep_id, season_id)
        if not cfg:
            return False
        wait = max(seconds, int(cfg.get("milliseconds", 600000)) / 1000 + 15)
        log.info(f"  等待观看倒计时 {wait:.0f}s（服务端要求满 10 分钟才能上报，提前上报会被拒）")
        time.sleep(wait)
        for i in range(1, 4):
            if self.deliver_watch_complete(cfg["task_id"], cfg["token"]):
                return True
            log.warning(f"  第 {i} 次上报未成功，30 秒后重试")
            time.sleep(30)
        return False

    # ---------------- 单任务处理 ----------------

    def process_task(self, task: dict) -> None:
        code = task.get("task_code", "")
        title = task.get("title", code)
        state = task.get("state", -1)
        if any(kw in title for kw in BUY_KEYWORDS):
            log.info(f"  [跳过-购买类] {title}")
            return
        if state == STATE_COMPLETED:
            log.info(f"  [已完成] {title}")
            return

        log.info(f"  [处理] {title} (code={code}, state={state})")

        if state == STATE_NOT_RECEIVED:
            self.receive_task(code)
            time.sleep(random.uniform(1.0, 2.0))

        if code in ("animatetab", "filmtab"):
            self.complete_channel("jp_channel" if code == "animatetab" else "tv_channel", code)
        elif code == "vipmallview":
            self.complete_vip_mall()
        elif code == "dress-view":
            self.complete_task_v2("dress-view")
        elif code == "ogvwatchnew":
            self.watch_episode()
        else:
            self.complete_task_v2(code)
        time.sleep(random.uniform(1.5, 3.0))


# ============================================================
# 主流程
# ============================================================

def all_done(tasks: list) -> bool:
    """所有非购买类任务是否都已完成"""
    pending = [t for t in tasks
               if not any(kw in t.get("title", "") for kw in BUY_KEYWORDS)
               and t.get("state") != STATE_COMPLETED]
    return not pending


def today_point_gain(client) -> int:
    """从积分流水统计今日新增大积分"""
    today = datetime.now().strftime("%Y-%m-%d")
    data = client.get(client.API_POINT_LIST,
                      params={"csrf": client.csrf, "change_type": 1, "pn": 1, "ps": 20})
    total = 0
    for it in ((data or {}).get("data") or {}).get("big_point_list", []) or []:
        ts = it.get("change_time")
        if ts and datetime.fromtimestamp(ts).strftime("%Y-%m-%d") == today:
            total += int(it.get("point") or 0)
    return total


def main() -> int:
    ap = argparse.ArgumentParser(description="B站大会员大积分自动获取脚本")
    ap.add_argument("--force", action="store_true", help="忽略今日已运行记录，强制再跑一次")
    ap.add_argument("--skip-watch", action="store_true", help="本次跳过观看剧集任务")
    ap.add_argument("--watch-mode", choices=["browser", "app_api", "off"], help="覆盖 config 中的 watch_mode")
    ap.add_argument("--skip-oqi", action="store_true", help="本次跳过欧气盲盒/魔力战令模块")
    ap.add_argument("--claim-rewards", action="store_true", help="强制领取全部战令等级奖励（不限周日）")
    args = ap.parse_args()

    if args.watch_mode:
        SETTINGS["watch_mode"] = args.watch_mode
    if args.skip_watch:
        SETTINGS["watch_mode"] = "off"

    today = datetime.now().strftime("%Y-%m-%d")
    state = load_state()
    if state.get("date") != today:
        state = {"date": today, "attempts": 0, "result": "unknown"}

    log.info("=" * 62)
    log.info("B站大会员大积分自动脚本启动  " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 62)
    run_start = time.time()

    max_attempts = int(SETTINGS.get("max_attempts_per_day", 2))
    if not args.force:
        if state.get("result") == "ok":
            log.info(f"今日（{today}）任务已全部完成，跳过本次运行（--force 可强制重跑）")
            return 0
        if state.get("attempts", 0) >= max_attempts:
            log.info(f"今日已尝试 {state['attempts']} 次（上限 {max_attempts}），跳过")
            return 0

    state["attempts"] = state.get("attempts", 0) + 1
    save_state(state)

    cookie = CONFIG.get("cookie", {})
    missing = [k for k in ("SESSDATA", "bili_jct", "DedeUserID")
               if not cookie.get(k) or str(cookie[k]).startswith("在此填入")]
    if missing:
        log.error(f"请先在 config.json 中填写 Cookie: 缺少 {', '.join(missing)}")
        return 1

    client = BiliVipPointClient(cookie)
    if not client.check_login():
        return 1

    combine = client.get_combine()
    if not combine:
        log.error("无法获取任务列表，退出")
        return 1
    point_before = (combine.get("point_info") or {}).get("point")

    client.do_sign(combine)
    time.sleep(random.uniform(1.5, 3))

    combine = client.get_combine(quiet=True)
    tasks = client.daily_tasks(combine)
    log.info("-" * 46)
    log.info(f"【日常任务】共 {len(tasks)} 项")
    for t in tasks:
        client.process_task(t)

    # 频道任务兜底：部分账号的任务列表偶发不展示 jp_channel/tv_channel，但接口仍可得分
    extra_channels = []
    if SETTINGS.get("try_channel_tasks", True):
        codes = {t.get("task_code") for t in tasks}
        credited = set(state.get("channel_done", []))
        for code, pos in (("animatetab", "jp_channel"), ("filmtab", "tv_channel")):
            name = "浏览追番频道10秒" if code == "animatetab" else "浏览影视频道10秒"
            if code in codes:
                continue
            if code in credited:
                log.info(f"  [兜底] {code} 今日已上报，跳过")
                extra_channels.append(name)
                continue
            log.info(f"  [兜底] 任务列表未包含 {code}，尝试直接上报浏览 {pos}")
            if client.complete_channel(pos, code):
                credited.add(code)
                state["channel_done"] = sorted(credited)
                save_state(state)
                extra_channels.append(name)

    # ---------------- 欧气盲盒 / 魔力战令 ----------------
    oqi_result = None
    oqi_cfg = SETTINGS.get("oqi_blindbox", {}) or {}
    if oqi_cfg.get("enabled", True) and not args.skip_oqi:
        try:
            from oqi_blindbox import OqiBlindBox
            oqi = OqiBlindBox(cookie, oqi_cfg, log)
            oqi_result = oqi.run(force_claim_rewards=args.claim_rewards)
        except Exception as e:
            log.warning(f"欧气盲盒模块异常（不影响大积分任务）: {e}")
    elif args.skip_oqi:
        log.info("已按要求跳过欧气盲盒/魔力战令模块（--skip-oqi）")

    # ---------------- 结果汇总 ----------------
    time.sleep(2)
    final = client.get_combine(quiet=True)
    ftasks = client.daily_tasks(final)
    log.info("-" * 46)
    log.info("【最终状态】")
    for t in ftasks:
        s = {0: "未领取", 1: "进行中", 3: "已完成"}.get(t.get("state"), "未知")
        mark = "√" if t.get("state") == STATE_COMPLETED else ("-" if any(
            kw in t.get("title", "") for kw in BUY_KEYWORDS) else "×")
        log.info(f"  {mark} {t.get('title')}: {s}")

    done = all_done(ftasks)
    state["result"] = "ok" if done else "partial"
    state["point"] = (final.get("point_info") or {}).get("point")
    if oqi_result:
        state["oqi"] = {
            "sign": oqi_result.get("sign"),
            "tasks_done": oqi_result.get("tasks_done"),
            "tasks_claimed": oqi_result.get("tasks_claimed"),
            "grade_claimed": oqi_result.get("grade_claimed"),
            "crystal": oqi_result.get("crystal", {}),
        }
    save_state(state)
    log.info(f"当前大积分: {state.get('point')}")
    if oqi_result:
        c = oqi_result.get("crystal") or {}
        log.info(f"当前魔晶: {c.get('total')}（可用 {c.get('available')}，"
                 f"24h 内过期 {c.get('expiring')}）")
    log.info("全部非购买任务已完成 ✓" if done else "存在未完成任务（可稍后重跑，或查看说明）")

    # ---------------- Server酱每日简报 ----------------
    try:
        from notify import send_briefing
        task_report = []
        for t in ftasks:
            title = t.get("title", "")
            if any(kw in title for kw in BUY_KEYWORDS):
                st = "skip_buy"
            elif t.get("state") == STATE_COMPLETED:
                st = "done"
            else:
                st = "fail"
            task_report.append({"title": title, "state": st, "subtitle": t.get("subtitle")})
        report = {
            "end_time": datetime.now(),
            "duration": time.time() - run_start,
            "ok": done,
            "points": {"before": point_before, "after": state.get("point"),
                       "today_gained": today_point_gain(client)},
            "tasks": task_report,
            "extra_channels": extra_channels,
            "oqi": oqi_result or {},
            "crystal": (oqi_result or {}).get("crystal") or {},
        }
        send_briefing(CONFIG.get("notify", {}), report)
    except Exception as e:
        log.warning(f"简报推送失败（不影响任务结果）: {e}")

    log.info("=" * 62)
    return 0 if done else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.info("用户中断")
        sys.exit(130)
    except Exception as e:
        log.exception(f"脚本异常: {e}")
        sys.exit(1)
