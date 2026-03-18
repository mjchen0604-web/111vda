# ChatCore 低流量主动探活

如果你希望只在低流量时段做主动探活/预清洗，不建议在服务内常驻轮询，直接用 cron 触发更稳。

## 默认脚本

- 探活脚本：`scripts/chatcore-probe-auths.sh`
- 安装 cron：`scripts/install-chatcore-probe-cron.sh`

## 安装示例

```bash
cd /opt/111vda
chmod +x scripts/chatcore-probe-auths.sh scripts/install-chatcore-probe-cron.sh
sudo CHATCORE_PROBE_SCHEDULE="*/20 2-6 * * *" \
  CHATCORE_PROBE_SWEEP_AFTER_PROBE=0 \
  ./scripts/install-chatcore-probe-cron.sh
```

默认行为：

- 每 20 分钟在 `02:00-06:59` 触发一次
- 调用内嵌 chat 的 `POST /api/actions/probe_auths`
- 只隔离当前失效凭证
- 不扩大到同账号其他凭证

## 手动执行

```bash
cd /opt/111vda
./scripts/chatcore-probe-auths.sh
```

## 可调参数

- `CHATCORE_PROBE_SCHEDULE`
  - cron 表达式
- `CHATCORE_INTERNAL_CHAT_HOST`
  - 默认 `127.0.0.1`
- `CHATCORE_INTERNAL_CHAT_PORT`
  - 默认 `1455`
- `CHATCORE_PROBE_LOG_FILE`
  - 默认 `/var/log/111vda-chatcore-probe.log`
- `CHATCORE_PROBE_SWEEP_AFTER_PROBE`
  - `1` 时探活后顺手调用一次 `sweep_invalid_auths`
