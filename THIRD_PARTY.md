# 第三方组件边界

当前机鉴源代码没有复制 SigNoz 或 HolmesGPT 的源码。它通过公开 HTTP API、Webhook 和 OpenTelemetry 协议与独立部署的服务组合。

计划运行组件：

- SigNoz Community：核心代码（`ee/` 与 `cmd/enterprise/` 之外）使用 MIT Expat；企业目录有单独许可。部署或分发前需要保留原项目许可和声明。
- HolmesGPT：Apache License 2.0。部署或分发前需要保留许可、版权、修改声明和适用的 NOTICE。
- OpenTelemetry Collector Contrib：Apache License 2.0。示例配置只描述如何连接，不包含其二进制。

OpenObserve、HyperDX、Keep、NetBox 和 LibreNMS 当前只作为研究或可选适配对象，不是本仓库运行依赖。未来如果复制、修改或分发其代码，需要在进入产品前单独审核许可证；本文件不是法律意见。
