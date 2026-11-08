# 网上营业厅浏览器助手（实验性）

助手运行在一台能访问官方营业厅和 HA 局域网地址、有图形桌面的可信电脑上。Home Assistant 仍通过 HACS 安装集成或手动复制组件目录。助手单独从项目源码运行，HA 环境无需 Node.js、Playwright 或 Chromium。

1. 在 HA 添加「中国广电 → 通过浏览器查询网上营业厅」。从「重新配置」取得私密 Webhook 路径，把它接到 HA 的局域网地址后。优先使用可信 HTTPS。
2. 在助手目录，用 Node.js 22 或以上安装浏览器依赖。

   ```bash
   npm install --no-save --package-lock=false playwright@1.61.0
   npx playwright install chromium
   ```

3. 在同一个终端设置变量后运行。通用流量包名是可选项。请先在营业厅确认它是本月主套餐通用流量，并填写网页显示的完整名称。留空时仅发布余额。

   ```bash
   read -rsp 'HA Webhook URL: ' BROADNET_WEBHOOK_URL; echo
   export BROADNET_WEBHOOK_URL
   export BROADNET_GENERAL_PACKAGE='替换为已核实的完整包名'
   umask 077
   node run.cjs
   ```

4. 助手打开专用浏览器配置。确认官方页面已有登录状态，在终端按 Enter 开始。首次使用时，用户可以在官方网页自行登录。助手使用浏览器持有的会话，每 30 分钟打开账户页并读取余额和套餐资源。查询或推送失败时停止，登录流程由用户在官方网页完成。
5. 首次收到读数后，与同一网页对照。网页流量标记 `G` 为 GiB 口径，HA 使用十进制 GB。助手使用原始 KiB 换算并用原始值求差，网页会先舍入，少量显示差异属正常。只有包名精确匹配、唯一、类型为流量且处于当前自然月周期才发布流量。遇到包名或字段变更时流量显示不可用。

默认浏览器配置位于运行目录的 `.broadnet-browser/`，也可通过 `BROADNET_BROWSER_PROFILE` 指定专用目录。该目录含敏感会话数据，请保持私有，避免放在同步盘、Git 或备份共享目录。Webhook 只接收归一化数字和观测时间，不能用于读取 HA 账户数据。泄露后在集成「重新配置」换发路径。
