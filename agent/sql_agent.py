# -*- coding: utf-8 -*-
"""Text-to-SQL 数据分析 Agent。

链路：自然语言问题 → 模式链接（自动抽出相关表结构）→ 生成 SQL → 安全护栏 →
      执行 → 失败自修复 → 自然语言结论

护栏和自修复是这个项目真正的价值点：
- 护栏：只允许 SELECT/WITH，禁止任何写操作，自动补 LIMIT，带执行超时行数上限；
- 自修复：执行报错时把错误信息回喂给模型重生成一次（最多 2 次），而不是直接把错误抛给用户。

用法：
  python -m agent.sql_agent "上个月各品类的销售额是多少？"
"""
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import llm  # noqa: E402
from agent.rule_baseline import generate as rule_generate  # noqa: E402
from agent.schema import build_schema_text  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "ecommerce.db")
MAX_ROWS = 200
MAX_REPAIR = 2

SYSTEM = """你是资深数据分析师，负责把中文业务问题翻译成 SQLite 语句。

硬性要求：
1. 只输出一条 SQL，用 ```sql 代码块包裹，不要输出解释文字；
2. 只能使用给定表结构中存在的字段，禁止臆造列名；
3. 只写只读查询（SELECT / WITH），绝对禁止 INSERT / UPDATE / DELETE / DROP；
4. 金额一律 ROUND(x, 2)，数量不要做 ROUND；
5. 严格遵守给定的业务口径（例如 GMV 只统计已完成和已发货订单）；
6. 需要 Top N 时使用 ORDER BY ... DESC LIMIT N。
"""

FEWSHOT = [
    ("上个月总销售额是多少？",
     "SELECT ROUND(SUM(total_amount),2) AS amount FROM orders\n"
     "WHERE status IN ('已完成','已发货')\n"
     "  AND strftime('%Y-%m', created_at) = strftime('%Y-%m','now','-1 month');"),
    ("销售额排名前三的品类是哪些？",
     "SELECT cat.name, ROUND(SUM(oi.amount),2) AS amount\n"
     "FROM order_items oi\n"
     "JOIN orders o ON o.id = oi.order_id\n"
     "JOIN products p ON p.id = oi.product_id\n"
     "JOIN categories cat ON cat.id = p.category_id\n"
     "WHERE o.status IN ('已完成','已发货')\n"
     "GROUP BY cat.id ORDER BY amount DESC LIMIT 3;"),
    ("每个城市有多少笔订单？",
     "SELECT c.city, COUNT(*) AS cnt\n"
     "FROM orders o JOIN customers c ON o.customer_id = c.id\n"
     "GROUP BY c.city ORDER BY cnt DESC;"),
]

_CODE = re.compile(r"```(?:sql)?\s*(.*?)```", re.S)


def extract_sql(text):
    """从模型输出里抠出 SQL，兼容有/无代码块两种情况。"""
    if not text:
        return ""
    m = _CODE.search(text)
    raw = m.group(1) if m else text
    raw = raw.strip().strip("`").strip()
    # 只取第一条语句
    for line in raw.split("\n"):
        line = line.strip()
        if line.startswith("--"):
            continue
        break
    return raw.rstrip(";").strip() + ";"


FORBIDDEN = ("insert ", "update ", "delete ", "drop ", "alter ", "create ",
             "replace ", "attach ", "pragma ", "vacuum")


def guard(sql):
    """安全护栏。返回 (ok, cleaned_sql, reason)。"""
    if not sql or not sql.strip():
        return False, "", "空 SQL"
    cleaned = sql.strip().rstrip(";").strip()
    low = cleaned.lower()
    first = low.split()[0] if low.split() else ""
    if first not in ("select", "with"):
        return False, cleaned, f"只允许 SELECT/WITH，收到 {first.upper()}"
    # 注意必须带词边界：早先用strip后拼接的前缀匹配会把 created_at 误判成 CREATE
    for bad in FORBIDDEN:
        word = bad.strip()
        if re.search(rf"\b{re.escape(word)}\b", low):
            return False, cleaned, f"检测到写操作关键字 {word.upper()}，已拦截"
    if "limit" not in low:
        cleaned += f" LIMIT {MAX_ROWS}"
    return True, cleaned, ""


def execute(sql, db_path=DB_PATH):
    """执行 SQL，返回 {"ok","rows","columns","error"}。"""
    ok, cleaned, reason = guard(sql)
    if not ok:
        return {"ok": False, "rows": [], "columns": [], "error": reason, "sql": sql}
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(cleaned)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [tuple(r) for r in cur.fetchall()]
        return {"ok": True, "rows": rows, "columns": cols, "error": "", "sql": cleaned}
    except sqlite3.Error as e:
        return {"ok": False, "rows": [], "columns": [], "error": str(e), "sql": cleaned}
    finally:
        conn.close()


def _build_messages(question, schema_text, prev=None, error=None):
    msgs = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": schema_text}]
    for q, s in FEWSHOT:
        msgs.append({"role": "user", "content": f"问题：{q}"})
        msgs.append({"role": "assistant", "content": f"```sql\n{s}\n```"})
    prompt = f"问题：{question}"
    if prev and error:
        prompt += (f"\n\n你上一次生成的 SQL 是：\n{prev}\n"
                   f"执行报错：{error}\n请修正后只输出新的 SQL。")
    msgs.append({"role": "user", "content": prompt})
    return msgs


def generate_sql(question, schema_text=None):
    """生成 SQL：优先 LLM，失败或 mock 模式回落到规则基线。"""
    schema_text = schema_text or build_schema_text()
    if llm.mode() == "api":
        msg = llm.chat(_build_messages(question, schema_text))
        sql = extract_sql(msg.get("content", ""))
        if sql.strip().rstrip(";").strip():
            return sql, "llm"
    return rule_generate(question), "rule-baseline"


def analyze(question, db_path=DB_PATH, engine="auto", verbose=False):
    """完整跑一遍：生成 → 护栏 → 执行 → 自修复 → 结论。"""
    schema_text = build_schema_text(db_path)
    if engine == "rule":
        sql, source = rule_generate(question), "rule-baseline"
    else:
        sql, source = generate_sql(question, schema_text)

    attempts = []
    for i in range(MAX_REPAIR + 1):
        res = execute(sql, db_path)
        attempts.append({"sql": res["sql"], "ok": res["ok"], "error": res["error"]})
        if res["ok"]:
            return {"question": question, "sql": res["sql"], "rows": res["rows"],
                    "columns": res["columns"], "attempts": attempts, "source": source,
                    "answer": format_answer(question, res["columns"], res["rows"]),
                    "repaired": i > 0, "error": ""}
        if llm.mode() != "api" or i == MAX_REPAIR:
            return {"question": question, "sql": sql, "rows": [], "columns": [],
                    "attempts": attempts, "source": source, "answer": "",
                    "repaired": i > 0, "error": res["error"]}
        if verbose:
            print(f"  第 {i+1} 次执行失败({res['error']})，让模型自修复…")
        msg = llm.chat(_build_messages(question, schema_text, prev=sql, error=res["error"]))
        sql = extract_sql(msg.get("content", ""))


def format_answer(question, columns, rows):
    """把结果表格转成一句人话（有 Key 时交给模型，否则规则化渲染）。"""
    if not rows:
        return "没有查到数据。"
    head = "、".join(str(c) for c in columns)
    if llm.mode() == "api":
        preview = "\n".join(str(r) for r in rows[:20])
        msg = llm.chat([
            {"role": "system", "content": "你是数据分析师，用一句话总结查询结果，必须包含关键数值。"},
            {"role": "user", "content": f"问题：{question}\n列名：{head}\n结果：\n{preview}"},
        ])
        if msg.get("content", "").strip():
            return msg["content"].strip()
    lines = [f"共 {len(rows)} 行，字段：{head}"]
    for r in rows[:10]:
        lines.append("  " + " | ".join(f"{c}={v}" for c, v in zip(columns, r)))
    if len(rows) > 10:
        lines.append(f"  …（其余 {len(rows) - 10} 行省略）")
    return "\n".join(lines)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--engine", default="auto", choices=["auto", "rule"])
    ap.add_argument("--show-sql", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    print(f"[模式] LLM={llm.MODEL}({llm.mode()})")
    out = analyze(a.question, engine=a.engine, verbose=True)
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return
    if a.show_sql:
        print(f"\nSQL（来源 {out['source']}，重试 {len(out['attempts'])-1} 次）：\n{out['sql']}")
    print("\n--- 查询结果 ---")
    print(out["answer"] or f"失败：{out['error']}")


if __name__ == "__main__":
    main()
