# sql-analysis-agent

> 中文 Text-to-SQL 数据分析 Agent：自然语言提问 → 自动生成 SQL → **安全护栏** → 执行 → **失败自修复** → 结论。附 25 道业务题的执行准确率评测，以及一份自动生成的周报样例。

![deps](https://img.shields.io/badge/dependencies-stdlib%20only-green)
![eval](https://img.shields.io/badge/eval-25%20questions-blue)

## 为什么做这个项目

数据分析 Agent 岗的要求一般是两头的：一头是 **SQL / Pandas / 数仓概念**，另一头是 **RAG / Function Calling / Agent 工作流**。Text-to-SQL 正好是两者的交汇点——它逼你同时处理**业务口径、Schema 理解、SQL 正确性判定和安全边界**。

## 快速开始

```bash
git clone https://github.com/ruuy7237/sql-analysis-agent.git
cd sql-analysis-agent

# 已接入 DeepSeek：项目根目录的 .env 里放好 LLM_API_KEY，默认就走真实模型
python db/seed.py                 # 生成确定性示例电商库（120 客户 / 30 商品 / 1500 订单）
python evals/run_eval.py          # 25 道题执行准确率评测
python weekly_report.py           # 生成一份本周经营周报

# 单条提问
python -m agent.sql_agent "上个月各个品类的销售额是多少？" --show-sql
python -m agent.sql_agent "GMV 最高的城市是哪一个？"

# 想退回规则基线（不联网、不花钱）就删掉 .env，再指定引擎：
python -m agent.sql_agent "GMV 最高的城市是哪一个？" --engine rule
```

## 链路

```
中文问题
  │
  ▼
模式链接（自动抽出相关表的 DDL + 低基数列取值 + 样本行 + 业务口径）
  │
  ▼
生成 SQL（LLM 或规则基线）──▶ 安全护栏（只读拦截 / 自动补 LIMIT）
  │                                  │
  │                            拦截则直接失败
  ▼
执行 ──报错──▶ 把错误信息回喂，重新生成（最多 2 次）
  │
  ▼
结果 → 自然语言结论
```

| 文件 | 职责 |
| --- | --- |
| `db/seed.py` | 确定性生成示例电商库（固定随机种子，任何人结果一致） |
| `agent/schema.py` | 模式链接：DDL + 枚举值 + 样本行 + 业务口径说明 |
| `agent/sql_agent.py` | 生成 / 护栏 / 执行 / 自修复 / 结论生成 |
| `agent/rule_baseline.py` | 规则基线生成器，作为评测的对照方案 |
| `evals/questions.json` | 25 道中文业务题 + 标准 SQL |
| `evals/run_eval.py` | 执行准确率评测与错误归因 |
| `weekly_report.py` | 跑 8 项固定分析，自动生成 Markdown 周报 |

### 安全护栏（重点）

Agent 会执行 SQL，所以这道门必须硬：

- 只允许 `SELECT` / `WITH`，命中 `INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/REPLACE/ATTACH/PRAGMA/VACUUM` 任一关键字即拦截；
- 自动补 `LIMIT 200`，防止全表扫出几十万行把内存打满；
- 关键字检测必须使用**词边界** `\b...\b`，否则 `created_at` 会被误判成 `CREATE`（本项目踩过并已修复，见 `agent/sql_agent.py` 注释）；
- 执行失败时把错误回喂模型自修复，最多 2 轮，修不好就如实报告失败而不是假装成功。

## 评测

**判定方式用「执行准确率」而不是比对 SQL 字符串**：同一语义本来就有多种写法，比对字符串会冤枉正确答案。做法是分别执行生成的 SQL 和标准 SQL，把结果集归一化为「忽略列名、数值保留两位、行排序」后再判定相等。

评测集 25 题（easy 8 / medium 11 / hard 6），覆盖：

| 类型 | 例子 |
| --- | --- |
| 单表聚合 | 一共有多少笔订单 / 平均订单金额 |
| 多表 JOIN | 上海的 GMV / 各品类销售额 |
| 分组排序 TopN | 销售额前三的品类 / GMV 最高的城市 |
| 相对时间 | 上个月 GMV / 最近 30 天订单数 / 今年 GMV |
| 双指标 | 按月 GMV 趋势 |

结果见 **[evals/report.md](evals/report.md)**，当前一轮（已接入 DeepSeek 真实模型）：

| 方案 | 执行准确率 | easy (8) | medium (11) | hard (6) |
| --- | --- | --- | --- | --- |
| rule-baseline（关键词规则） | 40.0%（10/25） | 25.0% | 54.5% | 33.3% |
| **LLM（deepseek-chat）** | **76.0%（19/25）** | 87.5% | 81.8% | 50.0% |

规则基线的价值在于提供参照：它能稳定做对「计数 + 简单过滤」这类，但遇到同义改写（「客单价」→ AVG）、多指标、隐含口径（GMV 只算已完成和已发货）就全面失效。把这两组的差距摆在报告里，才说得清 LLM 方案带来了什么。

### 五类典型错误

| 错误类型 | 例子 | 解法 |
| --- | --- | --- |
| 字段名编造 | 写成 `order.amount` | 提示里给完整 DDL，评测暴露后加 schema 校验 |
| 口径不一致 | GMV 忘过滤「已发货」 | 把业务口径写成提示的一部分并给少样本 |
| 相对时间写死 | 直接算成 `'2026-09'` | 少样本里示范 `strftime('%Y-%m','now','-1 month')` |
| 聚合层级错 | 想要 SUM 却写成 AVG | few-shot 覆盖「客单价」这类易混词 |
| JOIN 漏表 | 缺 customers 直接取 city | 模式链接时列出可用关联字段 |

## 自动周报

`python weekly_report.py` 会跑 8 项固定分析（本月/上月 GMV、订单数、客单价、取消数、Top5 城市、Top5 品类、近 6 月趋势），输出 Markdown + JSON，样例见 `reports/weekly_2026-10-02.md`。这就是 JD 里「自动化报表、周报」那一栏的落地。

## 踩坑记录

1. **护栏误伤 `created_at`**：正则写成了前缀匹配，把所有含 `create` 的合法 SQL 全拦了，一度导致周报 8 项全部执行失败——加词边界 `\b` 解决；
2. **示例数据必须是相对时间生成的**：如果订单日期写死，半年后别人 clone 仓库再问「最近 30 天」会得到 0；现在按「距今天 0~400 天」随机生成，题目永远有数据；
3. **执行准确率要用结果集比对**：早期用字符串比对，模型只要换个聚合写法就被判错，指标虚低且无法指导优化；
4. **低基数列的取值要喂给模型**：不给 `status`/`level` 的枚举，模型只能猜，`status='成功'` 这种错误非常常见。

## TODO

- [ ] 增加列级权限与脱敏（手机号、身份证）
- [ ] 结果自动转图表（ASCII / matplotlib）
- [ ] 引入 Self-Consistency：生成多条 SQL 投票选出一致结果
- [ ] 评测集扩充到 50 题，加入跨库分组 rolling 等复杂场景
