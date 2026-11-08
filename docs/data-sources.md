# 中国广电数据源调查

## 官方渠道

- 福建广电客服明确列出中国广电官网、微信/支付宝「中国广电营业厅」小程序和 10099 自助渠道，可查话费余额和套餐余量。[福州广电客服问答](https://www.fjgdwl.com/fuzhou/kfzx/khbz/index_8.shtml)
- 匿名访问全国官网时只观察到客户端挑战响应。可公开访问的移动营业厅产品页展示了套餐规则，例如某套餐通用流量在满足条件时可结转，变更或停机可能影响结转。产品详情与登录后个人用量属于不同数据。[全国官网](https://www.10099.com.cn/) · [移动营业厅套餐页](https://m.10099.com.cn/h5wap/busiClient/account/d100/)
- 未找到运营商向第三方开放的认证或查询契约。参考项目目前也未列出已实现的广电数据源。[ha_china_comm](https://github.com/sfairy/ha_china_comm) · [carrier-usage-skill](https://github.com/weieast1314/carrier-usage-skill)
- 10099 自助服务的公开说明不能证明特定短信指令有效。第三方文章所列指令不一致，本项目不采用这些指令。能否取得查询短信、发送号码和格式均需按号码归属地验证。

## 授权账号的网页实测

在官方网页正常输入图形验证码和短信验证码后，使用同一会话进入「个人中心 → 我的账户」。网页显示话费余额、套餐余量，并区分通用和定向流量。开发期间仅观察网页正常发出的请求，记录路由和字段结构。

| 网页步骤 | 观察到的请求或字段 |
| --- | --- |
| 取得图形验证码 | `GET /contact-web/api/login/getImageCheckCode` |
| 发送验证码、登录 | `POST /contact-web/api/login/getVerifyCode` 和 `POST /contact-web/api/login/gwLogin` |
| 读取余额 | `POST /contact-web/api/busi/qryBalanceFee`，响应中的 `intfResultBean.balance` |
| 读取套餐资源 | `POST /contact-web/api/busi/qryUserRes`，响应中的 `intfResultBean.userResList`。条目有 `itemTypeCode`、`addupValue`、`balance`、`startTime`、`endTime` 等字段 |

这是官方网页的内部通信。登录和查询请求都有网页生成的 `data` 封装。原浏览器会话可继续查询，另开的浏览器上下文仅恢复 Cookie 后未能恢复登录。尚需核实可支持的第三方认证方法、会话寿命及失效信号，以及不同套餐的单位、包分类和用量字段含义。成功读取一张卡不足以确定这些规则。当前集成使用官方网页实查值录入，继续保留短信推送作为实验性入口。[中国广电网上营业厅](https://www.10099.com.cn/)

## 短信转发

SmsForwarder 公开记录了 POST 表单的 `from`、`content` 和毫秒 `timestamp`。转发方时钟用于拒绝陈旧请求，时间戳不能证明短信在运营商侧的生成时间。HA 使用局域网限制和随机 Webhook 路径认证转发请求。公开资料没有验证本项目对任何地区的广电回执适用。[SmsForwarder Webhook 协议](https://github.com/pppscn/SmsForwarder/wiki/附录1：向webhook发送post-get-put-patch请求)

请自行在官方渠道核对每个字段的口径。无法判断的条目可保留在本人手机或营业厅页面，集成不记录未识别的短信正文。若真实回执存在多处动态内容，完整模板会拒绝不匹配的消息。用户可通过手动录入继续记录实查值。

## 实际验收

- 自行在手机上登录官方渠道。记录省份、查询入口、字段标签、单位、流量包种类及有效期，并在 HA 对照账户读数。反馈只需脱敏字段结构和对比结论。
- 如当地确实能接收查询回执，记录经过本人验证的获取方式、发送人短码、短信固定文本和字段边界。删除手机号、账号、姓名、验证码及任何唯一标识，可替换金额和流量值。两次相隔的回执有助于验证动态字段和模板兼容性。
- 若要评估营业厅自动查询，需要官方 API 文档，或本人授权范围内、符合渠道使用条件的脱敏只读请求结构及响应字段。还需确认认证方式、会话过期、重登录和频控行为。不要上传 HAR、会话凭据或原始通信记录。
