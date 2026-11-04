# tests/samples —— 厂商回显样本（脱敏）

供 `tests/test_vendor_parser.py` 做 golden test：解析逻辑一改，这些断言就会响。

## 来源

取自 2026-10-28 现场采集的真实归档日志（`<归档根>/2026-10-28/<设备名>.log`），
每个厂商挑一台「存在非零 CRC」的设备（烽火当天全 0，样本里也是 0）。

## 截取与脱敏规则

- 每个样本只保留 **厂商标识首行 + 2 个接口块**（目标接口 + 相邻一个）
- IPv4 → `10.0.0.N`
- MAC（`xxxx.xxxx.xxxx` / `xx:xx:xx:xx:xx:xx`）→ 固定占位值
- `Description:` 的值 → `DESC-<接口名>`（保留"描述在哪一行"这个结构，
  但不保留任何现场设备名/IP/业务标识）
- 行尾统一 CRLF（与设备回显一致）

已核对：样本里不含真实 IP 段、不含现场设备名（`172.x`、`10.185.x`、
`SJZX`、`WXXQ`、`BASS`、`NN-2-` 等均无残留）。

## 各样本的期望值（由 golden test 断言）

| 样本 | 厂商 | 目标接口 | CRC 值 |
|---|---|---|---|
| cisco.log | cisco | Ethernet2/11 | 28407803 |
| dptech.log | dptech | bond2 | 6 |
| fiberhome.log | fiberhome | 10gigaethernet1/0/1 | 0（当天无增长） |
| h3c.log | h3c | Ten-GigabitEthernet4/0/43 | 30964 |
| huawei.log | huawei | GigabitEthernet6/0/9 | 1201101608 |
| ruijie.log | ruijie | GigabitEthernet0/37 | 146051 |
| zte.log | zte | xgei-0/1/1/4 | 1 |

## 新增样本时

照上面的规则截取 + 脱敏，然后在 `tests/test_vendor_parser.py` 的 `CASES`
里补一行期望值，`python tests/test_vendor_parser.py` 跑一遍即可。
**不要**直接拷未脱敏的原始日志进来。
