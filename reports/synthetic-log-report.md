# 网络、系统与机房合成日志自测报告

- 场景通过：36/36
- 检查点通过：259/259
- 测试模式：合成日志＋规则＋知识库基线
- 限制：结果不能证明真实生产环境准确率

## 分领域结果

| 领域 | 通过 | 总数 |
|---|---:|---:|
| application | 5 | 5 |
| facility_fire | 1 | 1 |
| facility_power | 4 | 4 |
| facility_temperature | 1 | 1 |
| facility_water | 2 | 2 |
| linux_hardware | 1 | 1 |
| linux_kernel | 1 | 1 |
| linux_ras | 2 | 2 |
| linux_service | 1 | 1 |
| linux_storage | 3 | 3 |
| linux_system | 1 | 1 |
| linux_time | 1 | 1 |
| network_common_incident | 1 | 1 |
| network_errors | 1 | 1 |
| network_hardware | 1 | 1 |
| network_interface | 2 | 2 |
| network_layer2 | 2 | 2 |
| network_optics | 1 | 1 |
| network_redundancy | 3 | 3 |
| network_routing | 2 | 2 |

## 场景结果

| 编号 | 场景 | 来源 | 结果 | 失败检查点 |
|---|---|---|---|---|
| LNX-001 | OOM终止Java进程 | journald/kernel | 通过 | - |
| LNX-002 | 磁盘I/O错误导致文件系统只读 | journald/kernel | 通过 | - |
| LNX-003 | inode耗尽而非磁盘介质故障 | application_file | 通过 | - |
| LNX-004 | NVMe介质与健康告警 | smart_nvme_file | 通过 | - |
| LNX-005 | ECC可纠正错误不直接确认坏条 | journald/edac | 通过 | - |
| LNX-006 | ECC不可纠正错误伴随MCE | journald/edac | 通过 | - |
| LNX-007 | PCIe AER与网卡重置 | journald/kernel | 通过 | - |
| LNX-008 | 内核软锁死与watchdog | journald/kernel | 通过 | - |
| LNX-009 | systemd服务重启循环 | journald/systemd | 通过 | - |
| LNX-010 | 时钟同步失败影响日志排序 | journald/chrony | 通过 | - |
| APP-001 | 监听端口冲突 | application_file | 通过 | - |
| APP-002 | 依赖服务连接被拒绝 | application_file | 通过 | - |
| APP-003 | DNS解析失败 | application_file | 通过 | - |
| APP-004 | TLS证书已过期 | reverse_proxy_file | 通过 | - |
| APP-005 | 数据库死锁 | database_file | 通过 | - |
| NET-001 | 交换机端口运行状态下降 | network_syslog | 通过 | - |
| NET-002 | 端口短时间反复flap | network_syslog | 通过 | - |
| NET-003 | 接收光功率低 | network_syslog | 通过 | - |
| NET-004 | 接口CRC错误持续增长 | network_syslog | 通过 | - |
| NET-005 | LACP成员退出但聚合仍工作 | network_syslog | 通过 | - |
| NET-006 | Linux Bond主备切换 | journald/kernel | 通过 | - |
| NET-007 | BGP邻居会话下降 | network_syslog | 通过 | - |
| NET-008 | OSPF邻接关系下降 | network_syslog | 通过 | - |
| NET-009 | BPDU Guard阻断接入口 | network_syslog | 通过 | - |
| NET-010 | MAC地址在两个端口间漂移 | network_syslog | 通过 | - |
| NET-011 | MLAG对等链路丢失 | network_syslog | 通过 | - |
| NET-012 | 交换机单电源模块故障 | network_syslog | 通过 | - |
| NET-013 | 核心交换机宕机导致多个下联不可达 | nms_webhook_and_syslog | 通过 | - |
| FAC-001 | 核心机房单路掉电 | dcim_webhook | 通过 | - |
| FAC-002 | 核心机房双路掉电 | dcim_webhook | 通过 | - |
| FAC-003 | 普通机房单路掉电但设备在线 | dcim_webhook | 通过 | - |
| FAC-004 | 普通机房漏水但未影响设备 | leak_sensor_and_onsite | 通过 | - |
| FAC-005 | 漏水导致核心交换机故障 | dcim_nms_and_onsite | 通过 | - |
| FAC-006 | 温度升高但没有达到CC条件 | dcim_webhook | 通过 | - |
| FAC-007 | 机房等级未知时双路掉电等待确认 | dcim_webhook | 通过 | - |
| FAC-008 | 烟雾告警等待SOP确认 | environment_sensor | 通过 | - |

## 逐条检查

### LNX-001 OOM终止Java进程

- 通过｜故障分类：实际 `system`，预期 `system`
- 通过｜需要现场介入：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['oom_kill']`，预期 `['oom_kill']`
- 通过｜命中规则：实际 `['system_memory_pressure']`，预期 `['system_memory_pressure']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### LNX-002 磁盘I/O错误导致文件系统只读

- 通过｜故障分类：实际 `hardware`，预期 `hardware`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['block_device', 'block_io_error', 'filesystem_read_only']`，预期 `['block_io_error', 'filesystem_read_only']`
- 通过｜命中规则：实际 `['disk_io']`，预期 `['disk_io']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### LNX-003 inode耗尽而非磁盘介质故障

- 通过｜故障分类：实际 `hardware`，预期 `hardware`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['inode_exhausted', 'space_exhausted']`，预期 `['space_exhausted', 'inode_exhausted']`
- 通过｜命中规则：实际 `['disk_io']`，预期 `['disk_io']`
- 通过｜建议内容：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### LNX-004 NVMe介质与健康告警

- 通过｜故障分类：实际 `hardware`，预期 `hardware`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['block_device', 'nvme_health', 'smart_failure']`，预期 `['nvme_health']`
- 通过｜命中规则：实际 `['disk_io']`，预期 `['disk_io']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### LNX-005 ECC可纠正错误不直接确认坏条

- 通过｜故障分类：实际 `hardware`，预期 `hardware`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['memory_ce', 'memory_locator']`，预期 `['memory_ce', 'memory_locator']`
- 通过｜命中规则：实际 `['memory_machine_check']`，预期 `['memory_machine_check']`
- 通过｜不得越级确认：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### LNX-006 ECC不可纠正错误伴随MCE

- 通过｜故障分类：实际 `hardware`，预期 `hardware`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['machine_check', 'memory_ce', 'memory_locator', 'memory_ue']`，预期 `['memory_ue', 'machine_check', 'memory_locator']`
- 通过｜命中规则：实际 `['memory_machine_check']`，预期 `['memory_machine_check']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### LNX-007 PCIe AER与网卡重置

- 通过｜故障分类：实际 `hardware`，预期 `hardware`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['network_interface', 'nic_reset', 'pcie_aer']`，预期 `['pcie_aer', 'nic_reset']`
- 通过｜命中规则：实际 `['hardware_bus', 'network_link']`，预期 `['hardware_bus']`
- 通过｜不得越级确认：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### LNX-008 内核软锁死与watchdog

- 通过｜故障分类：实际 `system`，预期 `system`
- 通过｜需要现场介入：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['kernel_watchdog']`，预期 `['kernel_watchdog']`
- 通过｜命中规则：实际 `['system_stability']`，预期 `['system_stability']`
- 通过｜不得越级确认：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### LNX-009 systemd服务重启循环

- 通过｜故障分类：实际 `application`，预期 `application`
- 通过｜需要现场介入：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['restart_loop', 'service_failed']`，预期 `['service_failed', 'restart_loop']`
- 通过｜命中规则：实际 `['application_runtime']`，预期 `['application_runtime']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### LNX-010 时钟同步失败影响日志排序

- 通过｜故障分类：实际 `system`，预期 `system`
- 通过｜需要现场介入：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['time_sync_failure']`，预期 `['time_sync_failure']`
- 通过｜命中规则：实际 `['system_time']`，预期 `['system_time']`
- 通过｜建议内容：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### APP-001 监听端口冲突

- 通过｜故障分类：实际 `application`，预期 `application`
- 通过｜需要现场介入：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['port_conflict', 'service_failed']`，预期 `['port_conflict', 'service_failed']`
- 通过｜命中规则：实际 `['application_runtime']`，预期 `['application_runtime']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### APP-002 依赖服务连接被拒绝

- 通过｜故障分类：实际 `application`，预期 `application`
- 通过｜需要现场介入：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['dependency_connection']`，预期 `['dependency_connection']`
- 通过｜命中规则：实际 `['application_dependency']`，预期 `['application_dependency']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### APP-003 DNS解析失败

- 通过｜故障分类：实际 `application`，预期 `application`
- 通过｜需要现场介入：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['dns_failure']`，预期 `['dns_failure']`
- 通过｜命中规则：实际 `['application_dependency']`，预期 `['application_dependency']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### APP-004 TLS证书已过期

- 通过｜故障分类：实际 `application`，预期 `application`
- 通过｜需要现场介入：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['tls_certificate_expired']`，预期 `['tls_certificate_expired']`
- 通过｜命中规则：实际 `['application_dependency']`，预期 `['application_dependency']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### APP-005 数据库死锁

- 通过｜故障分类：实际 `application`，预期 `application`
- 通过｜需要现场介入：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['lock_contention']`，预期 `['lock_contention']`
- 通过｜命中规则：实际 `['application_runtime']`，预期 `['application_runtime']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### NET-001 交换机端口运行状态下降

- 通过｜故障分类：实际 `network`，预期 `network`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['link_down', 'network_interface']`，预期 `['link_down', 'network_interface']`
- 通过｜命中规则：实际 `['network_link']`，预期 `['network_link']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### NET-002 端口短时间反复flap

- 通过｜故障分类：实际 `network`，预期 `network`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['link_flap', 'network_interface']`，预期 `['link_flap', 'network_interface']`
- 通过｜命中规则：实际 `['network_link']`，预期 `['network_link']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### NET-003 接收光功率低

- 通过｜故障分类：实际 `network`，预期 `network`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['network_interface', 'optical_power']`，预期 `['optical_power', 'network_interface']`
- 通过｜命中规则：实际 `['network_link']`，预期 `['network_link']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜不得越级确认：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### NET-004 接口CRC错误持续增长

- 通过｜故障分类：实际 `network`，预期 `network`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['crc_error', 'network_interface']`，预期 `['crc_error', 'network_interface']`
- 通过｜命中规则：实际 `['network_link']`，预期 `['network_link']`
- 通过｜不得越级确认：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### NET-005 LACP成员退出但聚合仍工作

- 通过｜故障分类：实际 `network`，预期 `network`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['lacp_member_down', 'network_interface', 'redundancy_degraded']`，预期 `['lacp_member_down', 'redundancy_degraded']`
- 通过｜命中规则：实际 `['network_redundancy']`，预期 `['network_redundancy']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### NET-006 Linux Bond主备切换

- 通过｜故障分类：实际 `network`，预期 `network`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['bond_member_down', 'network_interface']`，预期 `['bond_member_down']`
- 通过｜命中规则：实际 `['network_link', 'network_redundancy']`，预期 `['network_redundancy']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### NET-007 BGP邻居会话下降

- 通过｜故障分类：实际 `network`，预期 `network`
- 通过｜需要现场介入：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['bgp_neighbor_down']`，预期 `['bgp_neighbor_down']`
- 通过｜命中规则：实际 `['network_control_plane']`，预期 `['network_control_plane']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### NET-008 OSPF邻接关系下降

- 通过｜故障分类：实际 `network`，预期 `network`
- 通过｜需要现场介入：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['network_interface', 'ospf_neighbor_down']`，预期 `['ospf_neighbor_down']`
- 通过｜命中规则：实际 `['network_control_plane']`，预期 `['network_control_plane']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### NET-009 BPDU Guard阻断接入口

- 通过｜故障分类：实际 `network`，预期 `network`
- 通过｜需要现场介入：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['network_interface', 'stp_protection']`，预期 `['stp_protection']`
- 通过｜命中规则：实际 `['network_layer2']`，预期 `['network_layer2']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### NET-010 MAC地址在两个端口间漂移

- 通过｜故障分类：实际 `network`，预期 `network`
- 通过｜需要现场介入：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['link_flap', 'mac_flap', 'network_interface']`，预期 `['mac_flap']`
- 通过｜命中规则：实际 `['network_layer2']`，预期 `['network_layer2']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### NET-011 MLAG对等链路丢失

- 通过｜故障分类：实际 `network`，预期 `network`
- 通过｜需要现场介入：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['link_down', 'mlag_peer_down', 'redundancy_degraded']`，预期 `['mlag_peer_down', 'redundancy_degraded']`
- 通过｜命中规则：实际 `['network_link', 'network_redundancy']`，预期 `['network_redundancy']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### NET-012 交换机单电源模块故障

- 通过｜故障分类：实际 `facility`，预期 `facility`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['power_supply', 'redundancy_degraded']`，预期 `['power_supply', 'redundancy_degraded']`
- 通过｜命中规则：实际 `['facility_power']`，预期 `['facility_power']`
- 通过｜候选原因：实际 `True`，预期 `True`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### NET-013 核心交换机宕机导致多个下联不可达

- 通过｜故障分类：实际 `network`，预期 `network`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜CC提醒：实际 `True`，预期 `True`
- 通过｜重大事件判断：实际 `required`，预期 `required`
- 通过｜事件数量：实际 `1`，预期 `1`
- 通过｜输入数量：实际 `3`，预期 `3`
- 通过｜影响对象数：实际 `3`，预期 `3`
- 通过｜事实类型：实际 `['core_device_failure', 'core_switch_outage', 'multi_device_network']`，预期 `['core_switch_outage', 'multi_device_network']`
- 通过｜命中规则：实际 `['network_core_outage']`，预期 `['network_core_outage']`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### FAC-001 核心机房单路掉电

- 通过｜故障分类：实际 `facility`，预期 `facility`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜CC提醒：实际 `True`，预期 `True`
- 通过｜重大事件判断：实际 `required`，预期 `required`
- 通过｜事实类型：实际 `['redundancy_degraded', 'single_feed_loss']`，预期 `['single_feed_loss', 'redundancy_degraded']`
- 通过｜命中规则：实际 `['facility_power']`，预期 `['facility_power']`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### FAC-002 核心机房双路掉电

- 通过｜故障分类：实际 `facility`，预期 `facility`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜CC提醒：实际 `True`，预期 `True`
- 通过｜重大事件判断：实际 `required`，预期 `required`
- 通过｜事实类型：实际 `['dual_feed_loss', 'single_feed_loss']`，预期 `['dual_feed_loss']`
- 通过｜命中规则：实际 `['facility_power']`，预期 `['facility_power']`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### FAC-003 普通机房单路掉电但设备在线

- 通过｜故障分类：实际 `facility`，预期 `facility`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜CC提醒：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['no_device_impact', 'redundancy_degraded', 'single_feed_loss']`，预期 `['single_feed_loss', 'redundancy_degraded']`
- 通过｜命中规则：实际 `['facility_power']`，预期 `['facility_power']`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### FAC-004 普通机房漏水但未影响设备

- 通过｜故障分类：实际 `facility`，预期 `facility`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜CC提醒：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['no_device_impact', 'water_leak']`，预期 `['water_leak', 'no_device_impact']`
- 通过｜命中规则：实际 `['facility_water']`，预期 `['facility_water']`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### FAC-005 漏水导致核心交换机故障

- 通过｜故障分类：实际 `facility`，预期 `facility`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜CC提醒：实际 `True`，预期 `True`
- 通过｜重大事件判断：实际 `required`，预期 `required`
- 通过｜事件数量：实际 `1`，预期 `1`
- 通过｜输入数量：实际 `2`，预期 `2`
- 通过｜事实类型：实际 `['core_device_failure', 'core_switch_outage', 'water_leak']`，预期 `['water_leak', 'core_device_failure']`
- 通过｜命中规则：实际 `['facility_water', 'network_core_outage']`，预期 `['facility_water']`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### FAC-006 温度升高但没有达到CC条件

- 通过｜故障分类：实际 `facility`，预期 `facility`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜CC提醒：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `not_required`，预期 `not_required`
- 通过｜事实类型：实际 `['no_device_impact', 'temperature']`，预期 `['temperature', 'no_device_impact']`
- 通过｜命中规则：实际 `['facility_temperature']`，预期 `['facility_temperature']`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### FAC-007 机房等级未知时双路掉电等待确认

- 通过｜故障分类：实际 `facility`，预期 `facility`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜CC提醒：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `needs_confirmation`，预期 `needs_confirmation`
- 通过｜事实类型：实际 `['dual_feed_loss', 'single_feed_loss']`，预期 `['dual_feed_loss']`
- 通过｜命中规则：实际 `['facility_power']`，预期 `['facility_power']`
- 通过｜不得误命中规则：实际 `['facility_power']`，预期 `['system_memory_pressure']`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

### FAC-008 烟雾告警等待SOP确认

- 通过｜故障分类：实际 `facility`，预期 `facility`
- 通过｜需要现场介入：实际 `True`，预期 `True`
- 通过｜CC提醒：实际 `False`，预期 `False`
- 通过｜重大事件判断：实际 `needs_confirmation`，预期 `needs_confirmation`
- 通过｜事实类型：实际 `['smoke_alarm']`，预期 `['smoke_alarm']`
- 通过｜命中规则：实际 `['facility_fire']`，预期 `['facility_fire']`
- 通过｜模拟数据标识：实际 `True`，预期 `True`

