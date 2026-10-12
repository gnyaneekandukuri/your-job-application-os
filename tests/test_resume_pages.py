from app.resume_pdf import page_count


def blocks(bullets):
    return [{'kind':'contact','text':'JANE CANDIDATE'},{'kind':'heading','text':'EXPERIENCE'}]+[
        {'kind':'bullet','text':'Led customer discovery and shipped a reporting workflow that reduced weekly preparation time for analysts. '*2}
        for _ in range(bullets)]


def test_page_count_matches_one_page_fitting_and_overflow():
    assert page_count([]) == 0
    assert page_count(blocks(10)) == 1
    assert page_count(blocks(80)) >= 2
