"""Record the channel for each freight quote; existing rates remain forwarder rates."""


def upgrade(db):
    columns = {row[1] for row in db.execute('PRAGMA table_xinfo(freight_quotes)')}
    if 'channel' not in columns:
        db.execute("ALTER TABLE freight_quotes ADD COLUMN channel TEXT NOT NULL DEFAULT 'forwarder' "
                   "CHECK(channel IN ('platform_alibaba','forwarder','other'))")


def migrate(db):
    from .store import migrate as migrate_store
    migrate_store(db, target_version=9)
