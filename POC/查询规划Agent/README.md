# 查询规划Agent

读取规则证据JSON，使用与规则证据Agent相同的Qwen3.6配置，为映射到费用明细或检验报告视图的标签生成查询计划。费用明细按收费项目字段检索，检验报告按`itemName`和`indexName`检索。规划完成后默认自动启动查询程序。

当规则描述涉及同时收费、重复收费、互斥收费或收费时间比较，但规则证据JSON未选择费用标签时，规划Agent会保持原JSON不变，自动增加费用明细查询计划，并标记为 `AUTO_SUPPLEMENTED`。

运行时会输出查询规划模型的首Token、Token/s、模型用时和输出Token，并在查询完成后输出查询程序用时与全链路总用时。

查询规划Agent使用 `QUERY_PLANNING_MODEL_TIMEOUT_SECONDS` 设置独立超时时间。每次请求失败时会立即输出请求次数、失败阶段、耗时、异常类型、HTTP状态或底层原因，以及是否继续重试。

默认批量处理全部规则：

```powershell
cd D:\AI\AI_S2\项目汇聚\POC\查询规划Agent
C:\Users\weyk\anaconda3\python.exe -m query_planning_agent
```

仅处理单条规则：

```powershell
C:\Users\weyk\anaconda3\python.exe -m query_planning_agent --rule-id RULE-002
```

只生成规划、不执行查询：

```powershell
C:\Users\weyk\anaconda3\python.exe -m query_planning_agent --rule-id RULE-002 --plan-only
```

查询规划输出位于 `D:\AI\AI_S2\项目汇聚\POC\查询规划结果`，随后生成查询结果和复核上下文。查询规划Agent与查询程序仍是两个独立目录，可以分别修改和单独运行。
