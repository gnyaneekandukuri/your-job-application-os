"""Read-only restart readiness; never interrupt a paid request."""
from .application_answer import LOCK as answer_lock
from .team_message import LOCK as message_lock


def activity(state):
    d=state.discovery
    alive=lambda obj: bool(obj and obj.is_alive())
    busy=[]
    for label, worker in [('scan',d.worker),('automatic_analysis',d.assessment_worker),
                          ('selected_analysis',d.selected.worker),('deep_analysis',d.analysis.worker),
                          ('tailoring',state.tailoring.worker),('skills',state.skills.worker)]:
        if alive(worker): busy.append(label)
    if state.google_sheets.schedule.active: busy.append('google_sheets_sync')
    manual=getattr(d.analysis,'manual_queue',None)
    if manual and manual.pending() and not d.draining(): busy.append('manual_analysis_queue')
    if getattr(state.resume_approach, 'active', False): busy.append('resume_approach')
    for label, lock in [('google_sheets_request',state.google_sheets.lock),('application_answer',answer_lock),('writing_help',message_lock),('tailoring_request',state.tailoring.lock)]:
        acquired=lock.acquire(blocking=False)
        if acquired: lock.release()
        else: busy.append(label)
    for label, connection in [('yc_browser',d.yc),('lenny_browser',d.lenny)]:
        if connection.status().get('busy'): busy.append(label)
    return {'draining':d.draining(), 'safe_to_restart':not busy,'active':busy}
