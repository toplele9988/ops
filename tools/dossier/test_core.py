"""Synthetic acceptance of read-only intake and isolated namespace import."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from build import materialize, identifier
from import_candidate import import_candidate
from prepare import readonly, file_sha


def fixture():
    return {
        'supplier': [dict(supplier_id='S-A',name='Vendor Alpha',supplier_type='产品',
            categories_json=['fixture'],contacts_json=[],reliability_notes='synthetic',
            aliases_json=[],field_sources_json={})],
        'source': [dict(source_id='SRC-A',kind='message',external_key='account-A/message-1',
            content_sha256='a'*64,file_sha256=None,parent_source_id=None,locator='text',
            source_at=None,observed_at='2026-01-01',scope='supplier',direction='unknown',
            parse_state='read',extract_state='read',failure_reason=None,coverage_json={},private_ref=None)],
        'product': [dict(product_id='P-A',product_family='F-A',category='fixture',canonical_model='MODEL-1',
            display_name='Synthetic Fixture',variant_label='unknown config, source A',config_json={},
            config_signature='source-A-not-null-equality',evidence_state='unknown',field_sources_json={})],
        'supplier_product': [dict(supplier_id='S-A',product_id='P-A',model_aliases_json=['MODEL-1'],evidence_json=['SRC-A'])],
        'offer': [dict(offer_id='O-A',supplier_id='S-A',product_id='P-A',amount_decimal='12.345',
            amount_raw='12.345',currency=None,tax_basis='unknown',tax_rate_text=None,unit=None,
            price_kind='unit_price',quote_state='observed',moq_text=None,quantity_condition=None,
            lead_time_text=None,packing_json={},conditions_json={},quoted_on=None,
            quoted_on_basis='unknown',document_period=None,valid_until=None,supersedes_offer_id=None,
            source_id='SRC-A',locator='text',field_sources_json={},dedup_key='source-A-offer')],
    }


class CoreTests(unittest.TestCase):
    def test_readonly_observes_live_wal_and_refuses_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'input.sqlite3'
            writer = sqlite3.connect(p)
            writer.execute('pragma journal_mode=wal')
            writer.execute('create table facts(value text)')
            writer.execute("insert into facts values('committed-in-wal')")
            writer.commit()
            self.assertGreater(Path(str(p)+'-wal').stat().st_size, 0)
            before = file_sha(p)
            with readonly(p) as reader:
                self.assertEqual(reader.execute('select value from facts').fetchone()[0], 'committed-in-wal')
                with self.assertRaises(sqlite3.OperationalError):
                    reader.execute("insert into facts values('forbidden')")
            self.assertEqual(file_sha(p), before)
            writer.close()

    def test_dry_run_creates_nothing_and_import_replays_without_duplicates(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);candidate=materialize(root,fixture());target=root/'target.sqlite3'
            import_candidate(candidate,target)
            self.assertFalse(target.exists())
            first=import_candidate(candidate,target,True)
            second=import_candidate(candidate,target,True)
            self.assertEqual(first['inserted']['offer'],1)
            self.assertTrue(all(n==0 for n in second['inserted'].values()))
            with readonly(target) as db:
                row=db.execute('select amount_decimal,currency,moq_text,quoted_on from sd_offer').fetchone()
                self.assertEqual(tuple(row),('12.345',None,None,None))

    def test_conflict_rolls_back_prior_inserts_and_preserves_legacy(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);candidate=materialize(root,fixture());target=root/'target.sqlite3'
            db=sqlite3.connect(target);db.execute('create table legacy(value text)');db.execute("insert into legacy values('keep')");db.commit();db.close()
            import_candidate(candidate,target,True)
            db=sqlite3.connect(candidate)
            db.execute("insert into metadata values('new_before_conflict','must_rollback')")
            db.execute("update supplier set name='Conflict'");db.commit();db.close()
            before=file_sha(target)
            with self.assertRaisesRegex(ValueError,'immutable_key_conflict'):
                import_candidate(candidate,target,True)
            self.assertEqual(file_sha(target),before)
            with readonly(target) as db:
                self.assertEqual(db.execute('select value from legacy').fetchone()[0],'keep')
                self.assertEqual(db.execute("select count(*) from sd_metadata where key='new_before_conflict'").fetchone()[0],0)

    def test_source_accounts_and_unknown_configurations_do_not_collapse(self):
        self.assertNotEqual(identifier('SRC-',('account-A','same-message')),identifier('SRC-',('account-B','same-message')))
        self.assertNotEqual(identifier('PV-',('same-family','unknown-at-source-A')),identifier('PV-',('same-family','unknown-at-source-B')))

    def test_incompatible_target_schema_is_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);candidate=materialize(root,fixture());target=root/'target.sqlite3'
            db=sqlite3.connect(target);db.execute('create table sd_supplier(wrong text)');db.close();before=file_sha(target)
            with self.assertRaisesRegex(ValueError,'partial_sd_schema'):
                import_candidate(candidate,target,True)
            self.assertEqual(file_sha(target),before)


if __name__=='__main__':
    unittest.main()
