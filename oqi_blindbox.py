#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B站「会员购 - 欧气盲盒 / 魔力战令」自动化模块
================================================================
功能（全部纯 HTTP，不使用 adb、不使用安卓模拟器，不做任何下单/支付动作）：

1. 签到           POST /magic-c/sign/detail + /magic-c/sign/achieve      ✅ 实测成功
                  （注意前缀是 /magic-c；写成 /mall-c 会被网关拦成 502）
2. [每日] 任务
   · 浏览类（欧气爆款种草10秒）：show.bilibili.com hercules 事件链       ✅ 实测成功
       GET  /api/activity/hercules/task/report-detail?taskId=<herculesId>
       等待 extraData.eventTime 秒
       POST /api/activity/fire/common/event/dispatch  {"eventId": ...}
   · 领奖：POST /mall-c/blind_box_activity/task/mls_pm/batch_receive_prize ✅ 实测成功
       {"prizeReceiveReqList":[{"activityId":..., "userTaskId":...}]}
   · 许愿类（欧气许愿好物1次）：❌ 仅 App 可用
       前端源码存在硬门槛 if(!isBiliApp) return；服务端 /magic-c-search/blind_box/wish/create 恒返回 502；
       /magic-c/items/wish/set（收藏"想要"）不会计入该任务。脚本会明确提示需在 App 内完成。
3. 周日等级奖励   POST /mall-magic-c/internet/mls_pm/war_order/prize_receive ✅ 载荷已确认
                  {"woActivityId": "<activityId>"}   ← 不带 rewardIdList 即"全部领取"
4. 魔晶统计       GET  /magic-c/magic_crystal/get_magic_crystal           （总数/即将过期）
                  GET  /magic-c/magic_crystal/list_page_magic_crystal     （按到期时间分桶）
                  GET  /magic-c/magic_crystal/list_page_magic_crystal_expire_record（历史过期记录）

已实测（2026-10-12）：
  · 战令任务列表 / 等级奖励列表 / 魔晶余额 / 魔晶明细  全部 code=0
  · 签到 code=0，signed false→true，魔晶 2→5
  · [每日] 浏览类任务 完成链路 code=0，任务 status 1 → 4，领奖后魔力值 0 → 5
  · 任务奖励重复领取不会重复加分（服务端幂等）
"""

import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timedelta

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")

MALL = "https://mall.bilibili.com"
SHOW = "https://show.bilibili.com"
# 注意：签到/魔晶用的是 /magic-c（不是 /mall-c）；战令/活动用 /mall-magic-c
MAGIC_C = "/magic-c"
MAGIC_C_ALT = "/mall-c"
MAGIC_MALL_C = "/mall-magic-c"
MAGIC_SEARCH = "/magic-c-search"

APP_UA = ("Mozilla/5.0 (Linux; Android 12; PGEM10 Build/V417IR; wv) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Version/4.0 Chrome/110.0.5481.154 Mobile Safari/537.36 "
          "os/android model/PGEM10 build/9130500 osVer/12 sdkInt/32 network/2 BiliApp/9130500 "
          "mobi_app/android channel/bili")

# 任务状态：1=未完成，2/3/4=已完成（不同子状态），5=不可用
TASK_TODO = 1
TASK_DONE = (2, 3, 4)
TASK_DISABLED = 5


class OqiBlindBox:
    """欧气盲盒 / 魔力战令 客户端"""

    def __init__(self, cookie: dict, settings: dict, logger: logging.Logger = None):
        self.cookie = cookie
        self.csrf = cookie.get("bili_jct", "")
        self.mid = cookie.get("DedeUserID", "")
        self.cfg = settings or {}
        self.log = logger or logging.getLogger("BiliVipPoint")
        self.interval = self.cfg.get("request_interval", [1.5, 3.5])
        self.timeout = self.cfg.get("request_timeout", 25)

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": APP_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Content-Type": "application/json",
            "Origin": MALL,
            "Referer": MALL + "/neul-next/index.html?page=boxmagicorder_premium",
        })
        for k, v in cookie.items():
            if v:
                self.session.cookies.set(k, v)

    # ---------------- 基础 ----------------

    def _sleep(self):
        lo, hi = (self.interval + [3.5])[:2] if isinstance(self.interval, list) else (1.5, 3.5)
        time.sleep(random.uniform(float(lo), float(hi)))

    def _req(self, method: str, url: str, **kw):
        kw.setdefault("timeout", self.timeout)
        for attempt in range(1, 3):
            try:
                r = self.session.request(method, url, **kw)
                if r.status_code == 502:
                    self.log.warning(f"  [盲盒] {method} {url} -> 502（网关拦截，稍后重试）")
                    time.sleep(2 * attempt)
                    continue
                if r.status_code == 405 and method == "POST":
                    self.log.warning(f"  [盲盒] {method} {url} -> 405（接口只接受其它方法）")
                    return None
                try:
                    return r.json()
                except ValueError:
                    self.log.debug(f"  [盲盒] {method} {url} 非 JSON: {r.status_code} {r.text[:120]}")
                    return None
            except requests.RequestException as e:
                self.log.warning(f"  [盲盒] {method} {url} 网络错误({attempt}): {e}")
                time.sleep(2 * attempt)
        return None

    def _get(self, path, params=None):
        return self._req("GET", MALL + path, params=params)

    def _post(self, path, payload=None, params=None):
        return self._req("POST", MALL + path, json=payload if payload is not None else {}, params=params)

    # ---------------- 魔晶 ----------------

    def get_crystal(self) -> dict:
        j = self._get(f"{MAGIC_C}/magic_crystal/get_magic_crystal")
        return (j or {}).get("data") or {}

    def crystal_detail(self, pages: int = 4) -> list:
        """魔晶明细（每条含 totalAmount / useEndTime；注意 remainAmount 不会递减）"""
        out = []
        for page in range(1, pages + 1):
            j = self._get(f"{MAGIC_C}/magic_crystal/list_page_magic_crystal",
                          params={"v": int(time.time() * 1000), "status": 3,
                                  "pageNum": page, "pageSize": 50})
            lst = (((j or {}).get("data") or {}).get("pageInfo") or {}).get("list") or []
            out.extend(lst)
            if len(lst) < 50:
                break
            self._sleep()
        return out

    def crystal_summary(self) -> dict:
        """魔晶汇总（修正版）

        ⚠️ 重要：明细接口里每条的 remainAmount 恒等于「该批次授予量」，**不会随消耗递减**，
           直接求和会严重偏大（实测：求和 55，真实可用 4）。客户端「魔晶总额/当前可用」取的是
           汇总接口的 remainAmount / effectAmount。
        因此：
          · 总额 / 可用  → 汇总接口 remainAmount / effectAmount（权威）
          · 24h 内过期   → 汇总接口 expiringAmount（权威）
          · 到期分布     → 按「先消耗最早到期批次」推算：把可用额度从最晚到期的批次往前填，
                          再用服务端 expiringAmount 校正最早到期的桶，保证三者自洽
        """
        info = self.get_crystal()
        self._sleep()
        detail = self.crystal_detail()

        avail = int(info.get("effectAmount") if info.get("effectAmount") is not None
                    else (info.get("remainAmount") or 0))
        total = int(info.get("remainAmount") if info.get("remainAmount") is not None else avail)
        expiring_srv = int(info.get("expiringAmount") or 0)

        now_ts = time.time()
        today = datetime.now().strftime("%Y-%m-%d")

        # 1) 未过期批次的"授予量"按到期日聚合
        granted = {}
        for it in detail:
            end = it.get("useEndTime")
            if not end or float(end) <= now_ts:       # 已过期的不参与
                continue
            amt = int(it.get("totalAmount") or it.get("remainAmount") or 0)
            if amt <= 0:
                continue
            key = datetime.fromtimestamp(end).strftime("%Y-%m-%d")
            granted[key] = granted.get(key, 0) + amt

        # 2) 「先消耗最早到期」→ 等价于把可用额度从最晚到期的批次开始往前填
        alloc = {}
        remaining = avail
        for key in sorted(granted, reverse=True):
            take = min(remaining, granted[key])
            alloc[key] = take
            remaining -= take
            if remaining <= 0:
                break

        # 3) 用服务端 expiringAmount 校正"最早到期（今天）"的桶
        diff = expiring_srv - alloc.get(today, 0)
        if diff > 0 and avail > 0:
            moved = 0
            for key in sorted(alloc, reverse=True):
                if key == today or moved >= diff:
                    continue
                take = min(diff - moved, alloc[key])
                alloc[key] -= take
                moved += take
            alloc[today] = alloc.get(today, 0) + moved
        elif diff < 0 and alloc.get(today, 0) > 0:
            # 服务端说今天到期更少 → 多出来的部分挪到最晚到期的桶
            back = min(-diff, alloc.get(today, 0))
            alloc[today] -= back
            latest = max(granted) if granted else today
            alloc[latest] = alloc.get(latest, 0) + back

        alloc = {k: v for k, v in alloc.items() if v > 0}

        return {
            "total": total,                 # 魔晶总额（客户端"魔晶总额"同源）
            "available": avail,             # 当前可用
            "expiring": expiring_srv,       # 24 小时内过期（服务端口径）
            "buckets": dict(sorted(alloc.items())),
            "expiring_today": expiring_srv,  # 兼容旧字段名
            "detail_count": len(detail),
            "granted_total": sum(granted.values()),
        }

    # ---------------- 签到 ----------------

    def sign_detail(self) -> dict:
        """签到状态（前缀 /magic-c，旧前缀 /mall-c 作为兜底）"""
        for prefix in (MAGIC_C, MAGIC_C_ALT):
            j = self._post(f"{prefix}/sign/detail", {})
            if j and j.get("code") == 0:
                return j.get("data") or {}
        return {}

    def sign_achieve(self, task_id=None) -> dict:
        payload = {"taskId": task_id} if task_id else {}
        for prefix in (MAGIC_C, MAGIC_C_ALT):
            j = self._post(f"{prefix}/sign/achieve", payload)
            if j and j.get("code") is not None:
                return j
        return {}

    def do_sign(self) -> bool:
        """欧气盲盒/战令 签到"""
        self.log.info("  [盲盒] 签到 …")
        detail = self.sign_detail()
        if not detail:
            self.log.warning("  [盲盒] 签到接口暂不可用，已跳过（可在 App 内手动签到）")
            return False
        if detail.get("signed"):
            self.log.info(f"  [盲盒] 今日已签到，跳过（{detail.get('taskName', '')}）")
            return True
        res = self.sign_achieve(detail.get("taskId"))
        if res.get("code") == 0:
            self.log.info("  [盲盒] 签到成功 ✓")
            return True
        self.log.warning(f"  [盲盒] 签到失败: {res.get('code')} {res.get('message')}")
        return False

    # ---------------- 魔力战令 ----------------

    def war_order_head(self) -> dict:
        j = self._post(f"{MAGIC_MALL_C}/internet/mls_pm/war_order/activity_head_info", {})
        return (j or {}).get("data") or {}

    def war_order_tasks(self) -> dict:
        j = self._post(f"{MAGIC_MALL_C}/internet/mls_pm/war_order/activity_task_list", {})
        return (j or {}).get("data") or {}

    def war_order_grades(self) -> dict:
        j = self._post(f"{MAGIC_MALL_C}/internet/mls_pm/war_order/activity_grade_list", {})
        return (j or {}).get("data") or {}

    @staticmethod
    def daily_tasks(task_data: dict) -> list:
        tasks = ((task_data.get("taskList") or {}).get("normalTasks") or [])
        return [t for t in tasks if "[每日]" in str(t.get("taskName", ""))]

    # ---------------- hercules 事件链（浏览类任务） ----------------

    def complete_by_hercules(self, guide_link: str) -> bool:
        """打开 guideLink 上的 hercules 任务：report-detail → 等待 → event/dispatch"""
        if not guide_link or "herculesId=" not in guide_link:
            return False
        herc_id = guide_link.split("herculesId=")[-1].split("&")[0]
        j = self._req("GET", SHOW + "/api/activity/hercules/task/report-detail",
                      params={"taskId": herc_id, "_": int(time.time() * 1000)})
        d = (j or {}).get("data") or {}
        if not d:
            self.log.warning(f"  [盲盒] 任务详情获取失败: {herc_id}")
            return False
        if not d.get("needDispatch"):
            self.log.info("  [盲盒] 该任务无需事件派发（可能已完成）")
            return True
        event_id = d.get("eventId")
        wait = int(((d.get("extraData") or {}).get("eventTime") or 10))
        margin = float(self.cfg.get("browse_wait_margin", 3))
        self.log.info(f"  [盲盒] 模拟浏览 {wait}s（eventId={event_id}）…")
        time.sleep(wait + margin)
        r = self._req("POST", SHOW + "/api/activity/fire/common/event/dispatch",
                      json={"eventId": event_id, "csrf": self.csrf},
                      headers={"Content-Type": "application/json",
                               "Origin": SHOW, "Referer": MALL + "/"})
        if (r or {}).get("code") == 0:
            self.log.info("  [盲盒] 浏览事件上报成功 ✓")
            return True
        self.log.warning(f"  [盲盒] 事件上报失败: {(r or {}).get('message')}")
        return False

    # ---------------- 领奖 ----------------

    def claim_task_reward(self, activity_id: str, user_task_id) -> bool:
        j = self._post(f"{MAGIC_C}/blind_box_activity/task/mls_pm/batch_receive_prize",
                       {"prizeReceiveReqList": [{"activityId": activity_id,
                                                 "userTaskId": user_task_id}]})
        if (j or {}).get("code") == 0:
            self.log.info(f"  [盲盒] 任务奖励已领取 (userTaskId={user_task_id}) ✓")
            return True
        self.log.debug(f"  [盲盒] 领取任务奖励返回: {j}")
        return False

    def claim_all_grade_rewards(self, wo_activity_id: str) -> bool:
        """领取「奖励」中全部可领取的战令等级奖励（不带 rewardIdList = 全部领取）"""
        if not wo_activity_id:
            return False
        self.log.info("  [盲盒] 领取全部战令等级奖励 …")
        j = self._post(f"{MAGIC_MALL_C}/internet/mls_pm/war_order/prize_receive",
                       {"woActivityId": wo_activity_id})
        if (j or {}).get("code") == 0:
            prizes = (j.get("data") or {}).get("prizeList") or []
            self.log.info(f"  [盲盒] 战令等级奖励领取成功 ✓ 获得 {len(prizes)} 项")
            for p in prizes[:8]:
                self.log.info(f"        · {p.get('prizeName')} ×{p.get('prizeNum')}")
            return True
        msg = (j or {}).get("message")
        if (j or {}).get("code") == 81102001 or (msg and ("没有" in msg or "无可领取" in msg)):
            self.log.info(f"  [盲盒] 当前没有可领取的等级奖励（{msg}）")
        else:
            self.log.warning(f"  [盲盒] 等级奖励领取返回: {(j or {}).get('code')} {msg}")
        return False

    # ---------------- 主流程 ----------------

    def run(self, force_claim_rewards: bool = False) -> dict:
        result = {"sign": False, "tasks_done": 0, "tasks_claimed": 0,
                  "grade_claimed": False, "crystal": {},
                  "daily": [], "magic_value": None, "grade": None}
        self.log.info("-" * 46)
        self.log.info("【欧气盲盒 / 魔力战令】")

        # 1) 签到
        if self.cfg.get("do_sign", True):
            try:
                result["sign"] = self.do_sign()
            except Exception as e:
                self.log.warning(f"  [盲盒] 签到异常: {e}")
            self._sleep()

        # 2) 战令信息 + [每日] 任务
        head, tasks = {}, {}
        try:
            head = self.war_order_head()
            self._sleep()
            tasks = self.war_order_tasks()
        except Exception as e:
            self.log.warning(f"  [盲盒] 战令数据获取异常: {e}")

        wo_id = head.get("activityId") or ""
        result["magic_value"] = tasks.get("currentMagicValue")
        result["grade"] = tasks.get("currentGrade")
        if wo_id:
            self.log.info(f"  战令活动: {wo_id} 状态={head.get('activityStatus')} "
                          f"魔力值={tasks.get('currentMagicValue')} 等级={tasks.get('currentGrade')}")

        if self.cfg.get("do_daily_tasks", True) and tasks:
            for t in self.daily_tasks(tasks):
                name = t.get("taskName")
                status = t.get("userTaskStatus")
                prize = t.get("prize") or {}
                reward_txt = ""
                if prize.get("prizeName"):
                    reward_txt = f"+{prize.get('prizeNum')} {prize.get('prizeName')}"
                entry = {"name": name, "done": False, "reward": reward_txt}
                result["daily"].append(entry)

                if status == TASK_DISABLED:
                    self.log.info(f"  [跳过] {name}（当前不可用）")
                    continue
                if status == TASK_TODO:
                    self.log.info(f"  [每日任务] {name}")
                    link = t.get("guideLink") or ""
                    if "herculesId=" in link:
                        ok = self.complete_by_hercules(link)
                    elif "许愿" in str(name):
                        self.log.info("      该任务只能在 B站 App 内完成"
                                      "（服务端许愿写接口对 PC 返回 502，前端也有 isBiliApp 门槛）")
                        ok = False
                    else:
                        self.log.info(f"      该任务需在 App/网页内手动完成：{name}")
                        ok = False
                    self._sleep()
                    if not ok:
                        continue
                # 完成（或本来就已完成）→ 领奖
                if str(t.get("activityId", "")).startswith("mls_"):
                    if self.claim_task_reward(t.get("activityId"), t.get("userTaskId")):
                        result["tasks_claimed"] += 1
                        self._sleep()
                result["tasks_done"] += 1
                entry["done"] = True

        # 3) 周日领取全部战令等级奖励
        claim_mode = str(self.cfg.get("claim_grade_rewards", "sunday")).lower()
        is_sunday = datetime.now().weekday() == 6
        if claim_mode == "always" or (claim_mode == "sunday" and is_sunday) or force_claim_rewards:
            try:
                grades = self.war_order_grades()
                unclaimed = int(grades.get("unReceivedPrizeCount") or 0)
                self.log.info(f"  可领取等级奖励: {unclaimed} 项"
                              f"{'（周日全部领取）' if is_sunday else ''}")
                if unclaimed > 0 or force_claim_rewards:
                    result["grade_claimed"] = self.claim_all_grade_rewards(wo_id or grades.get("activityId"))
                else:
                    self.log.info("  暂无已解锁待领取的等级奖励")
            except Exception as e:
                self.log.warning(f"  [盲盒] 等级奖励处理异常: {e}")
        elif claim_mode != "off":
            self.log.info("  非周日，跳过战令等级奖励领取（config: claim_grade_rewards=sunday）")

        # 4) 魔晶统计
        try:
            self._sleep()
            summ = self.crystal_summary()
            result["crystal"] = summ
            self.log.info("  【魔晶统计】")
            self.log.info(f"    魔晶总额: {summ.get('total')}　当前可用: {summ.get('available')}")
            self.log.info(f"    24 小时内过期: {summ.get('expiring')}（服务端口径）")
            if summ.get("buckets"):
                dist = " · ".join(f"{k[5:]} → {v}" for k, v in list(summ["buckets"].items())[:12])
                self.log.info(f"    到期分布（按先消耗最早到期推算）: {dist}")
        except Exception as e:
            self.log.warning(f"  [盲盒] 魔晶统计异常: {e}")

        return result


# ---------------- 独立运行入口 ----------------
if __name__ == "__main__":
    import argparse

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    ap = argparse.ArgumentParser(description="欧气盲盒 / 魔力战令 自动化（独立运行）")
    ap.add_argument("--claim-rewards", action="store_true", help="强制领取全部战令等级奖励")
    a = ap.parse_args()

    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    client = OqiBlindBox(cfg.get("cookie", {}), cfg.get("settings", {}).get("oqi_blindbox", {}),
                         logging.getLogger("Oqi"))
    out = client.run(force_claim_rewards=a.claim_rewards)
    print("\n结果:", json.dumps(out, ensure_ascii=False, indent=2))
