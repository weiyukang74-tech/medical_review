


# 项目汇聚测试入口

本目录包含：

- `POC/规则证据Agent`：生成规则证据结果；
- `POC/查询程序`：根据标签、映射表和脱敏病历查询证据；
- `POC/复核Agent`：读取复核上下文并输出复核结果；
- `POC/标签视图映射`：包含原版映射表和完整版映射表；
- `datat`：脱敏病历、真实六视图和相关源数据。

三个程序的默认路径都基于本目录内的 `POC` 和 `datat`，不会再读取原项目目录。"本次就诊中‘大关节松动训练’的110天记录中，包含不属于同一支付周期或不应合并计算的特殊情况（如跨年度结算、多部位独立计费规则等，需结合深圳当地具体细则判断，此处作为备选核验）。"

## 推荐测试顺序

```powershell
cd D:\AI\AI_S2\项目汇聚\POC\查询程序
C:\Users\weyk\anaconda3\python.exe -m evidence_retriever `
  --mapping "D:\AI\AI_S2\项目汇聚\POC\标签视图映射\规则证据标签关系映射表_完整版.xlsx" `
  --medical-data "D:\AI\AI_S2\项目汇聚\datat\脱敏病历.md" `
  --medins-id H44030500096 `
  --mdtrt-id 119797913
```

```powershell
cd D:\AI\AI_S2\项目汇聚\POC\复核Agent
C:\Users\weyk\anaconda3\python.exe -m review_agent
```

如果只验证当前已生成的结果，可以直接运行查询程序或复核Agent的默认命令。
