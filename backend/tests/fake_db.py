"""Minimal in-memory SQL surface for service-level tests.

It implements only the statement shapes used by ``ticket_import``; it is not a
general SQL engine, but it keeps the full transactional service flow testable
without a running MySQL.
"""

import re
from contextlib import contextmanager
from datetime import date


class FakeCursor:
    def __init__(self):
        self._result = []
        self.lastrowid = None
        self.tables = {
            "sample_state": [{"id": 1, "current_version": 1, "updated_at": None}],
            "products": [
                {"id": 1, "sku": "CF-001", "price": 29.0, "status": "active"},
                {"id": 2, "sku": "ML-001", "price": 12.5, "status": "active"},
                {"id": 3, "sku": "BK-002", "price": 22.0, "status": "active"},
            ],
            "stores": [
                {"id": 1, "code": "BJ-HS", "active": 1},
                {"id": 2, "code": "BJ-CY", "active": 1},
            ],
            "basket_orders": [
                {"id": 1, "external_id": "POS-3", "store_id": 2, "order_date": date(2026, 11, 20),
                 "total_amount": 41.5, "source_file": "f.csv", "status": "imported",
                 "version": 1, "change_seq": 1, "latest_batch_id": None, "voided_at": None},
                {"id": 2, "external_id": "POS-4", "store_id": 2, "order_date": date(2026, 11, 20),
                 "total_amount": 12.5, "source_file": "f.csv", "status": "imported",
                 "version": 1, "change_seq": 1, "latest_batch_id": None, "voided_at": None},
            ],
            "basket_items": [
                {"order_id": 1, "product_id": 1, "quantity": 1, "unit_price": 29.0},
                {"order_id": 2, "product_id": 2, "quantity": 1, "unit_price": 12.5},
            ],
            "association_rules": [
                {"id": 1, "status": "draft", "sample_version": 1, "invalidated_at": None,
                 "invalidate_reason": None, "support": 0.25, "confidence": 0.6, "lift": 1.8,
                 "store_consistent": 1, "source_run": "run-v1-2-80-250",
                 "published_support": None, "published_confidence": None,
                 "published_lift": None, "published_basket_count": None},
                {"id": 2, "status": "submitted", "sample_version": 1, "invalidated_at": None,
                 "invalidate_reason": None, "support": 0.25, "confidence": 0.6, "lift": 1.8,
                 "store_consistent": 1, "source_run": "run-v1-2-80-250",
                 "published_support": None, "published_confidence": None,
                 "published_lift": None, "published_basket_count": None},
                {"id": 3, "status": "published", "sample_version": 1, "invalidated_at": None,
                 "invalidate_reason": None, "support": 0.25, "confidence": 0.6, "lift": 1.8,
                 "store_consistent": 1, "source_run": "run-v1-2-80-250",
                 "published_support": 0.25, "published_confidence": 0.6,
                 "published_lift": 1.8, "published_basket_count": 2},
                {"id": 4, "status": "blocked", "sample_version": 1, "invalidated_at": None,
                 "invalidate_reason": None, "support": 0.04, "confidence": 0.18, "lift": 0.72,
                 "store_consistent": 1, "source_run": "run-v1-2-80-250",
                 "published_support": None, "published_confidence": None,
                 "published_lift": None, "published_basket_count": None},
            ],
            "import_batches": [],
            "basket_order_revisions": [],
            "rule_invalidations": [],
            "audit_logs": [],
        }
        self._sequences = {name: 0 for name in self.tables}

    def _set_result(self, rows):
        self._result = list(rows)

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)

    def executemany(self, sql, seq):
        for args in seq:
            self.execute(sql, args)

    def _insert(self, sql, args):
        match = re.match(r"INSERT INTO (\w+)\s*\(([^)]+)\)\s*VALUES", sql, re.S)
        table, columns = match.group(1), [c.strip() for c in match.group(2).split(",")]
        rows = self.tables[table]
        self._sequences[table] += 1
        row = {"id": self._sequences[table]}
        row.update(dict(zip(columns, args)))
        rows.append(row)
        self.lastrowid = row["id"]

    def execute(self, sql, args=()):
        s = " ".join(sql.split())
        if s.startswith("SELECT current_version FROM sample_state"):
            return self._set_result(self.tables["sample_state"])
        if s.startswith("SELECT id, new_version FROM import_batches"):
            return self._set_result([b for b in self.tables["import_batches"]
                                     if b["batch_no"] == args[0]])
        if s.startswith("SELECT id, sku, price FROM products"):
            return self._set_result(self.tables["products"])
        if s.startswith("SELECT id, code FROM stores"):
            return self._set_result(self.tables["stores"])
        if s.startswith("SELECT * FROM basket_orders WHERE external_id"):
            return self._set_result([o for o in self.tables["basket_orders"]
                                     if o["external_id"] == args[0]])
        if s.startswith("SELECT * FROM association_rules WHERE id"):
            return self._set_result([dict(r) for r in self.tables["association_rules"]
                                     if r["id"] == args[0]])
        if s.startswith("SELECT id, batch_no FROM import_batches WHERE new_version"):
            return self._set_result([b for b in self.tables.get("import_batches", [])
                                     if b["new_version"] == args[0]][-1:])
        if s.startswith("SELECT id, antecedent, consequent, sample_version, status FROM association_rules"):
            wanted = set(args[0])
            return self._set_result([dict(r) for r in self.tables["association_rules"]
                                     if r["status"] in wanted])
        if s.startswith("DELETE FROM basket_items"):
            self.tables["basket_items"] = [i for i in self.tables["basket_items"]
                                           if i["order_id"] != args[0]]
            return
        if s.startswith("UPDATE basket_orders SET status='voided'"):
            order = next(o for o in self.tables["basket_orders"] if o["id"] == args[-1])
            order.update(status="voided", version=order["version"] + 1, change_seq=args[0],
                         latest_batch_id=args[1], voided_at="UTC")
            return
        if s.startswith("UPDATE basket_orders SET store_id"):
            order = next(o for o in self.tables["basket_orders"] if o["id"] == args[-1])
            order.update(store_id=args[0], order_date=args[1], total_amount=args[2],
                         status="imported", version=order["version"] + 1, change_seq=args[3],
                         latest_batch_id=args[4], voided_at=None)
            return
        if s.startswith("UPDATE association_rules SET status='published'"):
            rule = next(r for r in self.tables["association_rules"] if r["id"] == args[-1])
            rule.update(status="published", published_support=rule.get("support", 0.25),
                        published_confidence=rule.get("confidence", 0.6),
                        published_lift=rule.get("lift", 1.8),
                        published_basket_count=args[0])
            return
        if s.startswith("UPDATE association_rules SET status='submitted'"):
            rule = next(r for r in self.tables["association_rules"] if r["id"] == args[0])
            rule.update(status="submitted")
            return
        if s.startswith("UPDATE association_rules SET status='invalidated'"):
            rule = next(r for r in self.tables["association_rules"] if r["id"] == args[-1])
            rule.update(status="invalidated", invalidated_at="UTC", invalidate_reason=args[0])
            return
        if s.startswith("UPDATE association_rules SET status"):
            rule = next(r for r in self.tables["association_rules"] if r["id"] == args[-1])
            rule.update(status=args[0], invalidated_at="UTC", invalidate_reason=args[1])
            return
        if s.startswith("UPDATE sample_state"):
            self.tables["sample_state"][0].update(current_version=args[0], updated_at="UTC")
            return
        if s.startswith("INSERT INTO"):
            return self._insert(s, args)
        raise AssertionError(f"unhandled SQL in fake cursor: {s}")


@contextmanager
def fake_transaction(cursor):
    yield cursor
