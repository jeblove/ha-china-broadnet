# 中国广电 · China Broadnet

在 Home Assistant 查看中国广电手机账户的**话费余额、流量余量、使用率和账单**，并用这些读数设置提醒。通过手机号和短信验证码登录后，集成默认每 **8 分钟**查询一次，定期查询也能减少因长时间未访问而需要重新登录的情况。

China Broadnet is a Home Assistant integration for China Broadnet mobile subscribers. Sign in with your mobile number and an SMS verification code to track your account balance, mobile data usage, and bills in Home Assistant.

同一个 HA 实例可以添加多个独立广电号码。每张卡用不同的别名分别登录。

## 安装与登录

需要 **Home Assistant Core 2026.10.0 或更新版本**。

1. 在「设置 → Marketplace」（Home Assistant 2026.11 起）或 HACS（旧版）搜索 **China Broadnet**，打开后选择「安装」或「下载」。也可以用下方按钮直达安装页。安装完成后重启 HA。

   [![在 Home Assistant 中打开中国广电](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jeblove&repository=ha-china-broadnet&category=integration)

   如果搜不到，在 Marketplace 选择「从链接添加」，或在 HACS 打开「自定义仓库」，填入 `https://github.com/jeblove/ha-china-broadnet`。如果提示选择类别，选「集成」，然后打开仓库安装。

2. 在 HA 打开「设置 → 设备与服务 → 添加集成」，搜索「中国广电」或「China Broadnet」。
3. 选择「手机号和短信验证码登录」，填入账号别名和手机号，阅读协议并提交。填入收到的验证码，查到余额后就会创建实体。验证码有效期为 5 分钟，发送 60 秒后可以手动重发。

会话过期时，按 HA 的重新认证提示为原号码重新获取验证码。定时查询只读取账户数据，验证码仅在你提交登录或重发时发送。

## 日常使用

- **余额和账单**。查看话费余额、当月消费、上月已出账金额，以及套餐外流量、语音和短信费用。旧会话的上月账单可能要等下次重新认证获取地区信息。
- **通用流量**。查看总量、已用、剩余和使用率。默认汇总可确认的本月通用包，可在集成的「重新配置」里调整勾选范围。
- **定向、赠送和结转包**。分别展示余量和到期时间。定向汇总使用率要求本月纳入的包有效期一致、总额度可确认。
- **用量进度**。「本月流量用量超前幅度」是已用占比减去账期进度。例如已用 70%、本月已过 40%，表示超前 30 个百分点。
- **按需启用的实体**。通用和定向流量剩余率、单个定向包使用率、预计通用流量用尽时间默认关闭。可在 HA 实体列表显示已禁用实体，然后逐个启用。
- **自动查询间隔**。在集成的「重新配置」中可设为 5 至 60 分钟。间隔越长，营业厅会话越可能因长时间无活动而过期。
- **手动刷新**。充值后，可在设备的「诊断」区按「刷新营业厅读数」。查询开始后 60 秒内会忽略重复请求，会话失效时可先重新认证。

## 设置提醒

想在通用流量使用率超过 80% 时收到通知，可创建「数值状态」触发器，选择「本月通用流量使用率」，把阈值设为「高于 80」。

也可以在「设置 → 自动化与场景 → 蓝图 → 导入蓝图」导入提醒蓝图。每条自动化选择一个传感器、一个通知动作和一个独立的「输入布尔值」助手。蓝图支持低余额、低流量、用量超前和到期提醒，可设置静默时段。静默结束后，仍存在的异常会触发一次提醒。 [中国广电用量提醒蓝图](https://github.com/jeblove/ha-china-broadnet/blob/main/blueprints/automation/china_broadnet/usage_alert.yaml)

需要自定义事件触发器时，可监听 `china_broadnet_usage_spike`（一小时内增加至少 5 GB）和 `china_broadnet_packages_changed`（流量包新增、移除、续期或有效期变化）。

## 实体不可用时

先打开设备的「诊断」区，查看**查询状态**以及余额、套餐余量各自的**最后成功查询时间**。需要反馈问题时，可从集成配置项菜单「下载诊断」。诊断包含集成版本、自动查询间隔、余额与套餐资源及整体查询的最后成功时间，以及网页验证退避的剩余秒数。

- **余额正常，部分流量实体不可用**。打开「重新配置」检查所选通用包是否仍有效。查询成功但所选包无法匹配时，HA 的「设置 → 系统 → 修复」会提示重新选择，恢复匹配后提示自动清除。定向包的额度缺失、为零或有效期不一致时，相应的使用率可能不可用。
- **预计流量可用天数或用尽时间不可用**。需要 Recorder 最近 7 天内至少一天可比较的通用流量历史，并且用量有增长。跨月、换包或历史不足时会暂停估算。
- **月末用量快照不可用**。首次积累读数后，需等到北京时间下一个月初完成归档。快照保留最近 12 个有读数的账期，各指标带有最后成功观察时间。
- **流量数字和广电 App 略有不同**。HA 统一显示十进制 GB。广电按二进制口径显示的 100 G，对应约 107.374 GB。使用率不受这种单位换算影响。
- **遇到网页验证页**。桌面备用登录和会话导入可能受到网络出口影响。平时优先用「手机号和短信验证码登录」。

## 进阶配置与隐私

支持手动录入营业厅读数、局域网 SmsForwarder 转发，以及实验性桌面图形验证码登录和会话导入。主副卡共享池及分摊留待后续。 [进阶配置](https://github.com/jeblove/ha-china-broadnet/blob/main/docs/advanced-setup.md)

HA 配置项保存手机号和会话凭据，配置与历史读数可能进入 HA 备份。反馈问题时使用集成提供的脱敏诊断，分享前检查内容。Webhook 地址和登录验证码请妥善保管。

[版本记录](https://github.com/jeblove/ha-china-broadnet/blob/main/CHANGELOG.md) · [数据源与验证范围](https://github.com/jeblove/ha-china-broadnet/blob/main/docs/data-sources.md) · [开发与测试](https://github.com/jeblove/ha-china-broadnet/blob/main/CONTRIBUTING.md)
