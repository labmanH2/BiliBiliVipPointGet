# BiliBiliVipPointGet

**B 站「大会员大积分」+「会员购·欧气盲盒/魔力战令」的本地每日自动化脚本（Windows）**

纯本地运行，不使用 adb、不使用安卓模拟器；只做**签到、浏览、领任务奖励**这类免费动作，
**不包含任何下单、支付、抽盒、回收**逻辑。

> ⚠️ 仅供个人学习与自用。使用前请阅读[免责声明](#免责声明)与[风险提示](#风险提示)。
> 接口属于非公开接口，随时可能变更；因使用本脚本造成的任何后果由使用者自行承担。

---

## 一、功能状态

### 1. 已实现且可用（大会员大积分）

| 任务 | 点数额度 | 实现方式 |
|---|---|---|
| 每日签到 | 每日基础分 | `POST /pgc/activity/score/task/sign2` |
| 浏览追番频道 10 秒 | +10 | `POST /pgc/activity/deliver/task/complete`，`position=jp_channel` |
| 浏览影视频道 10 秒 | +10 | 同上，`position=tv_channel` |
| 浏览会员购页面 10 秒 | +10 | `POST show.bilibili.com/api/activity/fire/common/event/dispatch` |
| 浏览装扮商城主页 | +10 | `POST /pgc/activity/score/task/complete/v2`，`taskCode=dress-view` |
| 观看剧集内容 10 分钟 | +40 | App 签名链路：`deliver/material/receive` 取 `task_id/token` → 等 600s 倒计时 → 签名调用 `deliver/task/complete` |

### 2. 已实现且可用（欧气盲盒 / 魔力战令）

| 任务 | 实现方式 |
|---|---|
| 盲盒/战令签到 | `POST /magic-c/sign/detail` → `POST /magic-c/sign/achieve {taskId}` |
| [每日] 欧气爆款种草 10 秒 | hercules 事件链：`GET /api/activity/hercules/task/report-detail` → 等 `eventTime` → `POST /api/activity/fire/common/event/dispatch` |
| 领取 [每日] 任务奖励 | `POST /mall-c/blind_box_activity/task/mls_pm/batch_receive_prize` |
| 周日领取全部战令等级奖励 | `POST /mall-magic-c/internet/mls_pm/war_order/prize_receive {"woActivityId": ...}` |
| 魔晶统计（总额 / 24h 过期 / 到期分布） | `GET /magic-c/magic_crystal/get_magic_crystal` + 明细推算 |

### 3. 做不到 / 不做的任务（重要）

| 任务 | 原因 |
|---|---|
| **[每日] 欧气许愿好物 1 次** | 写接口 `POST /magic-c-search/blind_box/wish/create` 对 PC 端恒定返回 **502**（同服务其它 POST 正常，属该路由本身故障）；前端还有 `if(!isBiliApp) return` 硬门槛。**目前只能手机 App 内手动许愿一次。** |
| 购买单点付费影片 / 购买会员购商品 / 购买装扮商品 / 线下门店消费 | 涉及真实支付，脚本**主动跳过**（按任务标题关键词识别） |
| 需要 App 专属风控票据的写接口 | 未实现，见「已知问题」 |

### 4. 附加能力

- **每日简报推送**：跑完把结果整理成 Markdown 简报，通过 [Server酱](https://sct.ftqq.com/) 推到微信（可关闭）。
- **每天只跑一次**：本地 `run_state.json` + 服务端任务状态双重判断；`--force` 可强制重跑。
- **无窗口运行**：计划任务用 `pythonw.exe` 直接启动，开机不会弹黑窗。

---

## 二、本地部署（Windows）

### 环境要求

- Windows 10 / 11
- Python 3.9+（需包含 `python.exe` 与同目录的 `pythonw.exe`）
- 无需管理员权限即可日常运行；**安装计划任务时需要管理员**

### 1. 安装依赖

```powershell
pip install requests
# 可选：仅当 config.json 里 watch_mode = "browser" 时需要
pip install playwright
```

### 2. 准备 Cookie

1. 浏览器登录 [bilibili.com](https://www.bilibili.com)
2. `F12` → Application（应用）→ Cookies → `https://www.bilibili.com`
3. 复制 `SESSDATA`、`bili_jct`、`DedeUserID`（`buvid3` / `buvid4` 建议一起填）

### 3. 生成配置

```powershell
cd <项目目录>
copy config.example.json config.json
notepad config.json
```

把上一步的三个值填进 `cookie` 段。**`config.json` 已在 `.gitignore` 里，切勿提交或外发。**

### 4. 先手动跑一次

```powershell
python bilibili_vip_point.py                 # 完整运行（约 12 分钟，含观看剧集 11 分钟等待）
python bilibili_vip_point.py --skip-watch    # 跳过观看剧集，约 1 分钟
python bilibili_vip_point.py --skip-oqi      # 跳过欧气盲盒模块
python bilibili_vip_point.py --force         # 忽略「今日已跑」记录
python oqi_blindbox.py                       # 只跑欧气盲盒（约 20 秒）
python notify.py                             # 单独测一次 Server酱 推送
```

### 5. 安装计划任务（登录后自动跑、每天一次）

以**管理员**身份打开 PowerShell：

```powershell
cd <项目目录>
powershell -ExecutionPolicy Bypass -File .\install_scheduled_task.ps1
# 可选：再额外加一个每天固定时间的兜底触发
powershell -ExecutionPolicy Bypass -File .\install_scheduled_task.ps1 -DailyAt "12:30"
```

安装脚本会：

1. 自动定位 Python，并优先使用 **`pythonw.exe`**（GUI 子系统，**运行时无任何窗口**）；
2. 生成/校验依赖，做一次 `py_compile` 语法自检（不会真的跑任务）；
3. 注册计划任务 `BilibiliVipPointAutoRun`：登录后延迟 1 分钟触发；
4. 用「`run_state.json` + 服务端任务状态」保证每天只跑一次。

> 若系统禁止运行脚本，必须带 `-ExecutionPolicy Bypass`（脚本本身是 UTF-8 with BOM，改它请保持该编码）。

卸载：

```powershell
powershell -ExecutionPolicy Bypass -File .\uninstall_scheduled_task.ps1
```

### 6. 日志

| 文件 | 内容 |
|---|---|
| `bilibili_vip_point.log` | 应用日志（DEBUG 级，排查问题主要看它） |
| `bilibili_vip_point.console.log` | INFO 级镜像；`pythonw` 运行时也会生成 |
| `run_state.json` | 当日运行状态（是否完成、尝试次数、魔晶快照） |

---

## 三、配置说明（`config.json`）

| 参数 | 默认 | 说明 |
|---|---|---|
| `watch_mode` | `app_api` | 观看剧集方式：`app_api`（纯 HTTP，推荐）/ `browser`（真实播放，备用）/ `off` |
| `watch_duration_seconds` | 660 | 观看等待秒数；服务端要求满 600s，提前上报会被拒 |
| `bangumi_ep_id` / `bangumi_season_id` | 286838 / 28735 | `app_api` 模式使用的剧集（任意 OGV 剧集均可） |
| `bangumi_url` | 怪物岛 ep286838 | `browser` 模式播放页，建议选时长 > 15 分钟的免费剧集 |
| `browser_channel` | `msedge` | `msedge` / `chrome` |
| `browser_headless` | `false` | **不要改成 true**：headless 下播放器不会发心跳，等于没看 |
| `browse_wait_seconds` | 10 | 模拟浏览频道页的停留时间 |
| `try_channel_tasks` | true | 任务列表没展示追番/影视任务时也尝试上报（部分账号列表不展示但接口仍计分） |
| `max_attempts_per_day` | 2 | 每天最多尝试次数 |
| `app_device_id` / `app_session_id` | 固定值 | 领观看凭证用的 App 设备参数，保持默认即可 |
| `oqi_blindbox.enabled` | true | 是否启用欧气盲盒模块 |
| `oqi_blindbox.claim_grade_rewards` | `sunday` | 战令等级奖励领取时机：`sunday` / `always` / `off` |
| `oqi_blindbox.request_interval` | [1.5, 3.5] | 请求随机间隔（秒），降低风控风险 |
| `notify.enabled` | false | 是否推送每日简报到微信 |
| `notify.send_key` | 空 | Server酱 SendKey（`SCT...` 或 `sctp...` 自动识别） |

---

## 四、工作原理（关键接口）

> 以下接口均为 B 站 H5/App 使用的非公开接口，仅作学习记录。接口会变，代码里已做失败降级。

### 大会员大积分

```
GET  /x/vip_point/task/combine                      任务列表
POST /pgc/activity/score/task/sign2                 签到（mobi_app/platform/csrf 放 query）
POST /pgc/activity/score/task/receive/v2           领取任务（form: taskCode=xxx ← 驼峰！）
POST /pgc/activity/score/task/complete/v2          完成任务（form: taskCode=xxx）
POST /pgc/activity/deliver/task/complete           频道浏览（position=jp_channel/tv_channel）
POST https://show.bilibili.com/api/activity/fire/common/event/dispatch   会员购浏览事件
POST /pgc/activity/deliver/material/receive        观看剧集：领观看凭证（返回 task_id/token，倒计时 600s）
POST /pgc/activity/deliver/task/complete           观看剧集：签名上报（task_id/token/task_sign/timestamp）
```

观看剧集上报的签名（App appkey 体系）：

```
appkey = 1d8b6e7d45233436
appsec = 560c52ccd288fed045859ed18bffd973
task_sign = md5(按 key 排序的 query(除 sign/task_sign 外全部参数) + appsec)
sign      = md5(按 key 排序的 query(含 task_sign) + appsec)
```

### 欧气盲盒 / 魔力战令

```
POST /magic-c/sign/detail                           签到状态
POST /magic-c/sign/achieve                          签到（{"taskId": ...}）
GET  https://show.bilibili.com/api/activity/hercules/task/report-detail?taskId=<herculesId>
POST https://show.bilibili.com/api/activity/fire/common/event/dispatch   {"eventId": ...}
POST /mall-magic-c/internet/mls_pm/war_order/activity_task_list         [每日] 任务列表
POST /mall-c/blind_box_activity/task/mls_pm/batch_receive_prize         领任务奖励
POST /mall-magic-c/internet/mls_pm/war_order/prize_receive              领等级奖励（woActivityId）
GET  /magic-c/magic_crystal/get_magic_crystal                           魔晶余额
GET  /magic-c/magic_crystal/list_page_magic_crystal                     魔晶明细
```

---

## 五、已知问题与踩坑记录

1. **参数名是驼峰**：`receive/v2`、`complete/v2` 要 `taskCode`，写成 `task_code` 一律 `-400`。
2. **观看剧集必须等满 10 分钟**再上报，提前调用也是 `-400`（不是签名错）。
3. **前缀有三个，别混**：签到/魔晶在 `/magic-c`，战令在 `/mall-magic-c`，会员购部分接口在 `/mall-c`。
   写成 `/mall-c/sign/detail` 会被网关拦成 **502**。
4. **战令接口 POST 要用 JSON body**（form 会 `service error`）；`prize_receive` 参数是 `woActivityId`。
5. **魔晶明细里 `remainAmount` 恒等于该批次授予量**（不会随消耗递减），直接求和会严重偏大；
   总额要用汇总接口的 `remainAmount`、可用用 `effectAmount`、24h 过期用 `expiringAmount`。
6. **`wish/create` 恒 502**：同一服务其它接口正常，属该路由故障；带 `x-bili-ticket` 也无效。
7. **计划任务不要用 `cmd.exe`**：cmd 是控制台子系统程序，交互式登录时会弹出黑窗并持续整个运行；
   用 `pythonw.exe`（GUI 子系统）才无窗口。

---

## 六、风险提示

1. **Cookie 等于账号**：`SESSDATA`/`bili_jct` 泄露即可被用来操作你的账号。`config.json` 不要提交、不要截图外发。
2. **非官方接口**：B 站可能随时调整接口、增加风控；脚本失败属正常现象，请先看日志再重试。
3. **频率**：脚本已内置随机间隔与每日一次限制，请勿改成高频轮询或多账号批量刷取。
4. **不要用于商业用途或代刷**。使用本脚本带来的封号、风控、积分未到账等风险由使用者自行承担。

---

## 七、常见问题

**Q: 观看剧集一直不成功？**
A: 确认 `watch_mode=app_api` 且当天任务已领取；`material/receive` 返回空说明任务状态不对（脚本会自动先领取）。
若想换方式，可临时用 `watch_mode=browser`（须 `browser_headless=false`）。

**Q: 开机后弹出 cmd 黑窗？**
A: 旧版计划任务用 `cmd.exe` 作为动作。重新运行一次 `install_scheduled_task.ps1` 即可改成 `pythonw.exe`。

**Q: 提示"禁止运行脚本"？**
A: 用 `powershell -ExecutionPolicy Bypass -File .\install_scheduled_task.ps1`。

**Q: 修改 `.ps1` 后中文乱码/报"字符串缺少终止符"？**
A: 两个 `.ps1` 必须是 **UTF-8 with BOM**（Windows PowerShell 按 GBK 读取无 BOM 文件）。

**Q: 简报没收到？**
A: `notify.enabled` 是否为 true、`send_key` 是否有效；可先 `python notify.py` 单独测试。

**Q: 许愿任务为什么不做？**
A: 见上文「做不到 / 不做的任务」，需要手机 App 手动点一次。

---

## 八、目录结构

```
.
├── bilibili_vip_point.py          # 主脚本：大积分任务 + 调度 + 简报入口
├── oqi_blindbox.py                # 欧气盲盒 / 魔力战令模块
├── notify.py                      # Server酱 推送与简报排版
├── install_scheduled_task.ps1     # 安装计划任务（管理员 + Bypass）
├── uninstall_scheduled_task.ps1   # 卸载计划任务
├── config.example.json            # 配置模板（复制为 config.json 后填写）
├── requirements.txt
├── README.md
├── LICENSE
└── .gitignore
```

运行时生成（已被 `.gitignore` 忽略）：`config.json`、`run_state.json`、`*.log`、`run_daily.cmd`、`run_hidden.vbs`。

---

## 九、参考与致谢

- [BiliBiliToolPro](https://github.com/RayWangQvQ/BiliBiliToolPro) — 大积分任务接口与 App 签名链路的参考
- [bilibili-API-collect](https://github.com/SocialSisterYi/bilibili-API-collect) — 公开接口整理
- [Server酱](https://sct.ftqq.com/) — 简报推送

---

## 免责声明

本项目仅用于**个人学习与自动化实践**，不提供任何形式的担保。
使用者应自行确保其行为符合 B 站用户协议与所在地法律法规；请勿用于商业用途、代刷或任何破坏平台规则的行为。
项目作者不对因使用本项目产生的任何直接或间接损失负责。

## 许可证

[MIT](LICENSE)
