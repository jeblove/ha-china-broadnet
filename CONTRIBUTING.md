# 参与贡献

本项目为独立实现，使用现有 MIT 许可。可参考 Home Assistant、HACS 和其他运营商的产品行为及公开协议。贡献中的代码、文字与测试样例应自行编写，或说明来源和适用许可。

1. 使用 Python 3.14 虚拟环境安装 `requirements-dev.txt`。
2. 提交前执行 `ruff check .`、`ruff format --check .`、`pytest -q`。
3. 配置流程或实体文案变化时同步 `strings.json`、英文和简体中文翻译。
4. 在测试 HA 实例复制组件目录，验证 UI 添加、更新、重载和删除。短信测试使用本人有权访问的局域网实例。
5. 提交问题时附 HA 版本、集成版本、数据来源、预期行为与脱敏复现步骤。测试用例使用合成数据，反馈真实回执时遵循[验收指南](docs/data-sources.md)。

配置流程在 `config_flow.py`，SmsForwarder 协议边界在 `receiver.py`，短信模板和数据换算分别在 `sms.py`、`usage.py`。两个入口共用 `coordinator.py` 和 `sensor.py`。
