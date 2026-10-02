# -*- coding: utf-8 -*-
"""规则基线 SQL 生成器（对照组）。

存在的意义：评测时如果只报「LLM 方案准确率 80%」，这个数字没有参照。
规则基线是一个刻意保持简单的参照物——按关键词拼 SQL，不懂同义改写，也不会多表推理。
把它的分数摆在一起，才能说清 LLM 方案到底带来了多少增量。
"""
import re

CITY_MAP = {c: c for c in ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "西安"]}
LEVELS = ["普通", "银卡", "金卡", "钻石"]
STATUS_OK = "o.status IN ('已完成','已发货')"


def _find_city(q):
    for c in CITY_MAP:
        if c in q:
            return c
    return None


def _find_level(q):
    for lv in LEVELS:
        if lv in q:
            return lv
    return None


def _agg(q):
    if any(k in q for k in ("销售额", "营业额", "GMV", "销售金额", "多少钱")):
        return "SUM"
    if any(k in q for k in ("平均", "客单价")):
        return "AVG"
    if any(k in q for k in ("最多", "最高", "最低", "最大", "多少个", "几个")):
        return "COUNT"
    return "COUNT"


def _time_filter(q):
    if "上个月" in q or "上月" in q:
        return "strftime('%Y-%m', o.created_at) = strftime('%Y-%m','now','-1 month')"
    if "最近30天" in q or "最近 30 天" in q or "近30天" in q:
        return "o.created_at >= date('now','-30 days')"
    if "今年" in q or "本年度" in q:
        return "strftime('%Y', o.created_at) = strftime('%Y','now')"
    return None


def generate(question):
    """把中文问题拼成一条 SQL。拼不出来返回 None（评测算失败）。"""
    q = question.strip()
    agg = _agg(q)
    city = _find_city(q)
    level = _find_level(q)
    tf = _time_filter(q)
    m = re.search(r"(?:前|top|Top|TOP)\s*(\d+)", q)
    limit = int(m.group(1)) if m else None

    # 分组维度
    if any(k in q for k in ("每个城市", "各城市", "各城市", "城市维度")):
        if agg == "COUNT":
            return ("SELECT c.city, COUNT(*) AS cnt FROM orders o "
                    "JOIN customers c ON o.customer_id=c.id GROUP BY c.city ORDER BY cnt DESC")
        return ("SELECT c.city, ROUND(SUM(o.total_amount),2) AS amount FROM orders o "
                "JOIN customers c ON o.customer_id=c.id "
                f"WHERE {STATUS_OK} GROUP BY c.city ORDER BY amount DESC")

    if any(k in q for k in ("每个品类", "各品类", "品类", "类别")):
        return ("SELECT cat.name, ROUND(SUM(oi.amount),2) AS amount FROM order_items oi "
                "JOIN orders o ON o.id=oi.order_id "
                "JOIN products p ON p.id=oi.product_id "
                "JOIN categories cat ON cat.id=p.category_id "
                f"WHERE {STATUS_OK} GROUP BY cat.id ORDER BY amount DESC"
                + (f" LIMIT {limit}" if limit else ""))

    if any(k in q for k in ("每个月", "月度", "按月")):
        return ("SELECT strftime('%Y-%m', o.created_at) AS ym, ROUND(SUM(o.total_amount),2) AS amount "
                f"FROM orders o WHERE {STATUS_OK} GROUP BY ym ORDER BY ym")

    if city:
        return (f"SELECT ROUND(SUM(o.total_amount),2) AS amount FROM orders o "
                f"JOIN customers c ON o.customer_id=c.id WHERE c.city='{city}' AND {STATUS_OK}")

    if level and "客户" in q:
        return f"SELECT COUNT(*) AS cnt FROM customers WHERE level='{level}'"

    where = [tf] if tf else []
    if any(k in q for k in ("订单",)) and agg == "COUNT":
        where.append(STATUS_OK if not any(k in q for k in ("所有", "全部")) else None)
        cond = " WHERE " + " AND ".join([w for w in where if w]) if any(where) else ""
        return f"SELECT COUNT(*) AS cnt FROM orders o{cond}"

    if agg == "SUM":
        where.append(STATUS_OK)
        cond = " WHERE " + " AND ".join([w for w in where if w])
        return f"SELECT ROUND(SUM(o.total_amount),2) AS amount FROM orders o{cond}"

    if "价格" in q and ("最贵" in q or "最高" in q):
        return "SELECT MAX(price) AS max_price FROM products"

    return None
