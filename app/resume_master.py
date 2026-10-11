"""Preserve a resume as an ordered inventory; generation may only edit named items."""
import io
import re
from collections import Counter
from pypdf import PdfReader


SECTION_NAMES = {'summary', 'professional summary', 'profile', 'education', 'education & certifications',
    'experience', 'professional experience', 'work experience', 'employment history', 'skills',
    'technical skills', 'skills & competencies', 'projects', 'certifications', 'additional information',
    'leadership', 'volunteering', 'awards', 'publications', 'interests', 'languages'}
MONTH = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
DATE_END = re.compile(r'(?<!\w)(?:'+MONTH+r'\.?\s+)?(?:19|20)\d{2}(?:\s*[-–—]\s*(?:(?:'+MONTH+r'\.?\s+)?(?:19|20)\d{2}|Present|Current|Now))?\s*$',re.I)


def default_format(kind, first=False):
    return {'font_family':'Times New Roman','font_size':16 if first else 10,
            'bold':first or kind in ('heading','role'), 'italic':False,
            'align':'center' if kind=='contact' else 'left'}


def format_text_blocks(rows, protected=(), aliases=None):
    """Reconstruct plain-text layout without generating or deleting wording.

    Protected proposal targets stay separate so accepting an existing suggestion
    cannot unexpectedly replace neighboring content after a layout repair.
    """
    from copy import deepcopy
    blocks=deepcopy(rows); protected=set(protected)
    if aliases is not None:
        aliases.update({b['item_id']:b['item_id'] for b in blocks if b.get('item_id')})
    for i,b in enumerate(blocks):
        text=b['text'].strip()
        next_text=blocks[i+1]['text'].strip() if i+1<len(blocks) else ''
        is_section=text.lstrip('# ').strip().casefold() in SECTION_NAMES
        dated=bool(DATE_END.search(text)) and len(text)<200 and b['kind']!='bullet'
        company=(i>1 and len(text)<150 and text[:1].isupper() and b['kind']!='bullet' and
                 bool(DATE_END.search(next_text)) and len(next_text)<200 and blocks[i+1]['kind']!='bullet')
        if i==0: kind='contact'
        elif is_section or b['kind']=='heading': kind='heading'
        elif b['kind']=='bullet': kind='bullet'
        elif dated or company: kind='role'
        elif i==1 and ('@' in text or 'linkedin.' in text or '|' in text): kind='contact'
        else: kind=b['kind'] if b['kind'] in ('contact','role') else 'text'
        if kind=='role' and '\t' not in text:
            match=DATE_END.search(text)
            if match and match.start()>0:
                # Only introduce an alignment separator; preserve every word.
                text=text[:match.start()].rstrip()+'\t'+text[match.start():]
        b.update(kind=kind,text=text,format=default_format(kind,i==0))
    output=[]; summary=False
    for b in blocks:
        starts_summary=b['text'].lower().startswith('summary:')
        join=(output and b['kind']=='text' and not starts_summary and
              (output[-1]['kind']=='bullet' or summary) and
              b.get('item_id') not in protected and output[-1].get('item_id') not in protected)
        if join:
            previous=output[-1]
            refs=previous.get('citations',[])+[c for c in b.get('citations',[]) if c not in previous.get('citations',[])]
            if len(previous['text'])+len(b['text'])<1199 and len(refs)<=6:
                previous['text']+=' '+b['text']
                previous['citations']=refs
                if aliases is not None and b.get('item_id'):
                    aliases[b['item_id']]=previous.get('item_id',b['item_id'])
                continue
        output.append(b)
        if starts_summary: summary=True
        elif b['kind'] in ('heading','role','bullet','contact'): summary=False
    return output


def text_template(text):
    blocks=[]
    for raw in text.splitlines():
        line=raw.strip()
        if not line or re.fullmatch(r"## Page \d+",line): continue
        bullet=bool(re.match(r'^[•▪●*-]\s+',line))
        if bullet: line=re.sub(r'^[•▪●*-]\s+','',line)
        kind='bullet' if bullet else 'heading' if line.startswith('#') else 'text'
        if kind=='heading':line=line.lstrip('# ').strip()
        blocks.append({'kind':kind,'text':line,'format':{}})
    return finish(format_text_blocks(blocks))


def finish(blocks):
    for i,b in enumerate(blocks):
        b.update(item_id=f'm{i+1}',citations=[] if b['kind']=='heading' else [{'source_id':'master:'+str(i+1),'quote':b['text']}],reason='Retained from the master resume.')
    return blocks


def pdf_template(raw):
    reader=PdfReader(io.BytesIO(raw))
    if reader.is_encrypted or len(reader.pages)>40: raise ValueError('Unsupported PDF')
    blocks=[]
    for page in reader.pages:
        fragments=[]
        def collect(text,cm,tm,font,size):
            if not text.strip() or not font: return
            x=tm[4]*cm[0]+tm[5]*cm[2]+cm[4]
            y=tm[4]*cm[1]+tm[5]*cm[3]+cm[5]
            effective=size*abs(cm[0] or cm[3] or 1)
            if not (0<=x<=float(page.mediabox.width) and 0<=y<=float(page.mediabox.height)):return
            name=str(font.get('/BaseFont',''))
            fragments.append({'text':text.strip(),'x':x,'y':y,'size':effective,'bold':'bold' in name.lower(),'italic':any(v in name.lower() for v in ['italic','oblique']),'font':name})
        content=page.get_contents()
        if content and len(content.get_data())>4_000_000:raise ValueError('Page too large')
        page.extract_text(visitor_text=collect)
        rows=[]
        for f in sorted(fragments,key=lambda f:(-f['y'],f['x'])):
            if not rows or abs(rows[-1][0]['y']-f['y'])>2:rows.append([f])
            else:rows[-1].append(f)
        for row in rows:
            row.sort(key=lambda f:f['x'])
            words=[];previous=None
            for f in row:
                gap=f['x']-(previous['x']+len(previous['text'])*previous['size']*.48) if previous else 0
                words.append(('\t' if gap>48 else ' ' if words else '')+f['text']);previous=f
            line=''.join(words).strip()
            if not line:continue
            bullet=line.startswith(('•','▪','●','\x7f'))
            if bullet:line=line[1:].strip()
            all_bold=all(f['bold'] for f in row if f['text'] not in ('•','▪','●','\x7f'))
            heading=bool(blocks and line.isupper() and len(line)<65)
            kind='heading' if heading else 'contact' if len(blocks)<2 else 'bullet' if bullet else 'role' if all_bold else 'text'
            font=Counter(f['font'] for f in row).most_common(1)[0][0].lower()
            family='Times New Roman' if 'times' in font else 'Arial' if 'arial' in font or 'helvetica' in font else 'Calibri' if 'calibri' in font else 'serif'
            fmt={'font_family':family,'font_size':round(row[0]['size'],2),'bold':all_bold,'italic':all(f['italic'] for f in row),'align':'center' if kind=='contact' else 'left'}
            # Wrapped bullet/summary text remains one item. A company/title row ends it.
            if kind=='text' and blocks and blocks[-1]['kind'] in ('bullet','text'):
                blocks[-1]['text']+=' '+line
            else:blocks.append({'kind':kind,'text':line,'format':fmt})
    if not blocks or sum(len(b['text']) for b in blocks)>64000:raise ValueError('Unreadable or oversized resume')
    return finish(blocks)


def file_template(filename, raw):
    import json, subprocess, sys
    from pathlib import Path
    if filename.lower().endswith('.pdf'):
        result=subprocess.run([sys.executable,'-m','app.pdf_worker','--resume-template'],input=raw,capture_output=True,timeout=25,cwd=Path(__file__).resolve().parents[1],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise ValueError('Resume layout could not be read')
        return json.loads(result.stdout)['blocks']
    from .document_text import extract_document
    text=extract_document(filename,raw)
    if filename.lower().endswith('.docx'):
        import zipfile, xml.etree.ElementTree as ET
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:root=ET.fromstring(archive.read('word/document.xml'))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'};blocks=[]
        for paragraph in root.findall('.//w:body//w:p',ns):
            text=''.join((n.text or '') if n.tag.endswith('}t') else '\t' if n.tag.endswith('}tab') else '\n' if n.tag.endswith('}br') else '' for n in paragraph.iter()).strip()
            if not text:continue
            style=paragraph.find('w:pPr/w:pStyle',ns)
            heading=style is not None and 'heading' in style.get('{'+ns['w']+'}val','').lower()
            kind='heading' if heading or (blocks and text.isupper() and len(text)<65) else 'bullet' if paragraph.find('w:pPr/w:numPr',ns) is not None else 'contact' if len(blocks)<2 else 'text'
            def prop(name):
                return paragraph.find('.//w:rPr/w:'+name,ns)
            fmt=default_format(kind,not blocks)
            for name,key in [('b','bold'),('i','italic')]:
                node=prop(name)
                if node is not None:fmt[key]=node.get('{'+ns['w']+'}val','true') not in ('0','false','off')
            size=prop('sz')
            if size is not None:
                try:fmt['font_size']=max(6,min(36,float(size.get('{'+ns['w']+'}val'))/2))
                except (TypeError,ValueError):pass
            font=prop('rFonts')
            family=font.get('{'+ns['w']+'}ascii') if font is not None else None
            if family in ('Arial','Calibri','Times New Roman'):fmt['font_family']=family
            align=paragraph.find('w:pPr/w:jc',ns)
            if align is not None and align.get('{'+ns['w']+'}val') in ('left','center','right'):
                fmt['align']=align.get('{'+ns['w']+'}val')
            if kind=='text' and fmt['bold']:kind='role'
            blocks.append({'kind':kind,'text':text,'format':fmt})
        return finish(blocks)
    return text_template(text)
