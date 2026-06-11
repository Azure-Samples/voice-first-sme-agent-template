// Agent Template — local dev runner.
//
// Reads settings.json, mirrors what ./deploy would inject into the
// container, and starts uvicorn (port 8000) + vite (port 5173) locally.
// No Azure deploy required — this is for "I want to see the UI on my
// laptop" before pushing.
//
// Usage:
//   ./local              start both servers
//   ./local --no-auth    skip MSAL on the backend (default for local)
//   ./local --frontend   start only vite
//   ./local --backend    start only uvicorn

import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname  = path.dirname(__filename);

const PROJECT_ROOT  = process.cwd();
const SETTINGS_PATH = path.join(PROJECT_ROOT, 'settings.json');
const ENV_PATH      = path.join(PROJECT_ROOT, '.env');
const IS_WIN        = process.platform === 'win32';

const args = process.argv.slice(2);
const FLAG_FRONTEND_ONLY = args.includes('--frontend');
const FLAG_BACKEND_ONLY  = args.includes('--backend');

// ---------- helpers ----------
function log(msg)  { console.log(msg); }
function step(msg) { console.log(`\n==> ${msg}`); }
function ok(msg)   { console.log(`    OK  ${msg}`); }
function warn(msg) { console.log(`    !   ${msg}`); }
function die(msg, code = 1) { console.error(`\nERROR: ${msg}`); process.exit(code); }

// Load .env into process.env (does not overwrite existing values).
function loadDotEnv() {
    if (!fs.existsSync(ENV_PATH)) return;
    const text = fs.readFileSync(ENV_PATH, 'utf8');
    for (const rawLine of text.split(/\r?\n/)) {
        const line = rawLine.trim();
        if (!line || line.startsWith('#')) continue;
        const eq = line.indexOf('=');
        if (eq < 0) continue;
        const key = line.slice(0, eq).trim();
        if (!key || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) continue;
        if (process.env[key] !== undefined) continue;
        let val = line.slice(eq + 1).trim();
        if ((val.startsWith('"') && val.endsWith('"')) ||
            (val.startsWith("'") && val.endsWith("'"))) {
            val = val.slice(1, -1);
        }
        process.env[key] = val;
    }
}

function readSettings() {
    if (!fs.existsSync(SETTINGS_PATH)) {
        die(`No settings.json at ${SETTINGS_PATH}. Run ./setup first.`);
    }
    try { return JSON.parse(fs.readFileSync(SETTINGS_PATH, 'utf8')); }
    catch (e) { die(`Failed to parse ${SETTINGS_PATH}: ${e.message}`); }
}

function humanize(slug) {
    if (!slug) return 'Voice Agent';
    return slug.split(/[-_]/).filter(Boolean)
        .map(w => w.charAt(0).toUpperCase() + w.slice(1))
        .join(' ');
}

function which(cmd) {
    const finder = IS_WIN ? 'where' : 'which';
    const r = spawnSync(`${finder} ${cmd}`, { encoding: 'utf8', shell: true });
    if (r.status !== 0) return null;
    return (r.stdout || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean)[0] || null;
}

// ---------- prepare frontend env ----------
function writeFrontendEnvLocal(s) {
    const title       = (s.agent && s.agent.title)        || humanize(s.projectName);
    const description = (s.agent && s.agent.description)  || '';
    const description2= (s.agent && s.agent.description2) || '';
    const buttonLabel = (s.agent && s.agent.buttonLabel)  || '';
    const body =
        `VITE_MSAL_CLIENT_ID=${(s.msal && s.msal.clientId) || ''}\n` +
        `VITE_MSAL_TENANT_ID=${(s.msal && s.msal.tenantId) || ''}\n` +
        `VITE_MSAL_REDIRECT_URI=http://localhost:5173/\n` +
        `VITE_MSAL_TRIBE_CLIENT_ID=${(s.msal && s.msal.clientId) || ''}\n` +
        `VITE_MSAL_TRIBE_TENANT_ID=${(s.msal && s.msal.tenantId) || ''}\n` +
        `VITE_APP_TITLE=${title}\n` +
        `VITE_APP_DESCRIPTION=${description}\n` +
        `VITE_APP_DESCRIPTION_2=${description2}\n` +
        `VITE_APP_BUTTON_LABEL=${buttonLabel}\n`;
    fs.writeFileSync(path.join(PROJECT_ROOT, 'frontend', '.env.local'), body, 'utf8');
}

// ---------- backend env ----------
function backendEnv(s) {
    const env = { ...process.env };
    env.STAGE = 'dev';
    // Default to no-auth locally so users can preview the UI without
    // having a working MSAL app reg. Override by exporting REQUIRE_AUTH=true.
    if (env.REQUIRE_AUTH === undefined) env.REQUIRE_AUTH = 'false';
    env.ENABLE_DOCS = 'true';
    env.MSAL_CLIENT_ID = (s.msal && s.msal.clientId) || '';
    env.MSAL_TENANT_ID = (s.msal && s.msal.tenantId) || '';
    env.PROJECT_NAME = s.projectName;
    env.AGENT_GREETING = (s.agent && s.agent.greeting) || '';
    env.SYSTEM_PROMPT_FILE = path.join('agents', s.projectName, 'system_prompt.txt');
    env.KNOWLEDGE_BASE_FILE = path.join('agents', s.projectName, 'knowledge_base.json');
    // Voice Live: optional locally. If absent, the lifespan TTS fetch
    // will fail soft (it logs and continues; the landing page still
    // renders).
    if (s.voiceLive && s.voiceLive.endpoint) {
        env.AZURE_VOICELIVE_ENDPOINT = s.voiceLive.endpoint;
    }
    if (s.voiceLive && s.voiceLive.model) {
        env.AZURE_VOICELIVE_MODEL = s.voiceLive.model;
    }
    // Standard voice picked in setup. Backend uses it for the live
    // Voice Live session (via /api/voice-config) AND for pre-generated
    // filler / nudge / greeting audio (init_voice_audio).
    if (s.agent && s.agent.voice) {
        env.AZURE_VOICELIVE_VOICE = s.agent.voice;
    }

    // Personal Voice: only inject when the feature is on AND the
    // operator has pasted a Speaker Profile ID. Always delete first so
    // a stale value from the parent shell can't shadow a disabled /
    // not-yet-configured feature.
    delete env.AZURE_PERSONAL_VOICE_PROFILE_ID;
    if (s.features && s.features.customVoice && s.customVoice && s.customVoice.profileId) {
        env.AZURE_PERSONAL_VOICE_PROFILE_ID = s.customVoice.profileId;
    }

    // RAG: point local backend at the deployed Search service so you
    // can iterate on Python code without redeploying. DefaultAzureCredential
    // will use your `az login` identity — make sure it has Search Index
    // Data Reader on the search service. (We DON'T set AZURE_CLIENT_ID
    // here — that would force DefaultAzureCredential to look for a UAMI
    // that only exists in Azure.)
    if (s.features && s.features.rag && s.deploy) {
        const searchSvc = (s.deploy.searchEndpoint) ||
            (s.deploy.ragSearchEndpoint) || '';
        if (searchSvc) {
            env.AZURE_SEARCH_ENDPOINT = searchSvc;
            env.AZURE_SEARCH_INDEX_NAME = 'rag-index';
            env.AZURE_SEARCH_SEMANTIC_CONFIG = 'default';
        }
    }
    return env;
}

// ---------- runners ----------
function pickPython() {
    // Prefer real python, skip the WindowsApps stub.
    if (IS_WIN) {
        const r = spawnSync('where python', { encoding: 'utf8', shell: true });
        const cands = (r.stdout || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean);
        const real = cands.find(p => !/WindowsApps/i.test(p));
        if (real) return real;
        return cands[0] || null;
    }
    return which('python3') || which('python');
}

function ensureBackendDeps(py) {
    step('Installing backend deps');
    const r = spawnSync(py, ['-m', 'pip', 'install', '--user', '-q', '-r', 'requirements.txt'], {
        cwd: path.join(PROJECT_ROOT, 'backend'),
        stdio: 'inherit',
    });
    if (r.status !== 0) die('pip install failed');
    ok('backend deps ready');
}

function ensureFrontendDeps() {
    const nm = path.join(PROJECT_ROOT, 'frontend', 'node_modules');
    const viteBin = path.join(nm, 'vite', 'bin', 'vite.js');
    if (fs.existsSync(viteBin)) { ok('frontend deps ready (cached)'); return; }
    step('Installing frontend deps');
    // Run via cmd /c on Windows; bare npm on POSIX. Passing the whole
    // command as a single string to `shell: true` is the most reliable
    // way to invoke npm.cmd on Windows.
    const cmdline = IS_WIN ? 'npm install --no-audit --no-fund' : 'npm install --no-audit --no-fund';
    const r = spawnSync(cmdline, {
        cwd: path.join(PROJECT_ROOT, 'frontend'),
        stdio: 'inherit',
        shell: true,
    });
    if (r.status !== 0) {
        die(`npm install failed (exit ${r.status}). Try running 'npm install' manually inside frontend/ to see the error.`);
    }
    ok('frontend deps ready');
}

function startBackend(env, py) {
    return spawn(py, ['-m', 'uvicorn', 'app.main:app', '--reload', '--port', '8000'], {
        cwd: path.join(PROJECT_ROOT, 'backend'),
        env,
        stdio: 'inherit',
    });
}

function startFrontend() {
    // Run vite directly via node — avoids npm/shell quoting issues on
    // Windows where `spawn('npm.cmd', [...], { shell: true })` can lose
    // args silently. Same pattern as scripts/dev.ps1.
    const viteBin = path.join(
        PROJECT_ROOT, 'frontend', 'node_modules', 'vite', 'bin', 'vite.js'
    );
    if (!fs.existsSync(viteBin)) {
        die(`vite not found at ${viteBin}. Run 'npm install' in frontend/ and retry.`);
    }
    const node = process.execPath;
    return spawn(node, [viteBin, '--host', '127.0.0.1', '--port', '5173'], {
        cwd: path.join(PROJECT_ROOT, 'frontend'),
        stdio: 'inherit',
    });
}

// ---------- main ----------
loadDotEnv();
const s = readSettings();

log('');
log('================================================================');
log('  Agent Template — Local dev');
log('================================================================');
log(`  Project:  ${s.projectName}`);
log(`  Title:    ${(s.agent && s.agent.title) || humanize(s.projectName)}`);
log('');
log('  >>> Open the app at:  http://localhost:5173  <<<');
log('     (the backend runs on :8000 and is proxied through vite)');
log('');
log('  Press Ctrl+C to stop both servers.');
log('');

const py = pickPython();
if (!FLAG_FRONTEND_ONLY && !py) die('Python not found on PATH. Install Python 3.12+ and retry.');

writeFrontendEnvLocal(s);
ok('wrote frontend/.env.local from settings.json');

const env = backendEnv(s);
if (!env.AZURE_VOICELIVE_API_KEY) {
    warn('AZURE_VOICELIVE_API_KEY not set — voice will be disabled, UI will still render.');
}

const procs = [];
if (!FLAG_FRONTEND_ONLY) { ensureBackendDeps(py); procs.push(['backend',  startBackend(env, py)]); }
if (!FLAG_BACKEND_ONLY)  { ensureFrontendDeps();  procs.push(['frontend', startFrontend()]); }

let stopping = false;
function stopAll(signal = 'SIGINT') {
    if (stopping) return;
    stopping = true;
    for (const [, p] of procs) {
        try { p.kill(signal); } catch {}
    }
}
process.on('SIGINT',  () => stopAll('SIGINT'));
process.on('SIGTERM', () => stopAll('SIGTERM'));

let exiting = 0;
for (const [name, p] of procs) {
    p.on('exit', (code) => {
        if (!stopping) {
            console.log(`\n[${name}] exited with code ${code}; stopping the other process.`);
            stopAll();
        }
        exiting++;
        if (exiting === procs.length) process.exit(code || 0);
    });
}
