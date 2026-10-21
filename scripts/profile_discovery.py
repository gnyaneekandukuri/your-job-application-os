"""Offline synthetic endpoint profile; run with python -m pytest -s scripts/profile_discovery.py.

All generated databases live in pytest's tmp_path. Baseline is commit a153991.
"""
import cProfile
import json
import pstats
import subprocess
import time
from types import ModuleType

from fastapi.testclient import TestClient
from app.main import create_app


def test_profile(tmp_path, monkeypatch):
    source = subprocess.check_output(['git','-c','safe.directory=*','show','a153991:app/job_list.py'],text=True)
    baseline = ModuleType('app._profile_baseline')
    baseline.__package__ = 'app'
    exec(compile(source,'baseline_job_list.py','exec'),baseline.__dict__)
    with TestClient(create_app(tmp_path)) as client:
        d=client.app.state.discovery
        jl=client.app.state.job_list
        # 1,000 listings, five scan reports, and ~0.5 MB of assessment metadata.
        items=[]
        for n in range(1000):
            items.append(dict(id=str(n),title='Product Manager',company='Example '+str(n),
                description='Own roadmap and discovery. '*30,location='USA',url=f'https://example.com/{n}',
                first_seen='2026-10-20T00:00:00+00:00',last_seen='2026-10-20T00:00:00+00:00',
                sources=[{'board':'yc'}],assessment={'state':'NOT_STARTED'},
                location_match={'status':'match'},keyword_match={'priority':'high','label':'High priority'}))
        reports=[dict(id=n,started='today',finished='today',state='complete',
            roles=[dict(id=i['id'],company=i['company'],title=i['title'],reason='Initial matching evidence. '*15) for i in items]) for n in range(5)]
        data={'items':items,'run_reports':reports,'assessment_results':{'items':[], 'evidence':'x'*480000},'settings':{}}
        monkeypatch.setattr(d,'table_view',lambda **kw:data.copy())
        assert jl.refresh()
        def measure(label, call):
            profile=cProfile.Profile(); durations=[]
            for _ in range(5):
                start=time.perf_counter(); profile.enable(); result=call(); profile.disable()
                encoded=json.dumps(result,separators=(',',':')).encode()
                durations.append((time.perf_counter()-start)*1000)
            print(f'{label}: median_ms={sorted(durations)[2]:.2f}, bytes={len(encoded)}')
            pstats.Stats(profile).strip_dirs().sort_stats('cumulative').print_stats(6)
            return result
        measure('overview_before',lambda:baseline.JobList.overview(jl))
        compact=measure('overview_after',jl.overview)
        assert len(json.dumps(compact))<50000
        measure('page_runs_before',lambda:baseline.JobList.page(jl,runs=True))
        original_view=d.view
        monkeypatch.setattr(d,'view',lambda **kw:(_ for _ in ()).throw(AssertionError('HTTP rebuilt discovery')))
        measure('page_runs_after',lambda:jl.page(runs=True))
        response=client.get('/api/discovery/page?view=runs')
        assert response.status_code==200 and response.json()['items']==[]

        monkeypatch.setattr(d,'view',original_view)
        with d.db() as db:
            db.executemany('INSERT INTO opportunities(id,payload,first_seen,last_seen) VALUES(?,?,?,?)',
                           [(i['id'],json.dumps(i),i['first_seen'],i['last_seen']) for i in items])
            db.execute("INSERT INTO automatic_candidates VALUES('0')")
        monkeypatch.setattr(d,'assess_high_priority',lambda *a,**kw:None)
        monkeypatch.setattr('app.discovery_screening.screen',lambda *a,**kw:{'errors':0})
        before_d=ModuleType('app._profile_discovery_baseline'); before_d.__package__='app'
        source=subprocess.check_output(['git','-c','safe.directory=*','show','a153991:app/discovery.py'],text=True)
        exec(compile(source,'baseline_discovery.py','exec'),before_d.__dict__)
        measure('reconcile_before',lambda:before_d.Discovery.reconcile_automatic(d))
        measure('reconcile_after',d.reconcile_automatic)
