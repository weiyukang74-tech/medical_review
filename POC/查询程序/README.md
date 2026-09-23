# 查询程序（POC）

本程序根据规则证据 JSON 中的一、二级标签读取确认后的关系映射表，再从 `datat/南山人医-数据导出` 下的视图 Excel 中取得结构化数据或病历原文。程序优先使用规则证据结果中的 `mdtrt_id`，不再使用写死的就诊号；仍兼容传入旧版 `脱敏病历.md` 文件。

当前规则：

1. 结构化数据先按 `medinsId + mdtrtId` 查询；
2. 诊断信息根据 `target_fact` 生成检索词，再对 `diseName` 做包含匹配，检索词不写死；
3. 患者信息只返回标签对应的字段；
4. 病历按 `medinsId + mdtrtId + recordName/recordType` 查询，并完整返回视图中的 `recordContent` 原文；复核上下文只在相同 `recordName` 的病历组内提取公共内容，公共内容按字段拼接展示，每条记录的差异内容也按字段拼接展示；
5. 首先执行主要来源；
6. 主要来源无数据、视图不可用或预期字段缺失时，自动查询次要来源；
7. 查询程序不判断医学事实和申诉命题。

## 运行

```powershell
 cd D:\AI\AI_S2\项目汇聚\POC\查询程序
C:\Users\weyk\anaconda3\python.exe -m evidence_retriever
```

不指定 `--rule-id` 时，程序会读取规则证据结果中的全部 `rule_id`，逐条使用各自的 `mdtrt_id` 查询。每条规则分别输出 `查询结果_RULE-001.json` 和 `复核上下文_RULE-001.json`，并生成 `查询结果索引.json`。

只测试 `P01`：

```powershell
C:\Users\weyk\anaconda3\python.exe -m evidence_retriever --proposition-id P01
```

指定单条规则时：

```powershell
C:\Users\weyk\anaconda3\python.exe -m evidence_retriever `
  --rule-evidence "D:\AI\AI_S2\项目汇聚\POC\规则证据结果\规则证据结果.json" `
  --mapping "D:\AI\AI_S2\项目汇聚\POC\标签视图映射\规则证据标签关系映射表_完整版.xlsx" `
  --rule-id RULE-001 `
  --output "D:\AI\AI_S2\项目汇聚\POC\查询结果\查询结果.json" `
  --review-context-output "D:\AI\AI_S2\项目汇聚\POC\查询结果\复核上下文.json"
```

## 输出结构

输出按 `rule_id → proposition_id → evidence` 分组。每个标签只保留：

- 一级证据域；
- 二级证据标签；
- `target_fact`；
- 查询状态、来源和数据。

诊断结果中的 `matched_by.value_source` 固定标明为 `target_fact`。病历结果中的 `recordContent` 是查询到的完整原文；复核上下文中的病历公共内容和差异内容放在 `medical_document_groups` 中。

查询结果写入完成后，程序会继续生成复核上下文。复核上下文以命题为中心合并规则判断依据与查询结果。重复结构化记录只在 `structured_data_pool` 中保存一次，并通过 `structured_data_refs` 引用；重复病历原文只在 `medical_documents` 中保存一次，并通过 `document_refs` 引用。

## 测试

```powershell
C:\Users\weyk\anaconda3\python.exe -m unittest discover -s tests -v
```
# 查询规划文件

查询程序默认读取 `D:\AI\AI_S2\项目汇聚\POC\查询规划结果\查询规划结果_RULE-XXX.json`。
如果存在对应规划，费用明细将按 `chrg_type` 和名称检索词过滤。病例文书不使用关键词筛选，查询结果保留完整原始记录；复核上下文只对相同 `recordName` 的病历进行公共内容提取。命中行不去重、不聚合、不裁剪字段。
如果当前事实没有专属费用查询计划，程序优先继承同一申诉命题中其他事实的争议实体。若同一命题也没有可继承实体，则标记 `QUERY_SCOPE_UNRESOLVED`，费用明细返回空数组，不再默认全量返回。

费用明细查询结果始终保留全量原始记录。构建复核上下文时，若同一费用项目记录达到摘要阈值且跨多个日期，程序按标准病历视图字典生成摘要，分别提供去重后的实际发生天数 `distinct_service_days` 和 `cnt`累计次数 `total_service_times`。只有全部字段完全相同的记录才计为技术性重复，不删除业务记录。
