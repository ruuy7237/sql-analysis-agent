# -*- coding: utf-8 -*-
"""Text-to-SQL 评测：执行准确率。

判定方式（公平起见刻意不用字符串比对）：跑「生成的 SQL」和「标准 SQL」，
把两边的结果集都归一化为「去掉列名、数值保留两位小数、行排序」再做相等判定。
同一语义本来就可能有多种写法，比 SQL 字符串没有意义。

同时对照两组方案：
  rule-baseline  纯关键词拼 SQL 的规则方案（刻意做得简单，作为参照组）
  llm            真实模型生成（需要 LLM_API_KEY；无 Key 时自动跳过）

用法：
  python evals/run_eval.py
"""
import argparse
import datetime
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agent import llm  # noqa: E402
from agent.rule_baseline import generate as rule_generate  # noqa: E402
from agent.schema import build_schema_text  # noqa: E402
from agent.sql_agent import DB_PATH, analyze, execute, extract_sql  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
QFILE = os.path.join(HERE, "questions.json")
REPORT = os.path.join(HERE, "report.md")
RESULTS = os.path.join(HERE, "results.json")


def norm_rows(rows):
    out = []
    for r in rows:
        vals = []
        for v in r:
            if isinstance(v, float):
                vals.append(round(v, 2))
            elif isinstance(v, (int,)) and not isinstance(v, bool):
                vals.append(float(v))
            else:
                vals.append(str(v))
        out.append(tuple(vals))
    return sorted(out, key=lambda t: tuple(str(x) for x in t))


def run_gold(sql, db_path):
    return execute(sql, db_path)


def evaluate(items, db_path, include_llm):
    records, gold_errors = [], []
    for it in items:
        gold = run_gold(it["sql"], db_path)
        if not gold["ok"]:
            gold_errors.append({"id": it["id"], "q": it["q"], "error": gold["error"]})
            continue

        gold_norm = norm_rows(gold["rows"])
        row = {"id": it["id"], "level": it["level"], "q": it["q"], "gold_preview": gold["rows"][:2]}

        pred_sql = rule_generate(it["q"])
        if pred_sql:
            res = execute(pred_sql, db_path)
            row["rule_sql"] = res["sql"]
            row["rule_ok"] = bool(res["ok"]) and norm_rows(res["rows"]) == gold_norm
            row["rule_err"] = res["error"] if not res["ok"] else ""
        else:
            row["rule_sql"], row["rule_ok"], row["rule_err"] = "", False, "规则基线拼不出 SQL"

        if include_llm:
            out = analyze(it["q"], db_path)
            row["llm_sql"] = out["sql"]
            row["llm_ok"] = bool(not out["error"]) and norm_rows(out["rows"]) == gold_norm
            row["llm_repaired"] = out.get("repaired", False)
            row["llm_err"] = out["error"] if out["error"] else ""

        records.append(row)
    return records, gold_errors


def summarize(records, key):
    if not records:
        return {"total": 0, "correct": 0, "accuracy": 0.0}
    by_level = {}
    ok = sum(1 for r in records if r.get(key))
    for r in records:
        d = by_level.setdefault(r["level"], {"total": 0, "ok": 0})
        d["total"] += 1
        d["ok"] += 1 if r.get(key) else 0
    return {
        "total": len(records), "correct": ok,
        "accuracy": round(ok / len(records) * 100, 1),
        "by_level": {k: {"total": v["total"], "accuracy": round(v["ok"] / v["total"] * 100, 1)}
                     for k, v in by_level.items()},
    }


def error_taxonomy(records, key="rule"):
    buckets = {}
    for r in records:
        if r.get(f"{key}_ok"):
            continue
        err = r.get(f"{key}_err", "")
        if "拼不出" in err:
            k = "规则无法覆盖（未生成 SQL）"
        elif "no such column" in err or "no such table" in err:
            k = "字段名/表名错误"
        elif "syntax" in err.lower():
            k = "SQL 语法错误"
        elif err:
            k = "SQL 执行错误（其它）"
        else:
            k = "逻辑错误（能跑但结果不对）"
        buckets[k] = buckets.get(k, 0) + 1
    return dict(sorted(buckets.items(), key=lambda kv: -kv[1]))


def write_report(items, records, gold_errors, rule_sum, llm_sum, include_llm, label):
    lines = [
        "# Text-to-SQL 评测报告（自动生成）",
        "",
        f"- 生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 生成引擎：**{label}**",
        f"- 评测集：{len(items)} 道中文业务题（easy / medium / hard）",
        "- 判定口径：**执行准确率**（比对结果集，忽略列名与写法差异）",
        "",
        "## 1. 总体对比",
        "",
        "| 方案 | 执行准确率 | 正确/总数 |",
        "| --- | --- | --- |",
        f"| rule-baseline（关键词规则） | {rule_sum['accuracy']}% | {rule_sum['correct']}/{rule_sum['total']} |",
    ]
    if include_llm:
        lines.append(f"| llm（{label}） | {llm_sum['accuracy']}% | {llm_sum['correct']}/{llm_sum['total']} |")

    lines += ["", "## 2. 分难度准确率", "", "| 难度 | rule-baseline |", "| --- | --- |"]
    for lv in ("easy", "medium", "hard"):
        d = rule_sum.get("by_level", {}).get(lv)
        if d:
            lines.append(f"| {lv} ({d['total']} 题) | {d['accuracy']}% |")
    if include_llm:
        lines += ["", "| 难度 | llm |", "| --- | --- |"]
        for lv in ("easy", "medium", "hard"):
            d = llm_sum.get("by_level", {}).get(lv)
            if d:
                lines.append(f"| {lv} ({d['total']} 题) | {d['accuracy']}% |")

    key = "llm" if include_llm else "rule"
    lines += ["", f"## 3. 错误归因（{key} 方案）", "", "| 错误类型 | 数量 |", "| --- | --- |"]
    for k, v in error_taxonomy(records, key).items():
        lines.append(f"| {k} | {v} |")

    lines += ["", "## 4. 典型错题（前 10 条）", "", "| 题号 | 问题 | 标准结果预览 | 生成结果 |", "| --- | --- | --- | --- |"]
    wrong = [r for r in records if not r.get(f"{key}_ok")][:10]
    for r in wrong:
        lines.append(
            f"| {r['id']} | {r['q'][:26]} | {str(r['gold_preview'])[:34]} | "
            f"{(r.get(f'{key}_err') or '结果不一致')[:30]} |")

    if gold_errors:
        lines += ["", "> ⚠️ 有标准 SQL 无法执行（评测集本身需要修）："]
        for g in gold_errors:
            lines.append(f"> - {g['id']} {g['q']}：{g['error']}")

    if include_llm:
        lines += ["", "> 以上为接入 DeepSeek（deepseek-chat）后跑出的真实成绩。", ""]
    else:
        lines += ["", "> 当前为规则基线（未配置 LLM_API_KEY）。配置 key 后重跑可得到 LLM 方案的数字。", ""]
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return REPORT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB_PATH)
    a = ap.parse_args()

    if not os.path.exists(a.db):
        raise SystemExit("数据库不存在，先运行 python db/seed.py")
    with open(QFILE, "r", encoding="utf-8") as f:
        items = json.load(f)["questions"]

    include_llm = llm.mode() == "api"
    label = f"{llm.MODEL}" if include_llm else "rule-baseline（mock 模式，未接 LLM）"
    print(f"评测开始 | {label} | {len(items)} 道题")

    records, gold_errors = evaluate(items, a.db, include_llm)
    rule_sum = summarize(records, "rule_ok")
    llm_sum = summarize(records, "llm_ok") if include_llm else {"total": 0, "correct": 0, "accuracy": 0.0}

    print(f"  rule-baseline: {rule_sum['accuracy']}% ({rule_sum['correct']}/{rule_sum['total']})")
    if include_llm:
        print(f"  llm         : {llm_sum['accuracy']}% ({llm_sum['correct']}/{llm_sum['total']})")
    if gold_errors:
        print(f"  ⚠️ 标准 SQL 执行失败 {len(gold_errors)} 条")

    rep = write_report(items, records, gold_errors, rule_sum, llm_sum, include_llm, label)
    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump({"rule": rule_sum, "llm": llm_sum, "records": records,
                   "gold_errors": gold_errors}, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n报告已写入 {rep}")


if __name__ == "__main__":
    main()
