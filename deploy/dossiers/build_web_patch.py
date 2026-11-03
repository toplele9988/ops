#!/usr/bin/env python3
"""Record this task's additive web delta and the tested static build hashes.

Shared files are reconstructed by removing ONLY the exact lines added here.
The baseline is the task's web snapshot, not a claim about deployed web sources.
"""
import difflib
import hashlib
from pathlib import Path

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[1]


def build():
    new=['web/src/dossiers.ts','web/src/pages/Dossiers.tsx','web/src/pages/Dossiers.css','web/src/pages/overview/SupplierGapCard.tsx']
    changed=['web/src/App.tsx','web/src/nav.ts','web/src/ops/queue.ts','web/src/pages/overview/TodayQueue.tsx','web/src/pages/Suppliers.tsx','web/src/pages/Products.tsx']
    patch=[];baseline=[];candidate=[]
    for name in sorted(new+changed):
        after=(REPO/name).read_text();before=after
        if name in new:before=''
        elif name=='web/src/App.tsx':
            before=''.join(line for line in after.splitlines(True) if './pages/Dossiers' not in line and not any('path="'+p in line for p in ['supplier-dossiers','product-dossiers','dossier-gaps']))
        elif name=='web/src/nav.ts':
            before=''.join(line for line in after.splitlines(True) if not any(p in line for p in ['"supplier-dossiers"','"product-dossiers"','"dossier-gaps"']))
        elif name=='web/src/ops/queue.ts':
            before=''.join(line for line in after.splitlines(True) if 'SupplierGapSummary' not in line and 'supplier_dossiers?' not in line)
        elif name=='web/src/pages/overview/TodayQueue.tsx':
            before=''.join(line for line in after.splitlines(True) if 'SupplierGapCard' not in line)
        elif name=='web/src/pages/Suppliers.tsx':
            before='import { PartiesWorkbench } from "../ui/PartiesWorkbench";\n\nexport function Suppliers() {\n  return <PartiesWorkbench initialKind="supplier" />;\n}\n'
        elif name=='web/src/pages/Products.tsx':
            before=''.join(line for line in after.splitlines(True) if 'import { Link }' not in line and '这里核准旧产品候选' not in line)
        digest=lambda s:hashlib.sha256(s.encode()).hexdigest()
        if name not in new:baseline.append(f'{digest(before)}  {name}\n')
        candidate.append(f'{digest(after)}  {name}\n')
        patch.extend(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='a/'+name if before else '/dev/null',tofile='b/'+name))
    (HERE/'web.patch').write_text(''.join(patch))
    (HERE/'web.baseline.sha256').write_text(''.join(baseline))
    (HERE/'web.candidate.sha256').write_text(''.join(candidate))
    dist=REPO/'web/dist'
    (HERE/'dist.sha256').write_text(''.join(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(dist)}\n' for p in sorted(dist.rglob('*')) if p.is_file()))


if __name__=='__main__':build()
