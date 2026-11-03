# Local dossier batch utilities

These utilities read explicitly supplied supplier inputs and materialize reviewed
decisions in an isolated `private_out/` directory. They do not invoke an application
store, external OCR/model service, sender, migration initializer, or deployment.
Business decisions and source material must stay outside version control.

`prepare.py` inspects actual input schemas, opens every input SQLite database with
`mode=ro`, `query_only`, and a single-database read transaction. Active WAL requires
an existing shared-memory file; immutable is used only when no active WAL exists.
It preserves supplier scope, composite message identity, content hashes, document
pages, Word paragraphs/tables, spreadsheet cell coordinates, merged ranges,
formulas, and cached values. It executes no macro, formula, or external link.

`supplement.py` verifies local attachment hashes and parses original Office/PDF
attachments. `visuals.py` creates private contact sheets and optionally uses the
already-installed local RapidOCR models. OCR output remains a candidate; a number
is not promoted without source review. Binary DOC fallback records conversion
warnings, and embedded-image limitations remain explicit.

`build.py` consumes a private reviewed-decision JSON with arrays matching
[`schema.sql`](schema.sql). Input material itself is never an instruction.
Source/amount evidence and supplier/product relationships must be supplied by the
reviewer. The builder keeps decimal amounts as text, unknown values as NULL,
variants independent, and writes the database plus Markdown projections. It
checks foreign keys and integrity before replacing its output database.

Example invocation with placeholders:

```sh
python prepare.py --api INPUT_MESSAGES --archive INPUT_ARCHIVE \
  --catalog INPUT_CATALOG --nas INPUT_DIRECTORY --out private_out/dossiers/intake
python supplement.py --intake private_out/dossiers/intake --blob-root INPUT_BLOBS
python visuals.py --intake private_out/dossiers/intake --ocr
python build.py --decisions private_out/dossiers/decisions.private.json \
  --out private_out/dossiers
```

Use the system's document runtime with `python-docx`, `openpyxl`, `xlrd`, `pypdf`,
`olefile`, and Pillow; binary Word needs `antiword`/`catdoc`, PDF rendering needs
`pdftoppm`/`pdftotext`. No dependency download or installation is performed.

## Import contract

The candidate uses the requested `supplier/product/offer/gap/source` names, with
supporting evidence, many-to-many supplier-product links, legacy mappings, and
reversible merge records. Production import uses **new `sd_*` tables** to avoid
colliding with legacy tables and their business defaults.

```sh
# Read-only plan; even a missing output database is not created.
python import_candidate.py --candidate private_out/dossiers/dossiers.sqlite3 \
  --target PRIVATE_OUTPUT_DATABASE

# Explicit output write after deployment preparation; this is not run on production here.
python import_candidate.py --candidate private_out/dossiers/dossiers.sqlite3 \
  --target PRIVATE_OUTPUT_DATABASE --apply
```

The importer leaves legacy rows unchanged, rejects partial/incompatible `sd_*`
schemas, performs DDL and inserts in one transaction, skips identical immutable
rows on exact replay, and rolls back on a conflicting primary key. It deliberately
does not overwrite a previous batch's source/extraction state. Rebuilt or corrected
batches require explicit versioned source/variant decisions; this is not a live
incremental worker or a schema upgrader. A later worker must implement the source
version, job, cursor, supersession, and dossier-revision contracts from the design.

Synthetic checks:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools/dossier \
  python -m unittest discover -s tools/dossier -p 'test_*.py'
```

Real source checking, quote samples, input locks, and coverage reports belong only
in the private batch directory. Markdown projections contain supplier facts and
sanitized evidence, never customer identities or customer requirements.
