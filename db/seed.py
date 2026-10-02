# -*- coding: utf-8 -*-
"""生成示例电商库 data/ecommerce.db。

设计要点：
- 固定随机种子 → 任何人 clone 后得到完全一致的数据，评测可复现；
- 订单日期按「距今天数」生成 → 「上个月」「最近 30 天」这类相对时间问题永远有数据，
  不会因为仓库放久了而全部失效。
用法：python db/seed.py
"""
import datetime
import os
import random
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB_PATH = os.path.join(ROOT, "data", "ecommerce.db")

DDL = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY,
    name TEXT,
    city TEXT,
    level TEXT,
    registered_at TEXT
);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT,
    category_id INTEGER,
    price REAL
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER,
    status TEXT,
    created_at TEXT,
    total_amount REAL
);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER,
    product_id INTEGER,
    quantity INTEGER,
    unit_price REAL,
    amount REAL
);
"""

CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "西安"]
LEVELS = ["普通", "银卡", "金卡", "钻石"]
STATUSES = ["已完成", "已完成", "已完成", "已发货", "已发货", "待付款", "已取消"]

CATEGORY_PRODUCTS = {
    "手机数码": ["智能手机 Pro", "降噪蓝牙耳机", "智能手表 S2", "平板电脑 Air", "无线充电器"],
    "家用电器": ["变频滚筒洗衣机", "风冷十字冰箱", "扫地机器人 X9", "破壁料理机", "空气循环扇"],
    "服饰鞋包": ["轻薄羽绒服", "直筒牛仔裤", "商务通勤双肩包", "复古跑鞋", "纯棉白衬衫"],
    "食品生鲜": ["五常有机大米", "阿拉斯加蟹腿", "低温鲜牛奶", "现磨挂耳咖啡", "阳光玫瑰葡萄"],
    "图书文具": ["算法导论", "深度学习入门", "中性笔套装", "皮质笔记本", "人体工学笔筒"],
    "运动户外": ["碳纤维登山杖", "自动充气帐篷", "专业跑步腰包", "瑜伽垫 TPE", "折叠户外椅"],
}

PRICE_RANGE = {
    "手机数码": (299, 4999), "家用电器": (399, 8999), "服饰鞋包": (59, 1299),
    "食品生鲜": (25, 699), "图书文具": (15, 299), "运动户外": (49, 1999),
}


def seed(db_path=DB_PATH, seed=42, n_customers=120, n_orders=1500):
    rng = random.Random(seed)
    today = datetime.date.today()

    if os.path.exists(db_path):
        os.remove(db_path)
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(DDL)

    categories = list(CATEGORY_PRODUCTS.keys())
    conn.executemany("INSERT INTO categories VALUES (?,?)",
                     [(i + 1, n) for i, n in enumerate(categories)])

    products, pid = [], 0
    for ci, cat in enumerate(categories, 1):
        for name in CATEGORY_PRODUCTS[cat]:
            pid += 1
            lo, hi = PRICE_RANGE[cat]
            price = round(rng.uniform(lo, hi), 2)
            products.append((pid, name, ci, price))
    conn.executemany("INSERT INTO products VALUES (?,?,?,?)", products)

    customers = []
    for i in range(1, n_customers + 1):
        reg = today - datetime.timedelta(days=rng.randint(30, 900))
        customers.append((i, f"客户{i:03d}", rng.choice(CITIES), rng.choice(LEVELS), reg.isoformat()))
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", customers)

    orders, items, item_id = [], [], 0
    for oid in range(1, n_orders + 1):
        cid = rng.randint(1, n_customers)
        created = today - datetime.timedelta(days=rng.randint(0, 400))
        status = rng.choice(STATUSES)
        total = 0.0
        for _ in range(rng.randint(1, 3)):
            p = rng.choice(products)
            qty = rng.randint(1, 3)
            item_id += 1
            amount = round(p[3] * qty, 2)
            total += amount
            items.append((item_id, oid, p[0], qty, p[3], amount))
        orders.append((oid, cid, status, created.isoformat(), round(total, 2)))

    conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", orders)
    conn.executemany("INSERT INTO order_items VALUES (?,?,?,?,?,?)", items)
    conn.commit()

    # 顺手建个索引，体现一点工程常识
    conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_items_order ON order_items(order_id)")
    conn.commit()
    conn.close()

    print(f"已生成示例库 {db_path}")
    print(f"  customers={n_customers} categories={len(categories)} products={len(products)} "
          f"orders={len(orders)} order_items={len(items)}")
    print(f"  订单日期范围: {min(o[3] for o in orders)} ~ {max(o[3] for o in orders)}")
    return db_path


if __name__ == "__main__":
    seed()
