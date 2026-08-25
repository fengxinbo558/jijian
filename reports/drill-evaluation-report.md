# 交互式故障演练评测报告

生成时间：2026-08-25T15:01:33.632110+00:00

## 结论

- 场景通过：25/25
- 检查点通过：300/300
- 五类盲测隔离：5/5
- 分析模式：rules_only=25
- 本报告只证明模拟闭环、分支和安全边界按预期运行，不代表真实生产定位准确率。

## 逐场景结果

| 场景 | 分类 | 结果 | 平台信号 | 人工节点 | 实际路径 |
|---|---|---:|---:|---:|---|
| `net-optical-module` | network | 通过 | 3 | 3 | network-check-config → network-replace-module → checkpoint-verify |
| `net-fiber-attenuation` | network | 通过 | 2 | 5 | network-check-config → network-replace-module → network-measure-optics → network-replace-cable → checkpoint-verify |
| `net-port-hardware` | network | 通过 | 2 | 5 | network-check-config → network-replace-module → network-measure-optics → network-migrate-port → checkpoint-verify |
| `net-port-config` | network | 通过 | 2 | 2 | network-check-config → checkpoint-verify |
| `net-switch-uplink` | network | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `linux-oom` | linux | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `linux-disk-readonly` | linux | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `linux-service-loop` | linux | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `linux-nic-bond` | linux | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `linux-soft-lockup` | linux | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `hw-disk-smart` | hardware | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `hw-memory-ecc` | hardware | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `hw-power-supply` | hardware | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `hw-fan` | hardware | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `hw-mainboard` | hardware | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `fac-core-single-feed` | facility | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `fac-dual-feed` | facility | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `fac-cooling-high-temp` | facility | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `fac-water-core` | facility | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `fac-smoke` | facility | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `app-port-conflict` | application | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `app-dependency-timeout` | application | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `app-dns` | application | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `app-tls-expired` | application | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |
| `app-db-deadlock` | application | 通过 | 2 | 2 | checkpoint-action → checkpoint-verify |

## 失败明细

无。

## 盲测隔离

- network：通过
- linux：通过
- hardware：通过
- facility：通过
- application：通过
