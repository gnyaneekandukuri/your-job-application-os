import io
import zipfile

from pypdf import PdfReader

from app.resume_master import file_template, normalize_master
from app.resume_pdf import page_count, render_resume
from app.tailoring import Block

NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def run(text, bold=False, italic=False, size=None):
    props = ('<w:b/>' if bold else '') + ('<w:i/>' if italic else '') + (f'<w:sz w:val="{size*2}"/>' if size else '')
    body = ''.join('<w:tab/>' if part == '\t' else f'<w:t xml:space="preserve">{part}</w:t>' for part in text.replace('\t', '\x00\t\x00').split('\x00') if part)
    return f'<w:r><w:rPr>{props}</w:rPr>{body}</w:r>'


def paragraph(*runs, style=None, border=False, center=False):
    props = (f'<w:pStyle w:val="{style}"/>' if style else '') + ('<w:pBdr><w:bottom w:val="single" w:sz="6"/></w:pBdr>' if border else '') + \
            ('<w:jc w:val="center"/>' if center else '')
    return f'<w:p><w:pPr>{props}</w:pPr>{"".join(runs)}</w:p>'


def document():
    styles = (f'<w:styles {NS}><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial"/><w:sz w:val="20"/></w:rPr></w:rPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:pPr><w:spacing w:line="264" w:lineRule="auto"/></w:pPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/><w:basedOn w:val="Normal"/></w:style></w:styles>')
    body = ''.join([
        paragraph(run('Jane Candidate', size=26), center=True),
        paragraph(run('jane@example.com  |  555'), center=True),
        paragraph(run('Work Experience', bold=True, size=13), border=True),
        paragraph(run('Example Co', bold=True), run(' - Data platform'), run('\tJan 2024 – Present', bold=True)),
        paragraph(run('Product Manager', bold=True, italic=True), run('\tRemote')),
        paragraph(run('Launched a reporting workflow that cut weekly preparation time by 40%.'), style='ListBullet'),
        paragraph(run('Tools:', bold=True), run(' Python, SQL'), style='ListBullet'),
    ])
    sect = '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="648" w:right="792" w:bottom="648" w:left="792"/></w:sectPr>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as archive:
        archive.writestr('word/document.xml', f'<w:document {NS}><w:body>{body}{sect}</w:body></w:document>')
        archive.writestr('word/styles.xml', styles)
    return out.getvalue()


def test_docx_master_keeps_layout_and_emphasis():
    blocks = normalize_master(file_template('master.docx', document()))
    kinds = [b['kind'] for b in blocks]
    assert kinds == ['contact', 'contact', 'heading', 'role', 'role', 'bullet', 'bullet']
    assert all(b['format']['font_family'] == 'Arial' for b in blocks)
    assert blocks[0]['format']['page'] == {'line_spacing': 1.1, 'width': 612, 'height': 792, 'margins': [32.4, 39.6, 32.4, 39.6]}
    assert blocks[2]['format']['rule'] == 'below'
    company = blocks[3]
    assert company['text'] == 'Example Co - Data platform\tJan 2024 – Present'
    assert company['format']['lead'] == {'text': 'Example Co', 'bold': True, 'italic': False}
    assert company['format']['right_bold'] is True and company['format']['bold'] is False
    title = blocks[4]['format']
    assert title['bold'] and title['italic'] and title['right_bold'] is False
    assert blocks[6]['format']['lead']['text'] == 'Tools:'
    # The tailoring model accepts these formats, and the renderer uses them.
    rows = [Block.model_validate(b).model_dump() for b in blocks]
    pdf = PdfReader(io.BytesIO(render_resume(rows)))
    assert len(pdf.pages) == 1 and 'Example Co - Data platform' in pdf.pages[0].extract_text()


def test_two_page_resume_is_not_squeezed_to_one():
    blocks = [{'kind': 'contact', 'text': 'JANE CANDIDATE'}, {'kind': 'heading', 'text': 'EXPERIENCE'}] + [
        {'kind': 'bullet', 'text': 'Led customer discovery and shipped a reporting workflow that reduced weekly preparation time. ' * 2}
        for _ in range(28)]
    assert page_count(blocks) == 2


def test_jd_quotes_match_despite_typographic_punctuation():
    from app.tailoring import normalized_quote
    jd = 'As Ploy’s first PM — you’ll own “major” initiatives'
    assert normalized_quote("Ploy's first PM - you'll own \"major\" initiatives") in normalized_quote(jd)


def test_calibri_bold_and_bold_italic_use_the_real_calibri_files():
    from pathlib import Path
    import pytest
    from app.resume_pdf import font_name
    if not Path('C:/Windows/Fonts/calibrib.ttf').is_file(): pytest.skip('Calibri is not installed')
    assert font_name({'font_family': 'Calibri', 'bold': True}) == 'Resume-calibrib'
    assert font_name({'font_family': 'Calibri', 'bold': True, 'italic': True}) == 'Resume-calibriz'


def test_word_list_indent_is_read_from_the_numbering_definition():
    import io, zipfile
    from app.resume_master import docx_template
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    document = (f'<w:document {w}><w:body>'
                '<w:p><w:r><w:t>Alex Example</w:t></w:r></w:p><w:p><w:r><w:t>alex@example.com</w:t></w:r></w:p>'
                '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t>EXPERIENCE</w:t></w:r></w:p>'
                '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>Built a reporting tool for 12 users.</w:t></w:r></w:p>'
                '</w:body></w:document>')
    numbering = (f'<w:numbering {w}><w:abstractNum w:abstractNumId="7"><w:lvl w:ilvl="0"><w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>'
                 '<w:num w:numId="1"><w:abstractNumId w:val="7"/></w:num></w:numbering>')
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w') as z:
        z.writestr('word/document.xml', document); z.writestr('word/numbering.xml', numbering)
    bullet = next(b for b in docx_template(raw.getvalue()) if b['kind'] == 'bullet')
    assert (bullet['format']['indent'], bullet['format']['hanging']) == (36.0, 18.0)
