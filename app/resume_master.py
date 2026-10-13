"""Preserve a resume as an ordered inventory; generation may only edit named items."""
import io
import re
from collections import Counter
from pypdf import PdfReader


SECTION_NAMES = {'summary', 'professional summary', 'profile', 'education', 'education & certifications',
    'experience', 'professional experience', 'work experience', 'employment history', 'skills',
    'technical skills', 'skills & competencies', 'projects', 'certifications', 'additional information',
    'leadership', 'volunteering', 'awards', 'publications', 'interests', 'languages', 'additional'}
MONTH = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
DATE_END = re.compile(r'(?<!\w)(?:'+MONTH+r'\.?\s+)?(?:19|20)\d{2}(?:\s*[-–—]\s*(?:(?:'+MONTH+r'\.?\s+)?(?:19|20)\d{2}|Present|Current|Now))?\s*$',re.I)


def default_format(kind, first=False):
    return {'font_family':'Times New Roman','font_size':16 if first else 10,
            'bold':first or kind in ('heading','role'), 'italic':False,
            'align':'center' if kind=='contact' else 'left'}


def format_text_blocks(rows, protected=(), aliases=None, preserve_format=False):
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
        b.update(kind=kind,text=text,format=b.get('format',default_format(kind,i==0)) if preserve_format else default_format(kind,i==0))
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
                # Older drafts can contain an already rewritten complete bullet
                # followed by its original wrapped tail. Do not append that tail twice.
                tail=' '.join(b['text'].split()).rstrip('.').casefold()
                prior=' '.join(previous['text'].split()).rstrip('.').casefold()
                if len(tail)<40 or tail not in prior:
                    previous['text']+=' '+b['text']
                previous['citations']=refs
                if aliases is not None and b.get('item_id'):
                    aliases[b['item_id']]=previous.get('item_id',b['item_id'])
                continue
        output.append(b)
        if starts_summary: summary=True
        elif b['kind'] in ('heading','role','bullet','contact'): summary=False
    return output


def normalize_master(rows):
    """Normalize cached/imported masters before allocating edit and citation IDs."""
    return finish(format_text_blocks(rows, preserve_format=True))


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
        return docx_template(raw)
    return text_template(text)


W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
FAMILIES = ('Arial', 'Calibri', 'Times New Roman')


def _on(node):
    return node is not None and node.get(W+'val', 'true') not in ('0', 'false', 'off')


def _run_props(rpr):
    props = {}
    if rpr is None: return props
    for tag, key in (('b', 'bold'), ('i', 'italic')):
        if rpr.find(W+tag) is not None: props[key] = _on(rpr.find(W+tag))
    size = rpr.find(W+'sz')
    if size is not None:
        try: props['size'] = max(6, min(36, float(size.get(W+'val'))/2))
        except (TypeError, ValueError): pass
    fonts = rpr.find(W+'rFonts')
    family = fonts.get(W+'ascii') or fonts.get(W+'hAnsi') if fonts is not None else None
    if family: props['family'] = family
    return props


def _par_props(ppr):
    props = {}
    if ppr is None: return props
    spacing = ppr.find(W+'spacing')
    if spacing is not None:
        for name in ('before', 'after'):
            if spacing.get(W+name) is not None: props['space_'+name] = int(spacing.get(W+name))/20
        if spacing.get(W+'line') and spacing.get(W+'lineRule', 'auto') == 'auto':
            props['line_spacing'] = int(spacing.get(W+'line'))/240
    borders = ppr.find(W+'pBdr')
    if borders is not None:
        props['rule'] = 'below' if borders.find(W+'bottom') is not None else 'above' if borders.find(W+'top') is not None else 'none'
    if ppr.find(W+'numPr') is not None: props['numbered'] = True
    jc = ppr.find(W+'jc')
    if jc is not None and jc.get(W+'val') in ('left', 'center', 'right'): props['align'] = jc.get(W+'val')
    return props


def _majority(pieces, key):
    weight = Counter()
    for piece in pieces:
        if piece[0].strip(): weight[key(piece)] += len(piece[0].strip())
    return weight.most_common(1)[0][0] if weight else None


def docx_template(raw):
    """Read paragraphs with resolved Word formatting: style chain, run emphasis, borders and page layout."""
    import zipfile, xml.etree.ElementTree as ET
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        root = ET.fromstring(archive.read('word/document.xml'))
        styles = ET.fromstring(archive.read('word/styles.xml')) if 'word/styles.xml' in archive.namelist() else None
    table, run_defaults, par_defaults, default_style = {}, {}, {}, None
    if styles is not None:
        run_defaults = _run_props(styles.find(W+'docDefaults/'+W+'rPrDefault/'+W+'rPr'))
        par_defaults = _par_props(styles.find(W+'docDefaults/'+W+'pPrDefault/'+W+'pPr'))
        for style in styles.findall(W+'style'):
            sid, name, based = style.get(W+'styleId'), style.find(W+'name'), style.find(W+'basedOn')
            table[sid] = {'name': (name.get(W+'val') if name is not None else sid or '').lower(),
                          'based': based.get(W+'val') if based is not None else None,
                          'run': _run_props(style.find(W+'rPr')), 'par': _par_props(style.find(W+'pPr'))}
            if style.get(W+'type') == 'paragraph' and style.get(W+'default') in ('1', 'true', 'on'): default_style = sid

    def resolved(sid):
        seen, styles_used = set(), []
        while sid in table and sid not in seen:
            seen.add(sid); styles_used.insert(0, table[sid]); sid = table[sid]['based']
        run, par = dict(run_defaults), dict(par_defaults)
        for style in styles_used:
            run.update(style['run']); par.update(style['par'])
        return run, par, ' '.join(s['name'] for s in styles_used)

    rows = []
    for paragraph in root.findall('.//'+W+'body//'+W+'p'):
        ppr = paragraph.find(W+'pPr')
        named = ppr.find(W+'pStyle') if ppr is not None else None
        style_run, par, names = resolved(named.get(W+'val') if named is not None else default_style)
        par.update(_par_props(ppr))
        pieces = []
        for run in paragraph.iter(W+'r'):
            props = {**style_run, **_run_props(run.find(W+'rPr'))}
            for node in run:
                text = (node.text or '') if node.tag == W+'t' else '\t' if node.tag == W+'tab' else '\n' if node.tag == W+'br' else ''
                if text: pieces.append((text, bool(props.get('bold')), bool(props.get('italic')), props.get('size', 11), props.get('family')))
        text = ''.join(p[0] for p in pieces).strip()
        if text: rows.append({'pieces': pieces, 'text': text, 'par': par, 'names': names})
    if not rows: raise ValueError('Empty document')

    emphasis = lambda p: (p[1], p[2])
    body_size = Counter(_majority(r['pieces'], lambda p: p[3]) for r in rows).most_common(1)[0][0]
    blocks = []
    for row in rows:
        pieces, par, text = row['pieces'], row['par'], row['text']
        split = next((i for i, p in enumerate(pieces) if '\t' in p[0] and ''.join(q[0] for q in pieces[:i]).strip()), None)
        left, right = (pieces[:split], pieces[split:]) if split is not None else (pieces, [])
        groups = []
        for piece in left:
            if not piece[0].strip() and not groups: continue
            if groups and groups[-1][0] == emphasis(piece): groups[-1][1].append(piece[0])
            elif piece[0].strip(): groups.append((emphasis(piece), [piece[0]]))
            else: groups[-1][1].append(piece[0])
        base = (_majority([p for p in left if emphasis(p) != groups[0][0]], emphasis) if len(groups) > 1 else None) or (groups[0][0] if groups else (False, False))
        lead = None
        if len(groups) > 1:
            lead_text = ''.join(groups[0][1]).strip()
            if 0 < len(lead_text) <= 120 and text.startswith(lead_text):
                lead = {'text': lead_text, 'bold': groups[0][0][0], 'italic': groups[0][0][1]}
        size = _majority(pieces, lambda p: p[3]) or body_size
        family = _majority(pieces, lambda p: p[4])
        all_bold = all(p[1] for p in left if p[0].strip())
        heading = bool(blocks) and ('heading' in row['names'] or (text.isupper() and len(text) < 65) or
                                    text.casefold() in SECTION_NAMES or
                                    (all_bold and split is None and len(text) < 40 and size >= body_size + 1.5))
        bullet = bool(par.get('numbered')) or 'list' in row['names']
        starts_bold = bool(groups) and groups[0][0][0]
        kind = ('heading' if heading else 'contact' if len(blocks) < 2 else 'bullet' if bullet else
                'role' if starts_bold and (all_bold or split is not None) else 'text')
        fmt = default_format(kind, not blocks)
        fmt.update(bold=all_bold if not lead else base[0], italic=base[1], font_size=size)
        if family in FAMILIES: fmt['font_family'] = family
        if par.get('align'): fmt['align'] = par['align']
        if lead: fmt['lead'] = lead
        right_emphasis = _majority(right, emphasis)
        if right_emphasis: fmt['right_bold'], fmt['right_italic'] = right_emphasis
        for key in ('space_before', 'space_after'):
            if key in par: fmt[key] = max(0, min(48, par[key]))
        if kind == 'heading': fmt['rule'] = par.get('rule', 'none')
        blocks.append({'kind': kind, 'text': text, 'format': fmt})
    page = {'line_spacing': max(.8, min(3, resolved(default_style)[1].get('line_spacing') or 1))}
    section = root.find('.//'+W+'body/'+W+'sectPr')
    if section is not None:
        size, margin = section.find(W+'pgSz'), section.find(W+'pgMar')
        if size is not None: page.update(width=int(size.get(W+'w'))/20, height=int(size.get(W+'h'))/20)
        if margin is not None: page['margins'] = [max(0, min(144, int(margin.get(W+side, 1440))/20)) for side in ('top', 'right', 'bottom', 'left')]
    blocks[0]['format']['page'] = page
    return finish(blocks)
