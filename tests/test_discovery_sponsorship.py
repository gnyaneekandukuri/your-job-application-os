import pytest
from app.discovery import Discovery
from app.discovery_sponsorship import no_sponsorship


@pytest.mark.parametrize('text', [
    'Visa sponsorship is not available for this position.',
    'This role does not offer visa sponsorship.',
    "We can't sponsor a visa right now.",
    'We are unable to provide visa sponsorship (e.g. H-1B).',
    'VISA Sponsorship will not be supported by this role.',
    'Fora is unable to sponsor or assist with U.S. work authorization.',
    'Qualified Applicants will not require employer sponsored work authorization now or in the future.',
    'This position is not eligible for immigration sponsorship.',
    'Sponsorship: This position is not eligible for visa sponsorship.',
    'Must be authorized to work in the US without requiring current or future employer sponsorship.',
    'No sponsorship available.',
    'Applicants must be a U.S. citizen.',
    '<p>Visa&nbsp;sponsorship is <b>not</b> available.</p>',
])
def test_explicit_refusals_match(text):
    assert no_sponsorship('Build AI products. ' + text)


@pytest.mark.parametrize('text', [
    'We will consider all applicants without regard to age, ancestry, citizenship, color or immigration status.',
    'Will you require Visa Sponsorship now, or in the future? Yes No',
    'I am authorized to work in the country and do not need a company to sponsor my visa',
    'Work Authorization: Legally authorized to work in the country of the job location.',
    'Visa sponsorship is available for this role.',
    'Controlled technology may not be released to you without sponsorship for an export license.',
    'We do not sponsor new visas, but we welcome H-1B transfers.',
    '',
])
def test_silence_questions_and_boilerplate_do_not_match(text):
    assert no_sponsorship(text) is None


def test_quote_is_the_refusing_sentence():
    found = no_sponsorship('Lead the roadmap. Visa sponsorship is not available for this position. Apply now.')
    assert found['quote'] == 'Visa sponsorship is not available for this position.'


def test_no_sponsorship_jobs_skip_automatic_analysis():
    item = {'keyword_match': {'priority': 'high'}, 'description': 'x' * 300, 'location_match': {'status': 'match'},
            'assessment': None, 'age_state': 'fresh'}
    assert Discovery.auto_eligible(item)
    assert not Discovery.auto_eligible({**item, 'sponsorship': {'quote': 'No sponsorship.'}})
    assert not Discovery.initial_screening_eligible({**item, 'sponsorship': {'quote': 'No sponsorship.'}})
