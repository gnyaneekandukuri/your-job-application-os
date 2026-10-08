"""Local, durable application history. No model calls or scan-data dependencies."""
from contextlib import contextmanager
import io
import base64
import json
import sqlite3
from datetime import date, datetime, timezone
from typing import Literal
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree.ElementTree import Element, SubElement, tostring

from pydantic import BaseModel, ConfigDict, Field
from .retrieval import RetrievalError

Status = Literal['Applied', 'Interviewing', 'Offer', 'Rejected', 'Withdrawn']

class ConfirmApplication(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str
    confirmed: Literal[True]
    applied_date: date
    notes: str = Field(default='', max_length=5000)

class UpdateApplication(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str
    status: Status
    follow_up_date: date | None = None
    notes: str = Field(default='', max_length=5000)

class Applications:
    def __init__(self, path):
        self.path = path
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS applications
                (id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)''')

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=15)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def list(self):
        with self.db() as db:
            rows = [json.loads(r[0]) for r in db.execute('SELECT payload FROM applications')]
        return sorted([self.summary(r) for r in rows], key=lambda r: (r['applied_date'], r['created_at']), reverse=True)

    @staticmethod
    def summary(record):
        return {k: v for k, v in record.items() if k not in ('job_snapshot', 'resume_snapshot', 'assessment_snapshot', 'resume_pdf')}

    def get(self, application_id):
        with self.db() as db:
            row = db.execute('SELECT payload FROM applications WHERE id=?', (application_id,)).fetchone()
        if not row:
            raise RetrievalError('Application not found.', 404)
        return json.loads(row[0])

    def confirm(self, job, tailoring, data, metadata=None):
        # Lock the resume while freezing it so concurrent edits cannot change the submitted copy.
        with tailoring.lock:
            exported = tailoring.exported(job, data.revision) if hasattr(tailoring,'exported') else None
            if exported:
                resume = exported['record']
            else:
                _, resume = tailoring.current(job, data.revision)
            if not exported and resume['state'] != 'APPROVED':
                raise RetrievalError('Approve the resume used for this application first.', 409)
            record = dict(id=str(uuid4()), job_id=job.id, revision=str(uuid4()),
                company=job.company, title=job.title, url=str(job.source_url or ''),
                location=(metadata or {}).get('location', ''), boards=(metadata or {}).get('boards', []),
                applied_date=data.applied_date.isoformat(), status='Applied', follow_up_date=None,
                notes=data.notes, created_at=datetime.now(timezone.utc).isoformat(),
                resume_revision=resume['revision'], framework=resume['framework'],
                resume_pdf=exported['pdf'] if exported else None, has_pdf=bool(exported),
                job_snapshot=job.model_dump(mode='json'), resume_snapshot=resume,
                assessment_snapshot={k: v for k, v in resume.get('context', {}).items() if k.startswith('assessment_')})
            payload = json.dumps(record)
            with self.db() as db:
                db.execute('INSERT OR IGNORE INTO applications VALUES (?,?,?)', (record['id'], job.id, payload))
                saved = json.loads(db.execute('SELECT payload FROM applications WHERE job_id=?', (job.id,)).fetchone()[0])
            # Repeat confirmations never overwrite the originally submitted resume.
            return self.summary(saved)

    def update(self, application_id, data):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM applications WHERE id=?', (application_id,)).fetchone()
            if not row:
                raise RetrievalError('Application not found.', 404)
            record = json.loads(row[0])
            if record['revision'] != data.revision:
                raise RetrievalError('This application changed. Reload before saving.', 409)
            record.update(status=data.status, notes=data.notes,
                follow_up_date=data.follow_up_date.isoformat() if data.follow_up_date else None,
                revision=str(uuid4()))
            db.execute('UPDATE applications SET payload=? WHERE id=?', (json.dumps(record), application_id))
        return self.summary(record)

    def resume(self, application_id):
        record = self.get(application_id)
        return '\n\n'.join(('• ' if b['kind'] == 'bullet' else '') + b['text']
            for b in record['resume_snapshot']['draft']['blocks']) + '\n'

    def pdf(self, application_id):
        record = self.get(application_id)
        if not record.get('resume_pdf'):
            raise RetrievalError('This older application has a text resume only.',404)
        return base64.b64decode(record['resume_pdf'])

    def export(self):
        # Minimal portable Office Open XML workbook. Inline strings cannot execute formulas.
        ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
        root = Element('worksheet', xmlns=ns)
        views = SubElement(root, 'sheetViews')
        SubElement(SubElement(views, 'sheetView', workbookViewId='0'), 'pane',
                   ySplit='1', topLeftCell='A2', activePane='bottomLeft', state='frozen')
        cols = SubElement(root, 'cols')
        for i, width in enumerate([25, 38, 16, 18, 25, 50, 40, 16, 60, 22], 1):
            SubElement(cols, 'col', min=str(i), max=str(i), width=str(width), customWidth='1')
        sheet = SubElement(root, 'sheetData')
        rows = [['Company', 'Role', 'Applied date', 'Status', 'Location', 'Job link', 'Resume version', 'Follow-up date', 'Notes', 'Job board']]
        rows += [[r['company'], r['title'], r['applied_date'], r['status'], r['location'], r['url'],
                  r['resume_revision'], r['follow_up_date'] or '', r['notes'], ', '.join(r['boards'])] for r in self.list()]
        for n, values in enumerate(rows, 1):
            row = SubElement(sheet, 'row', r=str(n))
            for col, value in enumerate(values):
                cell = SubElement(row, 'c', r=f'{chr(65+col)}{n}', t='inlineStr')
                # Strip XML 1.0 forbidden controls; keep tabs/newlines and Unicode.
                value = ''.join(c for c in str(value) if c in '\t\n\r' or 32 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF)
                SubElement(SubElement(cell, 'is'), 't', {'xml:space': 'preserve'}).text = value
        SubElement(root, 'autoFilter', ref=f'A1:J{len(rows)}')
        output = io.BytesIO()
        with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
            archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
            archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            archive.writestr('xl/workbook.xml', f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Applied jobs" sheetId="1" r:id="rId1"/></sheets></workbook>')
            archive.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
            archive.writestr('xl/worksheets/sheet1.xml', tostring(root, encoding='utf-8', xml_declaration=True))
        return output.getvalue()
