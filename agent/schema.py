# -*- coding: utf-8 -*-
"""从 SQLite 库里自动提取「给模型看的表结构」。

直接把 CREATE TABLE 丢给模型是不够的：模型不知道枚举值有哪些、日期是什么格式、
以及哪些字段能 join。这里额外补上：
  - 每张表的列名 / 类型 / 主键外键
  - 低基数列的真实取值（城市、状态、会员等级……）
  - 每个表 2 行样本数据
这些信息能显著减少「字段名编造」这类失败。
"""
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "data", "ecommerce.db")

_BUSINESS_RULES = """业务口径约定（写 SQL 时必须遵守）：
- GMV / 销售额 = SUM(orders.total_amount)，且只统计 status IN ('已完成','已发货') 的订单；
- 销售额按品类/商品统计时用 order_items.amount 求和，并 JOIN orders 过滤同样的状态；
- 客单价 = GMV / 订单数；
- 所有金额用 ROUND(x, 2)。
日期相关：`created_at` 是 YYYY-MM-DD 字符串，
  - 上个月 → strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now', '-1 month')
  - 最近 30 天 → created_at >= date('now', '-30 days')
  - 今年 → strftime('%Y', created_at) = strftime('%Y', 'now')
"""


def table_names(conn):
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def build_schema_text(db_path=DB_PATH, sample_rows=2):
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"数据库不存在，先运行 python db/seed.py（{db_path}）")
    conn = sqlite3.connect(db_path)
    try:
        tables = table_names(conn)
        blocks = []
        for t in tables:
            create_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone()[0]
            cols = [(r[1], r[2]) for r in conn.execute(f"PRAGMA table_info({t})")]
            lines = [create_sql.strip()]
            # 低基数列的取值
            for col, ctype in cols:
                try:
                    distinct = conn.execute(f"SELECT COUNT(DISTINCT {col}) FROM {t}").fetchone()[0]
                except sqlite3.Error:
                    continue
                if 0 < distinct <= 12:
                    vals = [str(r[0]) for r in conn.execute(
                        f"SELECT DISTINCT {col} FROM {t} WHERE {col} IS NOT NULL LIMIT 12")]
                    lines.append(f"-- {t}.{col} 取值: {', '.join(vals)}")
            rows = conn.execute(f"SELECT * FROM {t} LIMIT {sample_rows}").fetchall()
            if rows:
                lines.append("-- 样本: " + "; ".join(str(r) for r in rows))
            blocks.append("\n".join(lines))
        return "【表结构】\n" + "\n\n".join(blocks) + "\n\n" + _BUSINESS_RULES
    finally:
        conn.close()


if __name__ == "__main__":
    print(build_schema_text())
