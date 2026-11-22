#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Server酱 推送模块 + 每日简报排版
================================================================
Server酱 官方接口：
  POST https://sctapi.ftqq.com/<SENDKEY>.send
  data = {"text": 标题, "desp": Markdown 正文}

支持两种 Key：
  · SCT 开头  → Server酱 Turbo   https://sctapi.ftqq.com/<key>.send
  · sctp 开头 → Server酱³        https://sctp<uid>.push.ft07.com/send/<key>.send
"""

import logging
import re
import time
from datetime import datetime

import requests

log = logging.getLogger("BiliVipPoint")

STATE_MARK = {
    "done": "✅",
    "skip": "⏭️",
    "fail": "❌",
    "pending": "🕒",
    "info": "ℹ️",
}


# ============================================================
# Server酱
# ============================================================

def send_server_chan(send_key: str, title: str, desp: str, timeout: int = 30) -> bool:
    """通过 Server酱 推送一条消息；返回是否成功"""
    key = (send_key or "").strip()
    if not key:
        log.warning("未配置 Server酱 SendKey，跳过推送")
        return False

    if key.lower().startswith("sctp"):          # Server酱³
        m = re.match(r"sctp(\d+)", key, re.I)
        url = (f"https://sctp{m.group(1)}.push.ft07.com/send/{key}.send"
               if m else f"https://sctapi.ftqq.com/{key}.send")
    else:                                        # Server酱 Turbo（SCT 开头）
        url = f"https://sctapi.ftqq.com/{key}.send"

    try:
        r = requests.post(url, data={"text": title, "desp": desp}, timeout=timeout)
        if r.status_code != 200:
            log.warning(f"Server酱推送失败: HTTP {r.status_code} {r.text[:160]}")
            return False
        try:
            j = r.json()
        except ValueError:
            log.info("Server酱推送已发送（返回非 JSON）")
            return True
        code = j.get("code")
        errno = (j.get("data") or {}).get("errno")
        if code == 0 and errno in (0, None):
            log.info(f"Server酱简报已推送 ✓（pushid={(j.get('data') or {}).get('pushid')}）")
            return True
        log.warning(f"Server酱推送返回异常: code={code} msg={j.get('message')} data={j.get('data')}")
        return False
    except requests.RequestException as e:
        log.warning(f"Server酱推送异常: {e}")
        return False


# ============================================================
# 简报排版
# ============================================================

def _reward_label(task: dict) -> str:
    """从任务 subtitle 里提取 '+40大积分' 这类奖励标签"""
    subtitle = str(task.get("subtitle") or "")
    m = re.search(r"\+\s*(\d+)\s*大积分", subtitle)
    return f"+{m.group(1)}" if m else ""


def build_briefing(report: dict) -> tuple:
    """把运行结果整理成 (标题, Markdown 正文)"""
    now = report.get("end_time") or datetime.now()
    date_str = now.strftime("%m-%d")
    title = f"每日任务简报 {date_str}"

    lines = []
    points = report.get("points") or {}
    tasks = report.get("tasks") or []
    done_cnt = sum(1 for t in tasks if t.get("state") == "done")
    total_cnt = len([t for t in tasks if t.get("state") != "skip_buy"])

    # ---- 标题块 ----
    lines.append("# [1/1] B站大会员每日任务结果")
    lines.append("")
    lines.append(f"**📋 每日任务完成: {done_cnt}/{total_cnt}**")
    lines.append("")

    # ---- 大积分任务明细 ----
    for t in tasks:
        st = t.get("state")
        name = t.get("title", "")
        rw = t.get("reward") or _reward_label(t)
        if st == "done":
            lines.append(f"- ✅ {name}: 已完成 {rw}".rstrip())
        elif st == "skip_buy":
            lines.append(f"- ⏭️ {name}: 按设置跳过（购买类）")
        elif st == "fail":
            lines.append(f"- ❌ {name}: 未完成")
        else:
            lines.append(f"- 🕒 {name}: 进行中")

    # 兜底上报的频道任务
    for extra in report.get("extra_channels") or []:
        lines.append(f"- ✅ {extra}: 已完成（兜底上报）")

    lines.append("")
    gained = points.get("today_gained")
    gained_str = f"，今日 +{gained}" if gained not in (None, "") else ""
    lines.append(f"**📊 任务汇总:** 完成 {done_cnt} 个 · 当前大积分 **{points.get('after', '?')}**{gained_str}")

    # ---- 欧气盲盒 / 魔力战令 ----
    oqi = report.get("oqi") or {}
    if oqi:
        lines.append("")
        lines.append("---")
        lines.append("## 🎁 欧气盲盒 / 魔力战令")
        lines.append("")
        if oqi.get("sign"):
            lines.append("- ✅ 签到: 已完成")
        elif oqi.get("sign") is False:
            lines.append("- ❌ 签到: 未完成（可稍后重试或在 App 内签到）")
        for d in oqi.get("daily") or []:
            if d.get("done"):
                lines.append(f"- ✅ {d.get('name')}: 已完成 {d.get('reward') or ''}".rstrip())
            else:
                lines.append(f"- ❌ {d.get('name')}: 需在 App 内完成")
        if oqi.get("magic_value") is not None:
            lines.append(f"- 🎯 当前魔力值: **{oqi.get('magic_value')}**（等级 {oqi.get('grade', '?')}）")
        if oqi.get("grade_claimed"):
            lines.append("- 🏆 已领取全部战令等级奖励")

    # ---- 魔晶 ----
    crystal = (oqi.get("crystal") or report.get("crystal") or {})
    if crystal:
        lines.append("")
        total = crystal.get("total")
        avail = crystal.get("available")
        expiring = crystal.get("expiring")
        line = f"**🧊 魔晶:** 总额 **{total if total is not None else '?'}**"
        if avail is not None and avail != total:
            line += f"（当前可用 **{avail}**）"
        lines.append(line)
        lines.append(f"　24 小时内过期: **{expiring if expiring is not None else '?'}**")
        buckets = crystal.get("buckets") or {}
        if buckets:
            bucket_str = " · ".join(f"{k[5:]} → {v}" for k, v in list(buckets.items())[:5])
            lines.append(f"　到期分布（按先消耗最早到期推算）: {bucket_str}")

    # ---- 运行信息 ----
    lines.append("")
    lines.append("---")
    dur = report.get("duration")
    dur_str = f" · 耗时 {int(dur // 60)}分{int(dur % 60)}秒" if dur else ""
    lines.append(f"⏰ 运行时间: {now.strftime('%Y-%m-%d %H:%M:%S')}{dur_str}")
    if report.get("ok") is True:
        lines.append("🎉 本次运行全部成功")
    elif report.get("ok") is False:
        lines.append("⚠️ 本次运行存在未完成项，可稍后重跑（`--force`）")

    return title, "\n".join(lines)


def send_briefing(cfg: dict, report: dict) -> bool:
    """按配置发送简报"""
    cfg = cfg or {}
    if not cfg.get("enabled", True):
        log.info("已关闭简报推送（notify.enabled=false）")
        return False
    title, desp = build_briefing(report)
    prefix = str(cfg.get("title_prefix") if cfg.get("title_prefix") is not None else "B站").strip()
    if prefix and not title.startswith(prefix):
        title = f"{prefix}{title}"
    log.info(f"推送每日简报到 Server酱: {title}")
    log.debug("简报正文:\n" + desp)
    return send_server_chan(cfg.get("send_key", ""), title, desp,
                            timeout=int(cfg.get("timeout", 30)))


# ---------------- 独立测试 ----------------
if __name__ == "__main__":
    import json
    import os
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")

    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "config.json"), encoding="utf-8") as f:
        conf = json.load(f)

    demo = {
        "end_time": datetime.now(),
        "duration": 723,
        "ok": True,
        "points": {"before": 325, "after": 415, "today_gained": 90},
        "tasks": [
            {"title": "观看剧集内容", "state": "done", "reward": "+40"},
            {"title": "浏览会员购页面10秒", "state": "done", "reward": "+10"},
            {"title": "浏览装扮商城主页", "state": "done", "reward": "+10"},
            {"title": "购买单点付费影片", "state": "skip_buy"},
        ],
        "extra_channels": ["浏览追番频道10秒", "浏览影视频道10秒"],
        "oqi": {"sign": True, "magic_value": 5, "grade": 0, "grade_claimed": False,
                "daily": [{"name": "[每日] 欧气爆款种草10秒", "done": True, "reward": "+5 魔力值"},
                          {"name": "[每日] 欧气许愿好物1次", "done": False}]},
        "crystal": {"total": 4, "available": 4, "expiring": 1,
                    "buckets": {"2026-10-19": 1, "2026-10-22": 3}},
    }
    t, d = build_briefing(demo)
    print("标题:", t)
    print("-" * 60)
    print(d)
    print("-" * 60)
    print("实际推送:", send_briefing(conf.get("notify", {}), demo))
