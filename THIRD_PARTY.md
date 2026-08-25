# 第三方组件边界

当前机鉴源代码没有复制 SigNoz 或 HolmesGPT 的源码。它通过公开 HTTP API、Webhook 和 OpenTelemetry 协议与独立部署的服务组合。

计划运行组件：

- SigNoz Community：核心代码（`ee/` 与 `cmd/enterprise/` 之外）使用 MIT Expat；企业目录有单独许可。部署或分发前需要保留原项目许可和声明。
- HolmesGPT：Apache License 2.0。部署或分发前需要保留许可、版权、修改声明和适用的 NOTICE。
- OpenTelemetry Collector Contrib：Apache License 2.0。示例配置只描述如何连接，不包含其二进制。

OpenObserve、HyperDX、Keep、NetBox 和 LibreNMS 当前只作为研究或可选适配对象，不是本仓库运行依赖。未来如果复制、修改或分发其代码，需要在进入产品前单独审核许可证；本文件不是法律意见。

## 公开测试数据与测试环境

仓库只保存目录、导入器和测试报告。实际下载文件位于被忽略的 `data/public-datasets/cache/`，不会随产品源码提交。公开数据用于验证解析、治理和接口合同，不能替代客户现场验收。

- LogHub：许可限定研究或学术使用，并要求引用 LogHub 仓库与论文、保留许可。产品只允许管理员临时下载到本地测试缓存；不得把数据打包成商用训练集。
- GAIA-DataSet：上游 README 表述为 Apache-2.0，但当前仓库 `LICENSE` 文件内容为 GPL-2.0，标识存在冲突。当前只提供手工导入占位，不自动下载；商用前必须单独确认。
- OpenTelemetry Astronomy Shop：Apache-2.0。作为本地近真实微服务遥测生成器，不将演示遥测标记为生产数据。
- Microsoft AIOpsLab：MIT。需要独立部署、故障注入和遥测环境；当前只提供项目入口，不声称已经运行。
- Backblaze Drive Stats：使用时注明 Backblaze 来源；可销售衍生作品但不得销售原始数据。默认只下载字段 Schema，季度原始数据不进入仓库。
- DMTF Redfish Mockup Server：BSD-3-Clause。保留版权、许可条件与免责声明，不以 DMTF 或贡献者名义为产品背书。Mockup 只验证协议字段，不代表真实硬件故障。

上述摘要用于工程边界提示，不构成法律意见；正式商业发布仍需按当时的上游许可证版本复核。
