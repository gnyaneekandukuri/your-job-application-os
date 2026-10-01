import { useEffect, useRef, useState } from "react";

import { createPortal } from "react-dom";

type Source = { id: string; title: string; employer: string; project: string; path: string; revision: string; text: string; category: string };
export function AddExperience({ jobId, requirementId, inputVersion, requirementText, onSaved }: {
  jobId: string; requirementId: string; inputVersion: string; requirementText: string; onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [skipped, setSkipped] = useState(false);
  const [sources, setSources] = useState<Source[]>([]);
  const [source, setSource] = useState<Source | null>(null);
  const [selected, setSelected] = useState("");
  const [text, setText] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (open) dialog.current?.showModal(); }, [open]);
  const pending = useRef<Record<string, unknown> | null>(null);
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    void fetch('/api/library').then(r => { if (!r.ok) throw new Error('Could not load stories.'); return r.json(); })
      .then(d => { if (!cancelled) setSources(d.sources.filter((s: Source) => s.category === 'story')); })
      .catch(e => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, [open]);
  useEffect(() => {
    setSource(null);
    if (!selected) return;
    let cancelled = false;
    void fetch(`/api/library/sources/${selected}`).then(r => { if (!r.ok) throw new Error('Could not load story preview.'); return r.json(); })
      .then(d => { if (!cancelled) setSource(d); }).catch(e => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, [selected]);
  async function save() {
    if (!source || busy) return;
    setBusy(true); setError('');
    pending.current ??= { request_id: crypto.randomUUID(), source_id: source.id, expected_revision: source.revision,
      input_version: inputVersion, text: text.trim(), consent_to_send: consent };
    try {
      const response = await fetch(`/api/jobs/${jobId}/requirements/${requirementId}/experience`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Local-Review': '1' }, body: JSON.stringify(pending.current),
      });
      const data = await response.json();
      if (!response.ok) { pending.current = null; throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not save experience.'); }
      onSaved(); setOpen(false);
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  return <div className="experience-addition">
    {!open && <><p>{skipped ? 'Assessment kept - score unchanged.' : 'Missing context or disagree with this judgment?'}</p>
      <button onClick={() => { setOpen(true); setSkipped(false); }}>Add evidence / clarify</button>{!skipped && <button onClick={() => setSkipped(true)}>Keep assessment</button>}</>}
    {open && createPortal(<dialog ref={dialog} className="experience-dialog" aria-label="Add your experience" onCancel={() => setOpen(false)}><fieldset disabled={busy}>
      <legend>Add evidence or clarify</legend><p><strong>Requirement:</strong> {requirementText}</p>
      <label>Employer / project story<select value={selected} onChange={e => setSelected(e.target.value)} disabled={!!pending.current}>
        <option value="">Choose the correct story…</option>
        {sources.map(s => <option key={s.id} value={s.id}>{s.title} — {s.employer} / {s.project}</option>)}
      </select></label>
      {source && <><p>Will append to: {source.path}</p><details><summary>Check existing experience</summary><p className="description">{source.text}</p></details></>}
      <label>What did you do?<textarea rows={6} maxLength={12000} value={text} disabled={!!pending.current}
        placeholder="Describe the project, your actions and ownership, scope, dates if known, and what happened. Distinguish expected from achieved outcomes."
        onChange={e => setText(e.target.value)} /></label>
      <p>Your account is saved with history. It is evaluated for relevance and consistency; saving it does not guarantee score credit or approve a resume claim.</p>
      <label><input type="checkbox" checked={consent} disabled={!!pending.current} onChange={e => setConsent(e.target.checked)} /> Update analysis after saving: send updated story evidence and this role to OpenAI with usage tracked.</label>
      <button disabled={!source || text.trim().length < 20} onClick={() => void save()}>{busy ? 'Saving…' : pending.current ? 'Retry save' : consent ? 'Save and reassess' : 'Save experience'}</button>
      <button onClick={() => setOpen(false)}>Cancel</button>
      {error && <p className="error" role="alert">{error}</p>}
    </fieldset></dialog>, document.body)}
    {error && <p className="error" role="alert">{error}</p>}
  </div>;
}
