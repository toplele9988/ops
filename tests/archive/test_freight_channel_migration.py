from console.archive.store import Store, insert


def test_v9_preserves_existing_freight_rows_and_defaults_channel_to_forwarder(tmp_path):
    path = tmp_path / 'fictional-freight-channel.sqlite3'
    old = Store(path, target_version=8)
    with old.write() as db:
        forwarder = insert(db, 'parties', dict(kind='forwarder', name='虚构旧货代',
            created_at='2030-01-01T00:00:00+00:00', updated_at='2030-01-01T00:00:00+00:00'), 'fictional-owner')
        freight_id = insert(db, 'freight_quotes', dict(forwarder_id=forwarder, origin='虚构起运港',
            destination_country='ZZ', destination_detail=None, mode='air', service='虚构旧服务', unit='kg',
            price_fen=250, currency='CNY', min_charge_fen=1000, transit_days_min=3, transit_days_max=6,
            quoted_on='2029-12-01', valid_until='2030-02-01', source_ref='fictional-old-ref', notes=None,
            status='confirmed', created_at='2030-01-01T00:00:00+00:00',
            updated_at='2030-01-01T00:00:00+00:00'), 'fictional-owner')
        before = dict(db.execute('SELECT * FROM freight_quotes WHERE id=?', (freight_id,)).fetchone())

    upgraded = Store(path, target_version=9)
    upgraded.initialize()
    with upgraded.read() as db:
        after = dict(db.execute('SELECT * FROM freight_quotes WHERE id=?', (freight_id,)).fetchone())
        assert {key: value for key, value in after.items() if key != 'channel'} == before
        assert after['channel'] == 'forwarder'
        assert db.execute('SELECT max(version) FROM schema_migrations').fetchone()[0] == 9
    upgraded.initialize()
    with upgraded.read() as db:
        assert db.execute('SELECT channel FROM freight_quotes WHERE id=?', (freight_id,)).fetchone()[0] == 'forwarder'
