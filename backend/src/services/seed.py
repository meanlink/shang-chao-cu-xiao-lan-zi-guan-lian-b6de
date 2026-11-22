from datetime import date
import json
from ..db import execute, fetch_one, fetch_all, transaction
from ..security import password_hash


def seed_demo():
    users = [
        ("ops@linli.local", "林晨 / 运营分析", "analyst"),
        ("manager@linli.local", "周宁 / 促销经理", "manager"),
        ("store@linli.local", "陈杰 / 门店执行", "store_operator"),
        ("store-east@linli.local", "徐琳 / 门店执行", "store_operator"),
    ]
    for email, name, role in users:
        if not fetch_one("SELECT id FROM users WHERE email=%s", (email,)):
            execute("INSERT INTO users(email,display_name,role,password_hash) VALUES(%s,%s,%s,%s)",
                    (email, name, role, password_hash("basket123")))
    stores = [("BJ-HS", "海淀星河店", "华北"), ("BJ-CY", "朝阳望京店", "华北"), ("SH-XH", "徐汇滨江店", "华东")]
    for code, name, region in stores:
        if not fetch_one("SELECT id FROM stores WHERE code=%s", (code,)):
            execute("INSERT INTO stores(code,name,region) VALUES(%s,%s,%s)", (code, name, region))
    families = [
        ("咖啡与冲调", "饮品", "咖啡豆、挂耳与即饮咖啡"),
        ("乳品", "食品", "牛奶、酸奶与植物奶"),
        ("早餐烘焙", "烘焙", "面包、麦片与早餐饼"),
        ("清洁纸品", "家清", "抽纸、湿巾与厨房纸"),
        ("零食坚果", "零食", "坚果、饼干与巧克力"),
    ]
    for name, cat, desc in families:
        if not fetch_one("SELECT id FROM product_families WHERE name=%s", (name,)):
            execute("INSERT INTO product_families(name,category,description) VALUES(%s,%s,%s)",
                    (name, cat, desc))
    family_ids = {r["name"]: r["id"] for r in fetch_all("SELECT id,name FROM product_families")}
    products = [
        ("CF-001", "山野拼配咖啡豆", family_ids["咖啡与冲调"], 59.0),
        ("CF-002", "冷萃挂耳咖啡", family_ids["咖啡与冲调"], 29.0),
        ("ML-001", "高钙纯牛奶 1L", family_ids["乳品"], 12.5),
        ("ML-002", "燕麦植物奶 1L", family_ids["乳品"], 18.9),
        ("BK-001", "全麦吐司", family_ids["早餐烘焙"], 15.8),
        ("BK-002", "燕麦早餐饼", family_ids["早餐烘焙"], 22.0),
        ("CL-001", "厨房抽纸 3层", family_ids["清洁纸品"], 18.5),
        ("SN-001", "混合坚果 250g", family_ids["零食坚果"], 36.0),
    ]
    for sku, name, fid, price in products:
        if not fetch_one("SELECT id FROM products WHERE sku=%s", (sku,)):
            execute("INSERT INTO products(sku,name,family_id,price) VALUES(%s,%s,%s,%s)",
                    (sku, name, fid, price))
    product_ids = {r["sku"]: r["id"] for r in fetch_all("SELECT id,sku FROM products")}
    store_ids = {r["code"]: r["id"] for r in fetch_all("SELECT id,code FROM stores")}
    analyst = fetch_one("SELECT id FROM users WHERE email='ops@linli.local'")["id"]

    # Baseline import batch so every seeded ticket already has a provenance row.
    with transaction() as cur:
        cur.execute("SELECT current_version FROM sample_state WHERE id=1 FOR UPDATE")
        sample_version = int(cur.fetchone()["current_version"])
        cur.execute("SELECT id FROM import_batches WHERE batch_no=%s", ("seed-20261120",))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO import_batches(batch_no,source_file,op,ticket_count,base_version,"
                "new_version,created_by,note) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                ("seed-20261120", "orders_2026-11-20.csv", "create", 12,
                 sample_version, sample_version + 1, analyst, "初始 POS 日结批次"))
            batch_id = cur.lastrowid
            cur.execute("UPDATE sample_state SET current_version=current_version+1,"
                        "updated_at=UTC_TIMESTAMP() WHERE id=1")
            sample_version += 1
        else:
            batch_id = fetch_one("SELECT id FROM import_batches WHERE batch_no=%s",
                                 ("seed-20261120",))["id"]
        baskets = [
            ("POS-1120-0001", "BJ-HS", ["CF-001", "ML-001", "BK-001"]),
            ("POS-1120-0002", "BJ-HS", ["CF-002", "ML-001"]),
            ("POS-1120-0003", "BJ-CY", ["CF-001", "ML-001", "BK-002"]),
            ("POS-1120-0004", "BJ-CY", ["CF-001", "ML-001"]),
            ("POS-1120-0005", "SH-XH", ["CF-002", "ML-002", "BK-002"]),
            ("POS-1120-0006", "SH-XH", ["CF-001", "ML-001", "BK-001"]),
            ("POS-1120-0007", "BJ-HS", ["CL-001", "SN-001"]),
            ("POS-1120-0008", "BJ-CY", ["CL-001"]),
            ("POS-1120-0009", "SH-XH", ["SN-001", "BK-002"]),
            ("POS-1120-0010", "BJ-HS", ["CF-001", "ML-001"]),
            ("POS-1120-0011", "BJ-CY", ["CF-001", "ML-001", "BK-001"]),
            ("POS-1120-0012", "SH-XH", ["CF-001", "ML-001"]),
        ]
        price_rows = {r["sku"]: float(r["price"]) for r in fetch_all("SELECT sku,price FROM products")}
        family_name = {r["id"]: r["name"] for r in fetch_all("SELECT id,name FROM product_families")}
        sku_family = {r["sku"]: family_name[r["family_id"]]
                      for r in fetch_all("SELECT sku,family_id FROM products")}
        inserted_orders = []
        for external, store, skus in baskets:
            cur.execute("SELECT id FROM basket_orders WHERE external_id=%s", (external,))
            row = cur.fetchone()
            if row:
                inserted_orders.append((row["id"], external, store, skus))
                continue
            total = round(sum(price_rows[s] for s in skus), 2)
            cur.execute(
                "INSERT INTO basket_orders(external_id,store_id,order_date,total_amount,source_file,"
                "status,version,change_seq,latest_batch_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (external, store_ids[store], date(2026, 11, 20), total,
                 "orders_2026-11-20.csv", "imported", 1, 1, batch_id))
            oid = cur.lastrowid
            snapshot_lines = [{"sku": sku, "quantity": 1, "unit_price": price_rows[sku]}
                              for sku in skus]
            for sku in skus:
                cur.execute(
                    "INSERT INTO basket_items(order_id,product_id,quantity,unit_price) "
                    "VALUES(%s,%s,1,%s)", (oid, product_ids[sku], price_rows[sku]))
            cur.execute(
                "INSERT INTO basket_order_revisions(order_id,revision_seq,op,batch_id,external_id,"
                "store_id,order_date,total_amount,snapshot_json,created_by) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (oid, 1, "create", batch_id, external, store_ids[store], date(2026, 11, 20),
                 total, json.dumps({"lines": snapshot_lines}, ensure_ascii=False), analyst))
            inserted_orders.append((oid, external, store, skus))

        cur.execute("SELECT id FROM analysis_runs WHERE run_key=%s", ("seed-20261120",))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO analysis_runs(run_key,min_support,min_confidence,basket_count,rule_count,"
                "status,created_by,sample_version) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                ("seed-20261120", 0.08, 0.25, 12, 3, "completed", analyst, sample_version))
            run_id = cur.lastrowid
        else:
            run_id = fetch_one("SELECT id FROM analysis_runs WHERE run_key=%s",
                               ("seed-20261120",))["id"]
        seeded_rules = [
            ("咖啡与冲调 + 乳品", "早餐烘焙", 0.25, 0.60, 1.80, "全门店", 1, "submitted"),
            ("清洁纸品", "零食坚果", 0.04, 0.18, 0.72, "BJ-HS / BJ-CY / SH-XH", 1, "blocked"),
            ("乳品", "清洁纸品", 0.17, 0.42, 1.26, "BJ-HS / BJ-CY / SH-XH", 0, "blocked"),
        ]
        for antecedent, consequent, support, confidence, lift, scope, consistent, status in seeded_rules:
            cur.execute(
                "SELECT id FROM association_rules WHERE antecedent=%s AND consequent=%s",
                (antecedent, consequent))
            row = cur.fetchone()
            if row:
                continue
            cur.execute(
                "INSERT INTO association_rules(antecedent,consequent,support,confidence,lift,"
                "store_scope,store_consistent,status,source_run,created_by,sample_version) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (antecedent, consequent, support, confidence, lift, scope, consistent, status,
                 "seed-20261120", analyst, sample_version))
            rule_id = cur.lastrowid
            needed = set(antecedent.split(" + ")) | set(consequent.split(" + "))
            for oid, external, store, skus in inserted_orders:
                basket_families = sorted({sku_family[s] for s in skus})
                if needed.issubset(set(basket_families)):
                    cur.execute(
                        "INSERT INTO rule_basket_evidence(rule_id,analysis_run_id,sample_version,"
                        "order_id,external_id,store_code,basket_families,included) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,1)",
                        (rule_id, run_id, sample_version, oid, external, store,
                         " + ".join(basket_families)))
