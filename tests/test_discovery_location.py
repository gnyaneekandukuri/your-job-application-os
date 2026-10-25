import pytest
from app.discovery_location import location_match

US = {'location_filter': {'countries': ['US'], 'label': 'USA'}}


@pytest.mark.parametrize('location', [
    'Mountain View, CA', 'Redwood City, CA', 'San Francisco, California', 'Utah | Remote',
    'US-REMOTE', 'Remote - US', 'US - California - San Francisco', 'not set, USA',
    'New York, New York, USA', 'Atlanta, Georgia', 'Vancouver, WA', 'Austin, TX or Remote',
    # State codes that are also country codes: Panama, Morocco, Gabon, Colombia, India, Delaware.
    'Pittsburgh, PA', 'Boston, MA', 'Atlanta, GA', 'Denver, CO', 'Indianapolis, IN', 'Wilmington, DE',
    'New York, NY; London, UK'])
def test_us_states_and_us_labels_match(location):
    result = location_match(location, US)
    assert result['status'] == 'match', result
    assert set(result['countries']) <= {'US', 'GB'}


@pytest.mark.parametrize('location,status', [
    ('Berlin, DE', 'outside'), ('Delhi, IN', 'outside'), ('Jakarta, ID', 'outside'),
    ('Gurugram, HR, IN / Gurugram, Haryana, IN / DL, IN / Delhi, IN', 'outside'),
    ('Bengaluru, Karnataka, IN', 'outside'), ('Tbilisi, Georgia', 'outside'),
    ('Toronto, CA', 'unclear'), ('Vancouver, BC', 'unclear'), ('Remote', 'unclear'),
    ('San Francisco / Remote', 'unclear'), ('Remote, not in Canada', 'unclear')])
def test_foreign_cities_and_unscoped_remote_are_not_us(location, status):
    assert location_match(location, US)['status'] == status
