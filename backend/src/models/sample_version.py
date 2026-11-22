"""Versioned POS ticket ingestion and rule sample evidence.

The schema is additive: fresh databases create every table directly, while
``src.db.ensure_columns`` back-fills the extra columns and indexes onto
databases created by earlier snapshots.
"""

SAMPLE_VERSION_SCHEMA = [
    # One-row monotonic counter. Every accepted correction/revocation bumps the
    # version so rule runs, candidates and published snapshots can pin the
    # exact sample they were derived from.
    """CREATE TABLE IF NOT EXISTS sample_state (
        id TINYINT PRIMARY KEY DEFAULT 1,
        current_version INT NOT NULL DEFAULT 0,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CHECK (id = 1)
    )""",
    """CREATE TABLE IF NOT EXISTS import_batches (
        id INT AUTO_INCREMENT PRIMARY KEY,
        batch_no VARCHAR(40) UNIQUE NOT NULL,
        source_file VARCHAR(120) NOT NULL,
        op ENUM('create','correct','void') NOT NULL,
        ticket_count INT NOT NULL,
        base_version INT NOT NULL,
        new_version INT NOT NULL,
        created_by INT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        note VARCHAR(240) NULL,
        FOREIGN KEY (created_by) REFERENCES users(id),
        CHECK (ticket_count BETWEEN 1 AND 1000),
        CHECK (new_version > base_version)
    )""",
    """CREATE TABLE IF NOT EXISTS basket_order_revisions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        order_id INT NOT NULL,
        revision_seq INT NOT NULL,
        op ENUM('create','correct','void') NOT NULL,
        batch_id INT NOT NULL,
        external_id VARCHAR(60) NOT NULL,
        store_id INT NULL,
        order_date DATE NULL,
        total_amount DECIMAL(10,2) NULL,
        snapshot_json MEDIUMTEXT NULL,
        created_by INT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (order_id) REFERENCES basket_orders(id),
        FOREIGN KEY (batch_id) REFERENCES import_batches(id),
        FOREIGN KEY (created_by) REFERENCES users(id),
        UNIQUE KEY uq_order_revision (order_id, revision_seq)
    )""",
    """CREATE TABLE IF NOT EXISTS rule_basket_evidence (
        id INT AUTO_INCREMENT PRIMARY KEY,
        rule_id INT NOT NULL,
        analysis_run_id INT NOT NULL,
        sample_version INT NOT NULL,
        order_id INT NOT NULL,
        external_id VARCHAR(60) NOT NULL,
        store_code VARCHAR(20) NOT NULL,
        basket_families VARCHAR(400) NOT NULL,
        included TINYINT NOT NULL DEFAULT 1,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (rule_id) REFERENCES association_rules(id),
        FOREIGN KEY (analysis_run_id) REFERENCES analysis_runs(id),
        FOREIGN KEY (order_id) REFERENCES basket_orders(id),
        KEY ix_evidence_rule (rule_id, sample_version)
    )""",
    """CREATE TABLE IF NOT EXISTS rule_invalidations (
        id INT AUTO_INCREMENT PRIMARY KEY,
        rule_id INT NOT NULL,
        sample_version_before INT NOT NULL,
        sample_version_after INT NOT NULL,
        trigger_batch_id INT NOT NULL,
        trigger_ticket VARCHAR(60) NOT NULL,
        reason VARCHAR(240) NOT NULL,
        invalidated_by INT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (rule_id) REFERENCES association_rules(id),
        FOREIGN KEY (trigger_batch_id) REFERENCES import_batches(id),
        FOREIGN KEY (invalidated_by) REFERENCES users(id),
        KEY ix_invalidation_rule (rule_id)
    )""",
]

# Columns added to tables created by the original snapshot.
COLUMN_PATCHES = {
    "basket_orders": [
        ("version", "INT NOT NULL DEFAULT 1"),
        ("change_seq", "INT NOT NULL DEFAULT 0"),
        ("latest_batch_id", "INT NULL"),
        ("voided_at", "DATETIME NULL"),
    ],
    "association_rules": [
        ("sample_version", "INT NOT NULL DEFAULT 0"),
        ("invalidated_at", "DATETIME NULL"),
        ("invalidate_reason", "VARCHAR(240) NULL"),
        ("published_support", "DECIMAL(8,4) NULL"),
        ("published_confidence", "DECIMAL(8,4) NULL"),
        ("published_lift", "DECIMAL(8,4) NULL"),
        ("published_basket_count", "INT NULL"),
    ],
    "analysis_runs": [
        ("sample_version", "INT NOT NULL DEFAULT 0"),
    ],
}
