import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8000";

async function api(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      ...(options.body instanceof FormData || options.body instanceof Blob ? {} : { "Content-Type": "application/json" }),
      ...options.headers,
    },
  });
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || "Request failed");
  return response.status === 204 ? null : response.json();
}

function App() {
  const [token, setToken] = useState(localStorage.getItem("clouddrive-token"));
  const [files, setFiles] = useState([]);
  const [error, setError] = useState("");
  const [mode, setMode] = useState("login");
  const [credentials, setCredentials] = useState({ email: "", password: "", name: "" });

  const loadFiles = () => api("/files", { headers: { Authorization: `Bearer ${token}` } })
    .then(setFiles).catch((e) => setError(e.message));
  useEffect(() => { if (token) loadFiles(); }, [token]);

  async function authenticate(event) {
    event.preventDefault();
    try {
      const result = await api(`/auth/${mode}`, {
        method: "POST",
        body: JSON.stringify(credentials),
      });
      localStorage.setItem("clouddrive-token", result.access_token);
      setToken(result.access_token);
      setError("");
    } catch (e) { setError(e.message); }
  }

  async function upload(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      await api(`/files?filename=${encodeURIComponent(file.name)}`, {
        method: "POST",
        body: file,
        headers: { Authorization: `Bearer ${token}`, "Content-Type": file.type || "application/octet-stream" },
      });
      await loadFiles();
    } catch (e) { setError(e.message); }
    event.target.value = "";
  }

  async function download(file) {
    const response = await fetch(`${API}/files/${file.id}/download`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) { setError("Download failed"); return; }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = file.name;
    link.click();
    URL.revokeObjectURL(url);
  }

  if (!token) return <main className="shell"><h1>CloudDrive</h1><form onSubmit={authenticate} className="card">
    <h2>{mode === "login" ? "Sign in" : "Create account"}</h2>
    {mode === "register" && <input placeholder="Name" value={credentials.name} onChange={(e) => setCredentials({ ...credentials, name: e.target.value })} required />}
    <input type="email" placeholder="Email" value={credentials.email} onChange={(e) => setCredentials({ ...credentials, email: e.target.value })} required />
    <input type="password" placeholder="Password" minLength="8" value={credentials.password} onChange={(e) => setCredentials({ ...credentials, password: e.target.value })} required />
    <button>{mode === "login" ? "Sign in" : "Register"}</button>
    <button type="button" className="secondary" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}>
      {mode === "login" ? "Create an account" : "Already have an account?"}
    </button>
    {error && <p className="error">{error}</p>}
  </form></main>;

  return <main className="shell"><header><div><p className="eyebrow">CLOUD FILE SHARING</p><h1>Your files</h1></div>
    <button className="secondary" onClick={() => { localStorage.removeItem("clouddrive-token"); setToken(null); }}>Sign out</button></header>
    <label className="upload">Upload a file<input type="file" onChange={upload} /></label>
    {error && <p className="error">{error}</p>}
    <section className="files">{files.map((file) => <article className="card file" key={file.id}>
      <div><strong>{file.name}</strong><small>{Math.ceil(file.size_bytes / 1024)} KB · {file.access}</small></div>
      <button className="secondary" onClick={() => download(file)}>Download</button>
    </article>)}</section>
  </main>;
}

createRoot(document.getElementById("root")).render(<StrictMode><App /></StrictMode>);
