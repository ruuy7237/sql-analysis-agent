# -*- coding: utf-8 -*-
"""自动周报：跑一组固定分析任务，产出 Markdown 报告。

这一块对应 JD 里「自动化报表、周报」的要求：把重复性的取数工作模板化，
每周一次自动生成，人工只负责解读。

用法：
  python weekly_report.py
  python weekly_report.py --out reports/my_report.md
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from agent.sql_agent import DB_PATH, analyze, execute  # noqa: E402

ANALYSES = [
    ("本月 GMV（元）",
     "SELECT ROUND(SUM(total_amount),2) FROM orders "
     "WHERE status IN ('已完成','已发货') AND strftime('%Y-%m',created_at)=strftime('%Y-%m','now')"),
    ("上月 GMV（元）",
     "SELECT ROUND(SUM(total_amount),2) FROM orders "
     "WHERE status IN ('已完成','已发货') AND strftime('%Y-%m',created_at)=strftime('%Y-%m','now','-1 month')"),
    ("本月订单数",
     "SELECT COUNT(*) FROM orders WHERE strftime('%Y-%m',created_at)=strftime('%Y-%m','now')"),
    ("本月客单价（元）",
     "SELECT ROUND(AVG(total_amount),2) FROM orders "
     "WHERE status IN ('已完成','已发货') AND strftime('%Y-%m',created_at)=strftime('%Y-%m','now')"),
    ("本月取消订单数",
     "SELECT COUNT(*) FROM orders WHERE status='已取消' "
     "AND strftime('%Y-%m',created_at)=strftime('%Y-%m','now')"),
    ("本月 Top5 城市 GMV",
     "SELECT c.city, ROUND(SUM(o.total_amount),2) FROM orders o "
     "JOIN customers c ON o.customer_id=c.id WHERE o.status IN ('已完成','已发货') "
     "AND strftime('%Y-%m',o.created_at)=strftime('%Y-%m','now') "
     "GROUP BY c.city ORDER BY SUM(o.total_amount) DESC LIMIT 5"),
    ("本月 Top5 品类销售额",
     "SELECT cat.name, ROUND(SUM(oi.amount),2) FROM order_items oi "
     "JOIN orders o ON o.id=oi.order_id JOIN products p ON p.id=oi.product_id "
     "JOIN categories cat ON cat.id=p.category_id WHERE o.status IN ('已完成','已发货') "
     "AND strftime('%Y-%m',o.created_at)=strftime('%Y-%m','now') "
     "GROUP BY cat.id ORDER BY SUM(oi.amount) DESC LIMIT 5"),
    ("近 6 个月 GMV 趋势",
     "SELECT strftime('%Y-%m',created_at), ROUND(SUM(total_amount),2) FROM orders "
     "WHERE status IN ('已完成','已发货') GROUP BY strftime('%Y-%m',created_at) "
     "ORDER BY strftime('%Y-%m',created_at) DESC LIMIT 6"),
]


def render(results):
    today = datetime.date.today()
    lines = [f"# 电商周报（{today.isoformat()}）", "", "> 由 sql-analysis-agent 自动生成", ""]
    for item in results:
        title, res = item["title"], item
        lines.append(f"## {title}")
        if not res.get("ok") or not res.get("rows"):
            lines += [f"- 未取到数据（{res.get('error', '空结果')}）", ""]
            continue
        rows = res["rows"]
        if len(rows) == 1 and len(rows[0]) == 1:
            lines += [f"- **{rows[0][0]}**", ""]
        else:
            lines.append("| 项 | 值 |")
            lines.append("| --- | --- |")
            for r in rows[:10]:
                lines.append("| " + " | ".join(str(x) for x in r) + " |")
            lines.append("")
    lines.append("生成脚本：`python weekly_report.py`")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join("reports", f"weekly_{datetime.date.today().isoformat()}.md"))
    ap.add_argument("--db", default=DB_PATH)
    a = ap.parse_args()

    if not os.path.exists(a.db):
        raise SystemExit("数据库不存在，先运行 python db/seed.py")

    results = []
    for title, sql in ANALYSES:
        res = execute(sql, a.db)
        results.append({"title": title, "sql": sql, **res})
        status = "✓" if res["ok"] and res["rows"] else "✗"
        print(f"  {status} {title}")

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    md = render(results)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(md)
    with open(a.out.replace(".md", ".json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n周报已写入 {a.out}")


if __name__ == "__main__":
    main()
