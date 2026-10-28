"""Render saved resume blocks only; no evidence annotations or model calls."""
import json
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from threading import Lock
from xml.sax.saxutils import escape

from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, HRFlowable
import reportlab

_lock = Lock()
LAYOUT_VERSION = 'compact-letter-v3'
BULLET_INDENT, BULLET_HANGING = 36, 18

def font_name(fmt):
    family = fmt.get('font_family', 'Arial')
    stem = {'Arial':'arial', 'Calibri':'calibri', 'Times New Roman':'times', 'serif':'times'}.get(family, 'arial')
    # Windows names Calibri's bold italic calibriz.ttf; Arial and Times use the "bi" suffix.
    suffix = ('z' if stem == 'calibri' else 'bi') if fmt.get('bold') and fmt.get('italic') else 'bd' if fmt.get('bold') else 'i' if fmt.get('italic') else ''
    if stem == 'calibri' and suffix == 'bd': suffix = 'b'
    # Use the same installed fonts as the editor on Windows. Portable fallback is bundled with ReportLab.
    path = Path('C:/Windows/Fonts') / (stem + suffix + '.ttf')
    if not path.is_file():
        fallback = 'VeraBI' if suffix == 'bi' else 'VeraBd' if suffix == 'bd' else 'VeraIt' if suffix == 'i' else 'Vera'
        path = Path(reportlab.__file__).parent / 'fonts' / (fallback + '.ttf')
    name = 'Resume-' + path.stem
    with _lock:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(path)))
    return name

def page_layout(blocks):
    """Page size, margins and line spacing read from an uploaded Word master, when present."""
    return next(((b.get('format') or {}).get('page') for b in blocks if (b.get('format') or {}).get('page')), None) or {}


def _render_resume(blocks, compact=False, scale=1):
    output = BytesIO()
    layout = page_layout(blocks)
    width, height = layout.get('width') or 612, layout.get('height') or 792
    if layout.get('margins'):
        top, right_margin, bottom, left_margin = [m*(.85 if compact else 1) for m in layout['margins']]
    else:
        left_margin = right_margin = 30 if compact else 36
        top = bottom = 24 if compact else 30
    # Word's single spacing for common resume fonts is about 1.15 times the font size.
    line = 1.15*layout['line_spacing'] if layout.get('line_spacing') else 1.2
    if compact: line = min(line, 1.08)
    document = SimpleDocTemplate(output, pagesize=(width,height), leftMargin=left_margin, rightMargin=right_margin,
                                 topMargin=top, bottomMargin=bottom, title='Resume', author='')
    usable = width-left_margin-right_margin
    flow = []
    for index, block in enumerate(blocks):
        kind = block['kind']
        fmt = block.get('format') or {}
        original_size = fmt.get('font_size',10)
        size = max(min(original_size,9), original_size*scale)
        font = font_name(fmt)
        spacing = lambda key, default: default if fmt.get(key) is None else fmt[key]*(.6 if compact else 1)
        style = ParagraphStyle('resume', fontName=font, fontSize=size, leading=size*line,
            alignment={'left':TA_LEFT,'center':TA_CENTER,'right':TA_RIGHT}.get(fmt.get('align','left'), TA_LEFT),
            spaceBefore=spacing('space_before', (5 if compact else 8) if kind=='heading' else (2 if compact else 4) if kind=='role' else 0),
            spaceAfter=spacing('space_after', (2 if compact else 4) if kind=='heading' else (1 if compact else 2) if kind=='role' else (1 if compact else 3)),
            keepWithNext=kind in ('heading','role'), splitLongWords=True)
        rule_position = (fmt.get('rule') or 'above') if kind=='heading' else 'none'
        if rule_position=='above':
            rule=HRFlowable(width='100%',thickness=.7,color='#222222',spaceBefore=5 if compact else 8,spaceAfter=2 if compact else 3)
            rule.keepWithNext=True
            flow.append(rule)
            style.spaceBefore=0
        def markup(text, run_fmt=None, lead=None):
            run_font = font_name(run_fmt) if run_fmt else font
            if lead and text.startswith(lead['text']):
                lead_font = font_name({**fmt, 'bold':lead.get('bold'), 'italic':lead.get('italic')})
                return '<font name="%s">%s</font>%s' % (lead_font, escape(lead['text']), markup(text[len(lead['text']):], run_fmt))
            body = escape(text).replace('\n','<br/>').replace('  ',' &nbsp;')
            return '<font name="%s">%s</font>' % (run_font, body) if run_fmt else body
        parts=block['text'].split('\t')
        lead=fmt.get('lead')
        if len(parts)>1:
            right_fmt={**fmt,'bold':fmt.get('right_bold',fmt.get('bold')) if fmt.get('right_bold') is not None else fmt.get('bold'),
                       'italic':fmt.get('right_italic') if fmt.get('right_italic') is not None else fmt.get('italic')}
            right_style=ParagraphStyle('right',parent=style,alignment=TA_RIGHT,fontName=font_name(right_fmt))
            right=' '.join(parts[1:])
            # Reserve enough room for dates without colliding with long employer names.
            right_width=min(220,max(125,pdfmetrics.stringWidth(right,font_name(right_fmt),size)+6))
            row=Table([[Paragraph(markup(parts[0],lead=lead),style),Paragraph(escape(right),right_style)]],colWidths=[usable-12-right_width,right_width])
            row.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))
            row.keepWithNext=style.keepWithNext
            row.spaceBefore=style.spaceBefore;row.spaceAfter=style.spaceAfter
            flow.append(row)
        elif kind=='bullet':
            # The master's own indent, else Word's default bullet: text at 0.5 in, bullet at 0.25 in.
            indent=fmt.get('indent') or BULLET_INDENT
            style.leftIndent=indent;style.firstLineIndent=0;style.bulletIndent=max(0,indent-(fmt.get('hanging') if fmt.get('hanging') is not None else BULLET_HANGING))
            style.bulletFontName=font_name({**fmt,'bold':False,'italic':False});style.bulletFontSize=size
            flow.append(Paragraph(markup(block['text'],lead=lead),style,bulletText='•'))
        else:
            flow.append(Paragraph(markup(block['text'],lead=lead),style))
        if rule_position=='below':
            rule=HRFlowable(width='100%',thickness=.5,color='#808080',spaceBefore=1,spaceAfter=3 if compact else 4)
            flow[-1].keepWithNext=True
            rule.keepWithNext=True
            flow.append(rule)
    document.build(flow)
    return output.getvalue()


MAX_PAGES = 2


def render_resume(blocks):
    """Keep the resume within two pages without dropping text; never force unreadably tiny type."""
    from pypdf import PdfReader
    original = _render_resume(blocks)
    if len(PdfReader(BytesIO(original)).pages) <= MAX_PAGES:
        return original
    for scale in (1, .95, .9):
        compact = _render_resume(blocks, compact=True, scale=scale)
        if len(PdfReader(BytesIO(compact)).pages) <= MAX_PAGES:
            return compact
    # Long documents remain complete instead of being clipped or silently cut.
    return original


@lru_cache(maxsize=64)
def _page_count(serialized):
    from pypdf import PdfReader
    return len(PdfReader(BytesIO(render_resume(json.loads(serialized)))).pages)


def page_count(blocks):
    """Pages in the exported PDF, after the same two-page fitting as the download."""
    return _page_count(json.dumps(blocks, sort_keys=True)) if blocks else 0


def download_filename(blocks, company):
    import re
    name = next((b['text'].strip() for b in blocks if b['kind']=='contact' and '@' not in b['text']), 'Resume')
    if name.isupper(): name = name.title()
    clean = lambda value: re.sub(r'[^\w-]+', '_', value, flags=re.UNICODE).strip('_')[:90] or 'Resume'
    return clean(name) + '_' + clean(company) + '.pdf'
