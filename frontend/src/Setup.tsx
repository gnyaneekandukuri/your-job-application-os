import { useEffect, useState, type ReactNode } from "react";

export function Setup({ children }: { children: ReactNode }) {
  const [loaded, setLoaded] = useState(false);
  const [complete, setComplete] = useState(false);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [roles, setRoles] = useState("");
  const [countries, setCountries] = useState("");
  const [cities, setCities] = useState("");
  const [transfer, setTransfer] = useState("unknown");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    fetch("/api/setup").then(async r => {
      if (!r.ok) throw new Error("Could not load local setup.");
      const d = await r.json();
      setComplete(d.complete); setName(d.name); setRoles(d.target_roles.join(", "));
      setCountries(d.countries.join(", ")); setCities(d.preferred_cities.join(", "));
      setTransfer(d.requires_h1b_transfer === null ? "unknown" : String(d.requires_h1b_transfer));
      setLoaded(true);
    }).catch(e => setError(e.message));
  }, []);
  if (!loaded) return <main><p>{error || "Loading local workspace…"}</p></main>;
  if (complete && !editing) return <><button onClick={() => setEditing(true)}>Your preferences</button>{children}</>;
  const split = (value: string) => value.split(",").map(s => s.trim()).filter(Boolean);
  return <main style={{ maxWidth: 640, margin: "40px auto", padding: 24 }}>
    <h1>{complete ? "Your preferences" : "Set up your job search"}</h1>
    <p>Your context and results stay in your local workspace. Add your experience in Your context library after setup. Current discovery sources cover product-management roles.</p>
    <form onSubmit={async e => {
      e.preventDefault(); setBusy(true); setError("");
      try {
        const r = await fetch("/api/setup", { method: "POST", headers: { "Content-Type": "application/json", "X-Local-Review": "1" }, body: JSON.stringify({ name, target_roles: split(roles), countries: split(countries), preferred_cities: split(cities), requires_h1b_transfer: transfer === "unknown" ? null : transfer === "true" }) });
        if (!r.ok) { const d = await r.json(); throw new Error(typeof d.detail === "string" ? d.detail : "Check your preference fields."); }
        setComplete(true); setEditing(false);
      } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
    }}>
      <fieldset disabled={busy}>
        <label>Your name (optional)<input value={name} onChange={e => setName(e.target.value)} /></label>
        <label>Target roles, separated by commas<input required value={roles} onChange={e => setRoles(e.target.value)} placeholder="Product Manager, Data Product Manager" /></label>
        <label>Country codes, separated by commas<input required value={countries} onChange={e => setCountries(e.target.value)} placeholder="US, GB, ZA" /></label>
        <label>Preferred cities (optional)<input value={cities} onChange={e => setCities(e.target.value)} /></label>
        <label>Do you require an H-1B transfer?<select value={transfer} onChange={e => setTransfer(e.target.value)}><option value="unknown">Not specified</option><option value="true">Yes</option><option value="false">No</option></select></label>
        <p>Paid analysis uses your own OPENAI_API_KEY environment variable. Nothing is sent for analysis during setup.</p>
        {error && <p role="alert">{error}</p>}
        <button type="submit">{busy ? "Saving…" : "Save preferences"}</button>
        {complete && <button type="button" onClick={() => setEditing(false)}>Cancel</button>}
      </fieldset>
    </form>
  </main>;
}
