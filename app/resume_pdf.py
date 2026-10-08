"""Render saved resume blocks only; no evidence annotations or model calls."""
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

def font_name(fmt):
    family = fmt.get('font_family', 'Arial')
    stem = {'Arial':'arial', 'Calibri':'calibri', 'Times New Roman':'times', 'serif':'times'}.get(family, 'arial')
    suffix = 'bi' if fmt.get('bold') and fmt.get('italic') else 'bd' if fmt.get('bold') else 'i' if fmt.get('italic') else ''
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

def render_resume(blocks):
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=(612,792), leftMargin=36, rightMargin=36,
                                 topMargin=30, bottomMargin=30, title='Resume', author='')
    flow = []
    for index, block in enumerate(blocks):
        kind = block['kind']
        fmt = block.get('format') or {}
        size = fmt.get('font_size',10)
        font = font_name(fmt)
        style = ParagraphStyle('resume', fontName=font, fontSize=size, leading=size*1.2,
            alignment={'left':TA_LEFT,'center':TA_CENTER,'right':TA_RIGHT}.get(fmt.get('align','left'), TA_LEFT),
            spaceBefore=8 if kind=='heading' else 4 if kind=='role' else 0,
            spaceAfter=4 if kind=='heading' else 2 if kind=='role' else 3,
            keepWithNext=kind in ('heading','role'), splitLongWords=True)
        if kind=='heading':
            rule=HRFlowable(width='100%',thickness=.7,color='#222222',spaceBefore=8,spaceAfter=3)
            rule.keepWithNext=True
            flow.append(rule)
            style.spaceBefore=0
        parts=block['text'].split('\t')
        def paragraph(text, paragraph_style=style):
            return Paragraph(escape(text).replace('\n','<br/>'), paragraph_style)
        if len(parts)>1:
            right_style=ParagraphStyle('right',parent=style,alignment=TA_RIGHT)
            right=' '.join(parts[1:])
            # Reserve enough room for dates without colliding with long employer names.
            right_width=min(220,max(125,pdfmetrics.stringWidth(right,font,size)+6))
            row=Table([[paragraph(parts[0]),paragraph(right,right_style)]],colWidths=[528-right_width,right_width])
            row.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0),('TOPPADDING',(0,0),(-1,-1),0),('BOTTOMPADDING',(0,0),(-1,-1),0)]))
            row.keepWithNext=style.keepWithNext
            row.spaceBefore=style.spaceBefore;row.spaceAfter=style.spaceAfter
            flow.append(row)
        elif kind=='bullet':
            style.leftIndent=10;style.firstLineIndent=0;style.bulletIndent=0
            style.bulletFontName=font;style.bulletFontSize=size
            flow.append(Paragraph(escape(block['text']).replace('\n','<br/>'),style,bulletText='•'))
        else:
            flow.append(paragraph(block['text']))
    document.build(flow)
    return output.getvalue()
