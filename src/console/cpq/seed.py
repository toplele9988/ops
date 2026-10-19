"""Idempotent work-database seed import."""
import argparse
from datetime import date
import json
import os
from pathlib import Path
import sqlite3
import sys
import yaml
from .gates import _unpriced
from .store import CPQStore, lane_from_dict, num

ROOT = Path(__file__).resolve().parents[2]


def load_rows(path):
    with Path(path).open(encoding='utf-8') as stream:
        rows = yaml.safe_load(stream)
    if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):
        raise ValueError('YAML list of mappings required')
    return rows


def seed_work_db(work_db, *, freight_path=None, accessories_path=None, fx=None, as_of=None):
    if (fx is None)!=(as_of is None):
        raise ValueError('--fx and --as-of are required together')
    if fx is not None:
        num(fx, True)
        as_of = date.fromisoformat(as_of) if isinstance(as_of,str) else as_of
        if type(as_of) is not date:
            raise ValueError('as_of must be a date')
    lanes = [lane_from_dict(r) for r in load_rows(freight_path or ROOT/'data/cpq/freight_lanes.yaml')]
    accessories = load_rows(accessories_path or ROOT/'data/cpq/accessories.yaml')
    if len({l.lane_id for l in lanes})!=len(lanes):
        raise ValueError('duplicate lane_id')
    seen = set()
    for row in accessories:
        if any(not isinstance(row.get(k),str) or not row[k].strip() for k in ('accessory_id','name_en')) or row['accessory_id'] in seen:
            raise ValueError('invalid or duplicate accessory')
        num(row.get('cost_cny'))
        active = row.get('active',True)
        if type(active) not in (int,bool) or active not in (0,1):
            raise ValueError('invalid accessory active flag')
        seen.add(row['accessory_id'])
    store = CPQStore(None,work_db)
    store.initialize()
    warnings = []
    for lane in lanes:
        store.put_lane(lane)
        if _unpriced(lane):
            warnings.append(lane.lane_id)
            print(f'WARNING: {lane.lane_id}: all freight charges are zero; 上线前必须填入真实货代报价',file=sys.stderr)
    for accessory in accessories:
        store.put_accessory(accessory)
    if fx is not None:
        store.add_fx(fx,as_of,'cpq_seed')
    return {'lanes':len(lanes),'accessories':len(accessories),'fx_seeded':fx is not None,'unpriced_lanes':warnings}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-db',default=os.environ.get('CPQ_WORK_DB','/var/lib/imgw/console/cpq_work.sqlite3'))
    parser.add_argument('--freight-lanes',type=Path)
    parser.add_argument('--accessories',type=Path)
    parser.add_argument('--fx',type=float)
    parser.add_argument('--as-of',type=date.fromisoformat)
    args = parser.parse_args(argv)
    try:
        result = seed_work_db(args.work_db,freight_path=args.freight_lanes,accessories_path=args.accessories,fx=args.fx,as_of=args.as_of)
    except (ValueError,OSError,sqlite3.Error,yaml.YAMLError) as exc:
        parser.error(str(exc))
    print(json.dumps(result,ensure_ascii=False))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
