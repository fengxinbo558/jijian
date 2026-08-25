# 公开数据轻量样本实测报告

日期：2026-08-25  
运行方式：官方地址实时下载、本地忽略缓存、临时 SQLite 数据库  
结果：3/3 导入完成，0 条导入错误

| 数据源 | 读取 | 去重后告警 | 结果 |
|---|---:|---:|---|
| LogHub Linux 2k | 2,000 行 | 1 | 匹配前 50 条异常观测，其中 49 条被去重，形成 1 个认证失败事故入口 |
| Backblaze Drive Stats Schema | 197 行 | 0 | 识别为字段 Schema，不伪造磁盘故障 |
| DMTF Redfish Mockup | 1 个系统对象 | 0 | Health=OK、State=Enabled；写入资产字段，不伪造硬件故障 |

## 文件校验

- LogHub Linux 2k：`b3e20bc1afe732ab1bf3ed1de4bf9c809e4194e02f7dea911d918e5342e8e173`
- Backblaze Schema：`365cf50ad5ebfc3e20d0959337d9877ce9539e0432955bda836b7482cd0f5358`
- DMTF Redfish Mockup：`af03202a1bcd4f16ee8dab92364fc7b1f78cb3088f74d3b79294e9c22ee2957b`

## 结论与限制

- 已验证官方样本的下载、哈希、解析、设备身份、去重、告警入口和导入报告。
- LogHub 的 50 条重复认证失败不会制造 50 张工单；页面分别展示原始观测数、去重后告警数和重复数。
- 健康的 Redfish Mockup 和 Backblaze Schema 不会被包装成“发现故障”。
- 公开样本只能验证机制，不代表客户现场定位准确率，也不用于估算十万台生产吞吐。
- LogHub 仅按其许可用于研究测试；GAIA 因上游许可标识冲突未自动下载；OpenTelemetry Demo 和 AIOpsLab 需要单独运行环境。
