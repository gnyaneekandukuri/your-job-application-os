import re
from copy import deepcopy
from app.resume_master import text_template, format_text_blocks

TEXT = """ALEX EXAMPLE
alex@example.com
PROFESSIONAL EXPERIENCE
Summary: A product leader who builds
useful tools for customers.
Example Labs Boston, USA
Senior Product Manager April 2024 - Present
• Built a reporting platform with 12 users,
reducing manual work by 40%.
• Led work worth $300K in
operating-profit opportunity
Product Manager March 2022 - March 2024
• Delivered an earlier product.
ADDITIONAL INFORMATION
• Languages: English,
Spanish
"""

def words(text):
    return ' '.join(re.sub(r'(?m)^\s*[•▪●*-]\s+', '', text).split())

def test_pasted_resume_keeps_every_word_and_groups_wrapped_bullets():
    blocks=text_template(TEXT)
    assert words('\n'.join(b['text'] for b in blocks)) == words(TEXT)
    assert blocks[0]['format']['bold'] and blocks[0]['format']['align']=='center'
    assert all(b['format']['bold'] for b in blocks if b['kind'] in ('heading','role'))
    assert any(b['text']=='Senior Product Manager\tApril 2024 - Present' for b in blocks)
    assert any(b['kind']=='bullet' and b['text'].endswith('reducing manual work by 40%.') for b in blocks)
    assert any(b['kind']=='bullet' and b['text'].endswith('operating-profit opportunity') for b in blocks)
    assert any(b['text']=='Languages: English, Spanish' for b in blocks)
    assert len([b for b in blocks if b['kind']=='bullet'])==4

def test_repair_does_not_mutate_input_or_merge_pending_proposal_targets():
    rows=[{'item_id':'a','kind':'contact','text':'Alex','citations':[]},
          {'item_id':'b','kind':'contact','text':'alex@example.com','citations':[]},
          {'item_id':'c','kind':'bullet','text':'Built reporting,','citations':[]},
          {'item_id':'d','kind':'text','text':'saving time.','citations':[]}]
    before=deepcopy(rows)
    aliases={}
    assert len(format_text_blocks(rows,aliases=aliases))==3
    assert aliases['d']=='c'
    assert len(format_text_blocks(rows,{'d'}))==4
    assert rows==before

def test_normal_paragraphs_and_uploaded_formats_are_not_implicitly_repaired():
    blocks=text_template('Alex\nalex@example.com\nEXPERIENCE\nFirst point\nSecond point')
    assert blocks[-2]['text']=='First point' and blocks[-1]['text']=='Second point'
