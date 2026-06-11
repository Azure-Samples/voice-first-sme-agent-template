// Agent Template — deploy.
//
// Fully static. No interactive prompts. Reads settings.json, validates,
// builds the image, runs .azure/main.bicep, registers the deployed URL
// with Entra. If anything's missing in settings.json, fails fast with a
// pointer to ./setup.
//
// Env vars:
//   AZURE_VOICELIVE_API_KEY  — only required when settings.voiceLive.authMode === 'key'.
//                              For 'mi' (managed identity) mode, no key is needed.
//                              We never persist this value in settings.json.
//
// CLI flags:
//   --skip-build       skip ACR image build (faster redeploys when only
//                      infra changed)
//   --skip-infra       skip the bicep deployment (faster code-only push)
//   --skip-preflight   skip the local app smoke-test (don't recommend)
//   --search-only      only re-run the AI Search pipeline setup
//                      (skillset/index/indexer). No bicep, no build, no
//                      container update. Iteration loop for tweaking
//                      RAG config. ~30 seconds.

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname  = path.dirname(__filename);

const PROJECT_ROOT  = process.cwd();
const SETTINGS_PATH = path.join(PROJECT_ROOT, 'settings.json');
const MAIN_BICEP    = path.join(PROJECT_ROOT, '.azure', 'main.bicep');
const ENV_PATH      = path.join(PROJECT_ROOT, '.env');
const VERSION_PATH  = path.join(PROJECT_ROOT, 'VERSION');

// Load .env into process.env (does not overwrite existing values).
// Minimal parser — handles KEY=value, quotes, and # comments. We keep
// it inline so we don't pull in a dotenv dependency.
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
loadDotEnv();

const args = process.argv.slice(2);
const FLAG_SKIP_BUILD     = args.includes('--skip-build');
const FLAG_SKIP_INFRA     = args.includes('--skip-infra');
const FLAG_SKIP_PREFLIGHT = args.includes('--skip-preflight');
const FLAG_SEARCH_ONLY    = args.includes('--search-only');

const IS_WIN = process.platform === 'win32';

// ---------- output helpers ----------
function log(msg)  { console.log(msg); }
function step(msg) { console.log(`\n==> ${msg}`); }
function ok(msg)   { console.log(`    OK  ${msg}`); }
function warn(msg) { console.log(`    !   ${msg}`); }
function die(msg, code = 1) { console.error(`\nERROR: ${msg}`); process.exit(code); }

// ---------- az helpers ----------
function resolveAz() {
    const cmd = IS_WIN ? 'where az' : 'which az';
    const res = spawnSync(cmd, { encoding: 'utf8', shell: true });
    if (res.status !== 0) return null;
    const cands = (res.stdout || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    if (IS_WIN) return cands.find(p => /\.(cmd|bat|exe)$/i.test(p)) || cands[0] || null;
    return cands[0] || null;
}
const AZ_PATH = resolveAz();

function winQuote(arg) {
    if (!IS_WIN) return arg;
    if (!/[\s"&|<>^()%!,;=]/.test(arg)) return arg;
    return `"${String(arg).replace(/"/g, '\\"')}"`;
}

function spawnAz(args, opts = {}) {
    if (IS_WIN) {
        const quoted = args.map(winQuote).join(' ');
        return spawnSync(`"${AZ_PATH}" ${quoted}`, { ...opts, shell: true });
    }
    return spawnSync(AZ_PATH, args, opts);
}

function az(args, { allowFail = false } = {}) {
    if (!AZ_PATH) die('Azure CLI (`az`) not found on PATH. Install az CLI then re-run.');
    const res = spawnAz(args, { encoding: 'utf8' });
    if (res.status !== 0) {
        if (allowFail) return { ok: false, stdout: res.stdout || '', stderr: res.stderr || '' };
        die(`az ${args.join(' ')} failed:\n${(res.stderr || res.stdout || '').trim()}`);
    }
    return { ok: true, stdout: res.stdout || '', stderr: res.stderr || '' };
}

function azJson(args) {
    const r = az([...args, '-o', 'json'], { allowFail: true });
    if (!r.ok) return null;
    try { return JSON.parse(r.stdout); } catch { return null; }
}

function azStream(args) {
    const res = spawnAz(args, { stdio: 'inherit' });
    if (res.status !== 0) die(`az ${args.join(' ')} failed (exit ${res.status})`);
}

// ---------- version + git ----------
// Read the template version from the VERSION file at repo root. The same
// value is baked into the container image (Dockerfile ARG) and surfaced via
// GET /api/health. Falls back to "0.0.0" if the file is missing so old
// checkouts still deploy, but the image tag will look obviously wrong.
function readVersionFile() {
    try {
        const v = fs.readFileSync(VERSION_PATH, 'utf8').trim();
        return v || '0.0.0';
    } catch {
        warn(`VERSION file not found at ${VERSION_PATH}; using 0.0.0`);
        return '0.0.0';
    }
}

// Short git SHA for the current HEAD. Used as the image tag suffix and as
// the GIT_SHA build arg. Returns "nogit" if git isn't available or the
// repo isn't initialized — the build still succeeds, the tag just won't
// be uniquely tied to a commit.
function getGitShortSha() {
    const res = spawnSync('git', ['rev-parse', '--short', 'HEAD'], {
        cwd: PROJECT_ROOT, encoding: 'utf8',
    });
    if (res.status !== 0) return 'nogit';
    return (res.stdout || '').trim() || 'nogit';
}

function getGitStatusEntries() {
    const res = spawnSync('git', ['status', '--porcelain'], {
        cwd: PROJECT_ROOT, encoding: 'utf8',
    });
    if (res.status !== 0) return null;
    return (res.stdout || '').split(/\r?\n/).filter(Boolean);
}

// Returns true if `git status --porcelain` reports any uncommitted change.
// Returns false when git is unavailable or the worktree is clean — callers
// surface that as "unknown clean" rather than failing the deploy.
function isWorktreeDirty() {
    const entries = getGitStatusEntries();
    return Array.isArray(entries) && entries.length > 0;
}

function warnIfDirtyWorktree(gitSha) {
    if (!isWorktreeDirty()) return;
    const entries = getGitStatusEntries() || [];

    const plural = entries.length === 1 ? '' : 's';
    warn(`Working tree has ${entries.length} uncommitted change${plural}; image tag uses HEAD (${gitSha}) but ACR build includes local files.`);
    warn('Commit or stash before deploy if you need the version label to match the exact image contents.');

    const sample = entries.slice(0, 5).map(line => line.slice(3)).join(', ');
    if (sample) {
        log(`    Changed: ${sample}${entries.length > 5 ? ', ...' : ''}`);
    }
}

// Resolve the digest for an image tag in ACR. Returns the full immutable
// reference "<acr>.azurecr.io/<repo>@<digest>" so we can pin the Container
// App revision to a specific build (enables exact rollback).
function resolveImageDigest(acrName, repoName, tag) {
    // `az acr repository show-manifests` returns the manifest list; we
    // filter to entries whose tags contain the version-sha tag we just
    // pushed and take its digest.
    const manifests = azJson([
        'acr', 'repository', 'show-manifests',
        '--name', acrName,
        '--repository', repoName,
    ]);
    if (!Array.isArray(manifests)) return null;
    const match = manifests.find(m => Array.isArray(m && m.tags) && m.tags.includes(tag));
    if (!match || !match.digest) return null;
    return `${acrName}.azurecr.io/${repoName}@${match.digest}`;
}

// ---------- settings ----------
function readSettings() {
    if (!fs.existsSync(SETTINGS_PATH)) {
        die(`No settings.json at ${SETTINGS_PATH}. Run ./setup first.`);
    }
    try {
        return JSON.parse(fs.readFileSync(SETTINGS_PATH, 'utf8'));
    } catch (e) {
        die(`Failed to parse ${SETTINGS_PATH}: ${e.message}`);
    }
}

function writeSettings(s) {
    fs.writeFileSync(SETTINGS_PATH, JSON.stringify(s, null, 2) + '\n', 'utf8');
}

function requireField(obj, dotted, hint) {
    const parts = dotted.split('.');
    let cur = obj;
    for (const p of parts) {
        if (!cur || cur[p] === undefined || cur[p] === null || cur[p] === '') {
            die(`Missing settings.${dotted} — ${hint}\nRun ./setup to fix.`);
        }
        cur = cur[p];
    }
    return cur;
}

function validateSettings(s) {
    requireField(s, 'projectName', 'agent project name');
    requireField(s, 'azure.subscriptionId', 'pick a subscription in setup');
    requireField(s, 'azure.resourceGroup', 'pick a resource group in setup');
    requireField(s, 'azure.location', 'pick a region in setup');
    requireField(s, 'azure.nameSuffix', 'random suffix should have been generated by setup');
    requireField(s, 'msal.clientId', 'configure Entra app reg in setup');
    requireField(s, 'msal.tenantId', 'configure Entra app reg in setup');
    requireField(s, 'voiceLive.mode', 'choose Voice Live mode in setup');
    if (s.voiceLive.mode === 'existing') {
        requireField(s, 'voiceLive.endpoint', 'paste the existing Voice Live endpoint in setup');
    }
    // authMode is new — default to 'key' for backwards compatibility with
    // any settings.json written by an older setup wizard.
    if (!s.voiceLive.authMode) s.voiceLive.authMode = 'key';
    if (!s.voiceLive.model) s.voiceLive.model = 'gpt-realtime';
    if (!s.voiceLive.modelVersion) s.voiceLive.modelVersion = '2025-08-28';
    if (s.voiceLive.authMode === 'mi' && s.voiceLive.mode === 'existing') {
        // Required to scope the cross-RG role assignment in Bicep.
        requireField(s, 'voiceLive.accountName', 're-run setup; the account name is parsed from the endpoint URL');
        requireField(s, 'voiceLive.resourceGroup', 're-run setup; the account RG is auto-detected or prompted');
    }
    if (s.features && s.features.customDomain) {
        requireField(s, 'customDomain.name', 'set custom domain name in setup');
    }
    if (s.features && s.features.customVoice && !(s.customVoice && s.customVoice.profileId)) {
        warn('features.customVoice is on but customVoice.profileId is empty — deploy will succeed but the agent will keep using the standard Azure voice. Paste your Speaker Profile ID into settings.json and re-run ./deploy to activate.');
    }
}

// ---------- naming derivation ----------
function cosmosTtlSeconds(s) {
    const days = (s.transcripts && Number.isFinite(s.transcripts.retentionDays))
        ? s.transcripts.retentionDays : 30;
    if (days <= 0) return -1;
    return days * 86400;
}

function deriveNames(s, { templateVersion, gitSha }) {
    const slug = s.projectName.replace(/[^a-z0-9]/g, '').toLowerCase();
    const suffix = s.azure.nameSuffix;
    // Image tag is <version>-<sha> so every build is uniquely identifiable.
    // Rollback works via the resolved digest reference (see resolveImageDigest).
    const imageTag = `${templateVersion}-${gitSha}`;
    return {
        acrName:           `${slug}acr${suffix}`.slice(0, 50),
        envName:           `${s.projectName}-env`,
        logAnalyticsName:  `${s.projectName}-logs`,
        appIdentityName:   `${slug}-pull`,
        voiceLiveAccount:  `${s.projectName}-ai-${suffix}`,
        searchServiceName: `${slug}srch${suffix}`.slice(0, 60),
        // Storage names: 3-24 lowercase alphanumeric, globally unique.
        ragStorageAccount: `${slug}stor${suffix}`.replace(/[^a-z0-9]/g, '').slice(0, 24),
        // OpenAI embeddings account name: 2-64 chars, alphanumeric + hyphen.
        ragEmbeddingsAccount: `${slug}-emb-${suffix}`.slice(0, 64),
        ragContainerName:  'rag-documents',
        searchIndexName:   'rag-index',
        searchDataSourceName: 'rag-datasource',
        searchSkillsetName: 'rag-skillset',
        searchIndexerName: 'rag-indexer',
        searchSemanticConfig: 'default',
        // Cosmos account name: 3-44 chars, lowercase letters/digits/hyphens.
        cosmosAccountName: `${slug}-cosmos-${suffix}`.slice(0, 44),
        imageRepo:         s.projectName,
        imageTag,
        imageName:         `${s.projectName}:${imageTag}`,
    };
}

// ---------- preflight ----------
function checkPrereqs() {
    step('Preflight');
    if (!AZ_PATH) die('Azure CLI not found. Install from https://learn.microsoft.com/cli/azure/install-azure-cli');
    const acct = azJson(['account', 'show']);
    if (!acct) die('Not logged in to Azure. Run `az login` and retry.');
    ok(`logged in as ${acct.user && acct.user.name}`);
    if (!fs.existsSync(MAIN_BICEP)) die(`.azure/main.bicep not found at ${MAIN_BICEP}`);
}

function ensureSubscription(subId) {
    az(['account', 'set', '--subscription', subId]);
    ok(`subscription ${subId} active`);
}

function ensureResourceGroup(rg, location) {
    step(`Resource group: ${rg}`);
    const existing = azJson(['group', 'show', '--name', rg]);
    if (existing) {
        const state = existing.properties && existing.properties.provisioningState;
        if (state === 'Deleting') {
            die(`Resource group '${rg}' is being deleted. Wait for it to finish, then retry.`);
        }
        ok(`exists (${existing.location})`);
        return;
    }
    log(`    creating in ${location}...`);
    az(['group', 'create', '--name', rg, '--location', location]);
    ok('created');
}

// Resolve the principal running this deploy so bicep can grant it the
// Storage Blob Data Contributor role on the RAG storage account. We
// can't use shared-key access (tenant policy + bicep disables it) so
// the user needs an RBAC data-plane role to upload PDFs via Storage
// Explorer or the portal.
//
// Returns { objectId, principalType, userName } or all-empty on
// failure. Best-effort — never fatal.
function resolveDeployer() {
    const empty = { objectId: '', principalType: 'User', userName: '' };
    const acct = azJson(['account', 'show']);
    if (!acct) return empty;

    const userType = (acct.user && acct.user.type) || 'user';
    const userName = (acct.user && acct.user.name) || '';

    if (userType === 'user') {
        // Interactive `az login` — query the signed-in user from Graph.
        const me = azJson(['ad', 'signed-in-user', 'show']);
        if (me && me.id) {
            return { objectId: me.id, principalType: 'User', userName: me.userPrincipalName || me.mail || userName };
        }
        return { ...empty, userName };
    }

    if (userType === 'servicePrincipal') {
        // CI / SP login — look up the SP by its appId.
        const sp = azJson(['ad', 'sp', 'show', '--id', userName]);
        if (sp && sp.id) {
            return { objectId: sp.id, principalType: 'ServicePrincipal', userName: sp.displayName || userName };
        }
        return { ...empty, principalType: 'ServicePrincipal', userName };
    }

    return empty;
}

// ---------- bicep deploy ----------
// Submit + read outputs separately. Works around az CLI bug #29226 where
// combining `--query` with `deployment group create` intermittently fails
// after the deployment has already succeeded.
function submitDeployment({ name, resourceGroup, templateFile, params }) {
    step('Deploying infrastructure');
    const createArgs = [
        'deployment', 'group', 'create',
        '--name', name,
        '--resource-group', resourceGroup,
        '--template-file', templateFile,
        '--parameters', ...params,
        '--only-show-errors',
        '-o', 'none',
    ];
    log(`    az deployment group create ... (this can take a few minutes)`);
    const createRes = spawnAz(createArgs, { encoding: 'utf8' });
    if (createRes.status !== 0) {
        const realError = tryReadDeploymentError(name, resourceGroup);
        if (realError) die(`Deployment ${name} failed:\n  ${realError}${cleanupHint(resourceGroup)}`);
        const preflight = tryExtractPreflightError(createArgs);
        if (preflight) die(`Deployment ${name} failed:\n  ${preflight}${cleanupHint(resourceGroup)}`);
        die(`az deployment failed:\n${(createRes.stderr || createRes.stdout || '').trim()}${cleanupHint(resourceGroup)}`);
    }
    const result = azJson([
        'deployment', 'group', 'show',
        '--name', name,
        '--resource-group', resourceGroup,
    ]);
    if (!result) die(`Could not read deployment ${name} after submit.${cleanupHint(resourceGroup)}`);
    const state = result.properties && result.properties.provisioningState;
    if (state !== 'Succeeded') {
        const realError = tryReadDeploymentError(name, resourceGroup);
        die(`Deployment ${name} ended in state: ${state}${realError ? '\n  ' + realError : ''}${cleanupHint(resourceGroup)}`);
    }
    const flat = {};
    for (const [k, v] of Object.entries(result.properties.outputs || {})) flat[k] = v && v.value;
    ok('infrastructure deployed');
    return flat;
}

// Suffix appended to bicep failure messages so the user knows the
// one-liner to wipe a half-provisioned RG and start over. Only emitted
// when we have a resource group name to point at.
function cleanupHint(resourceGroup) {
    if (!resourceGroup) return '';
    return `\n\nTo wipe this attempt and start fresh:\n` +
           `  az group delete --name ${resourceGroup} --yes --no-wait\n` +
           `Then re-run ./deploy after fixing the issue above.`;
}

function tryReadDeploymentError(name, rg) {
    const ops = azJson([
        'deployment', 'operation', 'group', 'list',
        '--name', name, '--resource-group', rg,
    ]);
    if (!Array.isArray(ops)) return null;
    const failed = ops.find(o => o && o.properties && o.properties.provisioningState === 'Failed' && o.properties.statusMessage);
    if (!failed) return null;
    const sm = failed.properties.statusMessage;
    if (!sm || !sm.error) return null;
    const e = sm.error;
    const parts = [e.code, e.message].filter(Boolean);
    if (Array.isArray(e.details)) for (const d of e.details) parts.push(`  - ${d.code || ''}: ${d.message || ''}`);
    return parts.join('\n  ');
}

function tryExtractPreflightError(createArgs) {
    const debugArgs = createArgs.filter(a => a !== '-o' && a !== 'none' && a !== '--only-show-errors');
    debugArgs.push('--debug');
    const res = spawnAz(debugArgs, { encoding: 'utf8' });
    const out = (res.stderr || '') + (res.stdout || '');
    if (!out) return null;
    const m = out.match(/azure\.core\.exceptions\.[A-Za-z]+:\s*(\([^)]+\)[^\r\n]+)/);
    if (m) return m[1].trim();
    const m2 = out.match(/\(([A-Z][A-Za-z]+)\)\s+([^\r\n]+)/);
    if (m2) return `(${m2[1]}) ${m2[2].trim()}`;
    return null;
}

// ---------- preflight: catch backend/frontend bugs before pushing ----------
//
// We've been burned by import-time crashes that only surface after a
// 10-minute ACR build + Container App rollout (e.g. malformed
// knowledge_base.json making `for doc in DOCUMENTS` iterate over dict
// keys). Run cheap local checks first.
function runAppPreflight(s) {
    if (FLAG_SKIP_PREFLIGHT) { warn('--skip-preflight set, skipping'); return; }
    step('Preflighting app code (catches startup crashes before deploy)');

    // 1) Backend: import app.main with the same env vars the container
    //    sees. Catches FileNotFoundError, malformed JSON, bad imports.
    const backendDir = path.join(PROJECT_ROOT, 'backend');
    if (fs.existsSync(path.join(backendDir, 'app', 'main.py'))) {
        const py = IS_WIN ? 'python' : 'python3';
        const projectName = s.projectName;
        const sysPromptFile = (s.agent && s.agent.systemPromptFile)
            || path.posix.join('agents', projectName, 'system_prompt.txt');
        const kbFile = (s.agent && s.agent.knowledgeBaseFile)
            || path.posix.join('agents', projectName, 'knowledge_base.json');
        // Run from PROJECT_ROOT so relative paths resolve like in container.
        const env = {
            ...process.env,
            PYTHONPATH: backendDir,
            SYSTEM_PROMPT_FILE: sysPromptFile,
            KNOWLEDGE_BASE_FILE: kbFile,
            PROJECT_NAME: projectName,
            AGENT_GREETING: (s.agent && s.agent.greeting) || '',
            // Stub creds so config.py validators don't bail.
            AZURE_VOICELIVE_API_KEY: process.env.AZURE_VOICELIVE_API_KEY || 'preflight-stub-key-1234567890',
            VOICE_LIVE_ENDPOINT: 'https://preflight.invalid',
            VOICE_LIVE_MODEL: 'gpt-realtime',
            PREFLIGHT: '1',
        };
        const res = spawnSync(py, ['-c', 'import app.main'], {
            cwd: PROJECT_ROOT,
            env,
            encoding: 'utf8',
        });
        if (res.error && res.error.code === 'ENOENT') {
            warn(`${py} not found on PATH; skipping backend import check`);
        } else if (res.status !== 0) {
            const tail = (res.stderr || res.stdout || '').trim();
            log('');
            if (/ModuleNotFoundError|ImportError:\s*No module named/.test(tail)) {
                log('Hint: this looks like a missing Python dependency.');
                log('Install backend deps into a venv and re-run ./deploy:');
                if (IS_WIN) {
                    log('  python -m venv .venv');
                    log('  .\\.venv\\Scripts\\Activate.ps1');
                    log('  pip install -r backend/requirements.txt');
                } else {
                    log('  python3 -m venv .venv');
                    log('  source .venv/bin/activate');
                    log('  pip install -r backend/requirements.txt');
                }
                log('');
            }
            log(tail);
            log('');
            die('Backend failed to import. Fix the error above before redeploying.\n' +
                '  (Pass --skip-preflight to bypass, but the container will likely crash-loop.)');
        } else {
            ok('backend imports cleanly');
        }
    }

    // 2) Frontend: TypeScript typecheck. Catches missing imports, type
    //    errors, etc. that would break `npm run build` inside ACR.
    const frontendDir = path.join(PROJECT_ROOT, 'frontend');
    const pkgJson = path.join(frontendDir, 'package.json');
    if (fs.existsSync(pkgJson) && fs.existsSync(path.join(frontendDir, 'node_modules'))) {
        const pkg = JSON.parse(fs.readFileSync(pkgJson, 'utf8'));
        const scripts = pkg.scripts || {};
        // Prefer an explicit typecheck script if one exists; else fall
        // back to `tsc -b --noEmit` via the local binary.
        let cmd, cmdArgs;
        if (scripts.typecheck) {
            cmd = IS_WIN ? 'npm.cmd' : 'npm';
            cmdArgs = ['run', '--silent', 'typecheck'];
        } else {
            const tscBin = path.join(frontendDir, 'node_modules', '.bin', IS_WIN ? 'tsc.cmd' : 'tsc');
            if (!fs.existsSync(tscBin)) { ok('frontend (no tsc; skipping)'); return; }
            cmd = tscBin;
            cmdArgs = ['-b', '--noEmit'];
        }
        const res = spawnSync(cmd, cmdArgs, { cwd: frontendDir, encoding: 'utf8', shell: IS_WIN });
        if (res.status !== 0) {
            const tail = (res.stdout || res.stderr || '').trim();
            log('');
            log(tail);
            log('');
            die('Frontend typecheck failed. Fix the errors above before redeploying.\n' +
                '  (Pass --skip-preflight to bypass.)');
        }
        ok('frontend typechecks cleanly');
    } else {
        warn('frontend/node_modules missing — skipping typecheck (run `npm install` in frontend/ to enable)');
    }
}

// Resolve the Python binary for host-side rendering. Prefers the project
// venv (where the customer installed jinja2/jsonschema via requirements.txt)
// over the system Python, which may lack the deps or be the wrong version.
function resolveHostPython() {
    const venvPy = IS_WIN
        ? path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe')
        : path.join(PROJECT_ROOT, '.venv', 'bin', 'python3');
    if (fs.existsSync(venvPy)) return venvPy;
    return IS_WIN ? 'python' : 'python3';
}

// ---------- render system_prompt.txt from customization.json ----------
//
// Runs scripts/render.py to regenerate agents/<projectName>/system_prompt.txt
// from agents/<projectName>/customization.json. Ensures the system prompt
// baked into the container reflects the customer's current customization.
//
// Backward compat: if customization.json doesn't exist, skip with a warning
// and let the existing system_prompt.txt flow through. Customers who haven't
// adopted data-first customization still deploy cleanly.
function renderSystemPrompt(s) {
    const projectName = s.projectName;
    const customizationPath = path.join(PROJECT_ROOT, 'agents', projectName, 'customization.json');
    if (!fs.existsSync(customizationPath)) {
        warn(`no customization.json at agents/${projectName}/ — using existing system_prompt.txt`);
        warn('(Run the customize-agent skill to adopt data-first customization.)');
        return;
    }
    step('Rendering system_prompt.txt from customization.json');
    const py = resolveHostPython();
    const res = spawnSync(py, ['scripts/render.py', '--agent', projectName], {
        cwd: PROJECT_ROOT,
        encoding: 'utf8',
    });
    if (res.error && res.error.code === 'ENOENT') {
        const promptPath = path.join(PROJECT_ROOT, 'agents', projectName, 'system_prompt.txt');
        if (fs.existsSync(promptPath)) {
            warn(`${py} not found on PATH — using existing system_prompt.txt without re-rendering.`);
            warn('Install Python 3.11+ and run ./render to ensure the prompt reflects your latest customization.json.');
            return;
        }
        die(`${py} not found on PATH and no system_prompt.txt exists. Install Python 3.11+ and run ./render, then re-run ./deploy.`);
    }
    if (res.status !== 0) {
        const tail = (res.stderr || res.stdout || '').trim();
        log('');
        log(tail);
        log('');
        die('Renderer failed. Fix the error above and re-run ./deploy.\n' +
            '  (If Python deps are missing: pip install -r requirements.txt)');
    }
    const out = (res.stdout || '').trim();
    ok(out || `wrote agents/${projectName}/system_prompt.txt`);
}

// ----------
// ---------- image build ----------
function buildImage(s, names, { templateVersion, gitSha, localChanges }) {
    step('Building container image in ACR');
    if (FLAG_SKIP_BUILD) { warn('--skip-build set, skipping'); return; }

    // Write frontend/.env.production into the build context. We do it this
    // way (rather than passing --build-arg) because ACR's cloud agent runs
    // an unquoted `docker build` and trips over values containing spaces
    // (e.g. `VITE_APP_TITLE=Sales Coach`), failing with "docker build
    // requires exactly 1 argument". Vite's loadEnv() reads .env.production
    // automatically at build time. The .gitignore in this template MUST NOT
    // list this file: `az acr build` honors .gitignore when packing the
    // upload tarball, and any exclusion silently strips it from the build.
    const title = (s.agent && s.agent.title) || humanize(s.projectName);
    const description  = (s.agent && s.agent.description)  || '';
    const description2 = (s.agent && s.agent.description2) || '';
    const buttonLabel  = (s.agent && s.agent.buttonLabel)  || '';
    const envPath = path.join(PROJECT_ROOT, 'frontend', '.env.production');
    const envBody =
        `VITE_MSAL_CLIENT_ID=${s.msal.clientId}\n` +
        `VITE_MSAL_TENANT_ID=${s.msal.tenantId}\n` +
        `VITE_MSAL_REDIRECT_URI=/\n` +
        `VITE_MSAL_TRIBE_CLIENT_ID=${s.msal.clientId}\n` +
        `VITE_MSAL_TRIBE_TENANT_ID=${s.msal.tenantId}\n` +
        `VITE_APP_TITLE=${title}\n` +
        `VITE_APP_DESCRIPTION=${description}\n` +
        `VITE_APP_DESCRIPTION_2=${description2}\n` +
        `VITE_APP_BUTTON_LABEL=${buttonLabel}\n`;
    fs.writeFileSync(envPath, envBody, 'utf8');

    try {
        // --no-logs queues the build, waits for completion, and suppresses
        // the live log streamer. The streamer crashes the local `az` CLI
        // on Windows: it emits U+2713 (✓) which Python's cp1252 stdout
        // encoding can't represent (Azure/azure-cli upstream bug). The
        // build still runs server-side and `az acr build` exits with the
        // run's status. View build logs in the portal if needed:
        //   ACR > Services > Tasks > Runs > <run-id>
        azStream([
            'acr', 'build',
            '--registry', names.acrName,
            '--resource-group', s.azure.resourceGroup,
            '--image', names.imageName,
            '--build-arg', `TEMPLATE_VERSION=${templateVersion}`,
            '--build-arg', `GIT_SHA=${gitSha}`,
            '--build-arg', `LOCAL_CHANGES=${localChanges ? 'true' : 'false'}`,
            '--no-logs',
            PROJECT_ROOT,
        ]);
        ok(`image built and pushed: ${names.imageName}`);
    } finally {
        try { fs.unlinkSync(envPath); } catch {}
    }
}

function humanize(slug) {
    if (!slug) return 'Voice Agent';
    return slug.split(/[-_]/).filter(Boolean)
        .map(w => w.charAt(0).toUpperCase() + w.slice(1))
        .join(' ');
}

// ---------- search indexer (RAG data plane) ----------
//
// Bicep provisions the AI Search service + storage + embeddings account.
// Indexes/datasources/skillsets/indexers are data-plane resources, so
// we configure them here via REST. The deploying user has RG Owner, so
// AAD-based PUTs to the search endpoint succeed.
//
// Pipeline (all server-side, in Azure AI Search):
//
//   blob ─┐
//         │  (1) crack         (2) split       (3) embed         (4) project
//         ▼                                                          ▼
//        indexer ─► extract text ─► chunk pages ─► call OpenAI ─► write rows
//                                                                    │
//                                                                    ▼
//                                                                 vector index
//                                                              (chunk + vector
//                                                               + title + src)
//
// At query time, the index has a `vectorizer` configured so the search
// service embeds the user's query with the same model — no client-side
// embedding code needed.
//
// You drop a PDF/docx/pptx in the storage container; the rest happens
// automatically. Indexer runs on the schedule (5 min) and on demand.
function configureSearchIndexer(s, names, embeddings, opts = {}) {
    const { networkMode = false } = opts;
    step('Configuring AI Search RAG pipeline (skillset + vectorized index)');
    const searchEndpoint = `https://${names.searchServiceName}.search.windows.net`;
    // Skillsets + indexProjections + integrated vectorization need
    // 2024-07-01 or newer. We use 2024-07-01 (GA).
    const apiVersion = '2024-07-01';
    const storageResId = `/subscriptions/${s.azure.subscriptionId}/resourceGroups/${s.azure.resourceGroup}/providers/Microsoft.Storage/storageAccounts/${names.ragStorageAccount}`;

    if (!embeddings || !embeddings.endpoint || !embeddings.deployment) {
        warn('Embeddings endpoint missing from bicep outputs. Skipping search setup.');
        return;
    }

    // Set once we detect that the deployer's egress IP doesn't match what
    // Azure Search sees (typically: corporate NAT / proxy / VPN egresses
    // through a different IP than ipify reports). We print one big
    // actionable hint instead of repeating it for every failed PUT.
    let firewallHintPrinted = false;
    function maybePrintFirewallHint(errBody) {
        if (firewallHintPrinted) return;
        if (!networkMode) return;
        if (!/publicNetworkAccess: Enabled/i.test(errBody) || !/not allowed by applicable rules/i.test(errBody)) return;
        firewallHintPrinted = true;
        const ip = (s.network && s.network.deployerIpAddress) || '<unknown>';
        log('');
        log('    ┌── Search firewall rejected the deployer IP ──────────────────────────');
        log(`    │ Detected deployer IP: ${ip}`);
        log('    │ AI Search has PNA=Enabled with that IP allow-listed, but the request');
        log('    │ is being rejected by the firewall. This usually means traffic to');
        log('    │ Azure egresses through a DIFFERENT IP than what was auto-detected');
        log('    │ (common on corporate networks with proxy / NAT / VPN to Azure).');
        log('    │');
        log('    │ To finish setup:');
        log('    │   1. Open the AI Search resource in the Azure Portal');
        log(`    │        ${names.searchServiceName} (Networking > Firewall)`);
        log('    │   2. Add your *actual* Azure-egress IP to the firewall allow-list,');
        log('    │      or temporarily set "All networks" while you re-run ./deploy');
        log('    │      (the wizard re-locks it to Disabled at the end of pass 2).');
        log('    │   3. Update network.deployerIpAddress in settings.json to that IP.');
        log('    │   4. Re-run ./deploy. The indexer step will pick up where it left off.');
        log('    └──────────────────────────────────────────────────────────────────────');
        log('');
    }

    function searchPut(resourcePath, body, label) {
        const tmp = path.join(os.tmpdir(), `search-${label}-${Date.now()}.json`);
        fs.writeFileSync(tmp, JSON.stringify(body));
        const res = az([
            'rest',
            '--method', 'PUT',
            '--uri', `${searchEndpoint}${resourcePath}?api-version=${apiVersion}`,
            '--resource', 'https://search.azure.com/',
            '--headers', 'Content-Type=application/json',
            '--body', `@${tmp}`,
        ], { allowFail: true });
        try { fs.unlinkSync(tmp); } catch {}
        if (!res.ok) {
            const errBody = (res.stderr || res.stdout || '').trim();
            warn(`${label} setup failed: ${errBody.split('\n').slice(0, 3).join(' | ')}`);
            maybePrintFirewallHint(errBody);
            return false;
        }
        ok(`${label} ready`);
        return true;
    }

    // text-embedding-3-large is 3072-dim. text-embedding-ada-002 = 1536.
    // Keep this in sync with the bicep default (text-embedding-3-large).
    const dims = embeddings.model === 'text-embedding-ada-002' ? 1536 : 3072;

    // 1. Datasource — blob container, Search MSI -> Storage Blob Data Reader.
    //    ResourceId= form is required because the storage account has
    //    shared-key access disabled.
    const dsOk = searchPut(`/datasources/${names.searchDataSourceName}`, {
        name: names.searchDataSourceName,
        type: 'azureblob',
        credentials: { connectionString: `ResourceId=${storageResId};` },
        container: { name: names.ragContainerName },
        dataDeletionDetectionPolicy: {
            '@odata.type': '#Microsoft.Azure.Search.NativeBlobSoftDeleteDeletionDetectionPolicy',
        },
    }, 'datasource');

    // 2. Index — must be created BEFORE the skillset, because the
    //    skillset's indexProjections reference the index and Search
    //    validates that the target index exists at skillset-create time.
    //    Chunked schema with vector field, HNSW algorithm, an index-time
    //    vectorizer (so queries pass plain text and Search embeds it
    //    server-side using the same model), and a semantic config for
    //    the L2 reranker.
    const idxOk = searchPut(`/indexes/${names.searchIndexName}`, {
        name: names.searchIndexName,
        fields: [
            { name: 'chunk_id',  type: 'Edm.String', key: true, retrievable: true, filterable: true, sortable: true, analyzer: 'keyword' },
            { name: 'parent_id', type: 'Edm.String', filterable: true, retrievable: true },
            { name: 'title',     type: 'Edm.String', searchable: true, retrievable: true },
            { name: 'chunk',     type: 'Edm.String', searchable: true, retrievable: true },
            { name: 'source',    type: 'Edm.String', retrievable: true, filterable: true },
            {
                name: 'text_vector',
                type: 'Collection(Edm.Single)',
                searchable: true,
                retrievable: false,
                stored: true,
                dimensions: dims,
                vectorSearchProfile: 'rag-vector-profile',
            },
        ],
        vectorSearch: {
            algorithms: [
                {
                    name: 'rag-hnsw',
                    kind: 'hnsw',
                    hnswParameters: { m: 4, efConstruction: 400, efSearch: 500, metric: 'cosine' },
                },
            ],
            profiles: [
                {
                    name: 'rag-vector-profile',
                    algorithm: 'rag-hnsw',
                    vectorizer: 'rag-vectorizer',
                },
            ],
            vectorizers: [
                {
                    name: 'rag-vectorizer',
                    kind: 'azureOpenAI',
                    azureOpenAIParameters: {
                        resourceUri: embeddings.endpoint,
                        deploymentId: embeddings.deployment,
                        modelName: embeddings.model,
                        // No apiKey -> uses Search MSI.
                    },
                },
            ],
        },
        semantic: {
            configurations: [
                {
                    name: names.searchSemanticConfig,
                    prioritizedFields: {
                        titleField: { fieldName: 'title' },
                        prioritizedContentFields: [{ fieldName: 'chunk' }],
                    },
                },
            ],
        },
    }, 'index');

    // 3. Skillset — Split + AzureOpenAIEmbedding + indexProjections.
    //    SplitSkill chunks the cracked text into ~2000-char overlapping
    //    pages. AzureOpenAIEmbeddingSkill embeds each page using the
    //    Search MSI (granted Cognitive Services OpenAI User on the
    //    embeddings account by the rag-embeddings bicep module).
    //    indexProjections fan out: one document per chunk, writing the
    //    embedding + the chunk text + the parent metadata.
    const skillsetOk = searchPut(`/skillsets/${names.searchSkillsetName}`, {
        name: names.searchSkillsetName,
        description: 'Crack PDFs, split into pages, embed each page.',
        skills: [
            {
                '@odata.type': '#Microsoft.Skills.Text.SplitSkill',
                name: 'split',
                description: 'Split text into pages with overlap.',
                context: '/document',
                textSplitMode: 'pages',
                maximumPageLength: 2000,
                pageOverlapLength: 500,
                inputs: [
                    { name: 'text', source: '/document/content' },
                ],
                outputs: [
                    { name: 'textItems', targetName: 'pages' },
                ],
            },
            {
                '@odata.type': '#Microsoft.Skills.Text.AzureOpenAIEmbeddingSkill',
                name: 'embed',
                description: 'Embed each page with Azure OpenAI.',
                context: '/document/pages/*',
                resourceUri: embeddings.endpoint,
                deploymentId: embeddings.deployment,
                modelName: embeddings.model,
                dimensions: dims,
                // No apiKey or authIdentity -> uses the Search MSI.
                inputs: [
                    { name: 'text', source: '/document/pages/*' },
                ],
                outputs: [
                    { name: 'embedding', targetName: 'text_vector' },
                ],
            },
        ],
        indexProjections: {
            selectors: [
                {
                    targetIndexName: names.searchIndexName,
                    parentKeyFieldName: 'parent_id',
                    sourceContext: '/document/pages/*',
                    mappings: [
                        { name: 'chunk',       source: '/document/pages/*' },
                        { name: 'text_vector', source: '/document/pages/*/text_vector' },
                        { name: 'title',       source: '/document/metadata_storage_name' },
                        { name: 'source',      source: '/document/metadata_storage_path' },
                    ],
                },
            ],
            parameters: {
                // Don't write parent docs to the index — only chunks.
                projectionMode: 'skipIndexingParentDocuments',
            },
        },
    }, 'skillset');

    if (!dsOk || !idxOk || !skillsetOk) {
        warn('Skipping indexer creation due to earlier failures.');
        return;
    }

    // 4. Indexer — runs the skillset, projects to the index. Schedule
    //    every 5 min so newly uploaded blobs are picked up automatically.
    //    Field mappings here apply to the *parent* doc; chunk-level
    //    mappings are in the skillset's indexProjections above.
    //
    //    Network mode (PR 3): the indexer runs in Search's "private"
    //    execution environment so it can reach Storage + Embeddings
    //    through the Search-managed Shared Private Links. Without
    //    this, the indexer runs in the shared multi-tenant fleet and
    //    can't see the SPL-protected resources.
    const indexerBody = {
        name: names.searchIndexerName,
        dataSourceName: names.searchDataSourceName,
        targetIndexName: names.searchIndexName,
        skillsetName: names.searchSkillsetName,
        schedule: { interval: 'PT5M' },
        parameters: {
            configuration: {
                dataToExtract: 'contentAndMetadata',
                parsingMode: 'default',
                imageAction: 'none',
                executionEnvironment: networkMode ? 'private' : 'standard',
            },
        },
        // The indexer needs the parent doc's content available to the
        // skillset (it's referenced as /document/content). 'default'
        // parsing mode populates it for PDF/docx/pptx/etc.
        fieldMappings: [],
        outputFieldMappings: [],
    };
    searchPut(`/indexers/${names.searchIndexerName}`, indexerBody, 'indexer');

    // 5. Kick it off so the user doesn't wait 5 minutes for the first run.
    const runRes = az([
        'rest',
        '--method', 'POST',
        '--uri', `${searchEndpoint}/indexers/${names.searchIndexerName}/run?api-version=${apiVersion}`,
        '--resource', 'https://search.azure.com/',
    ], { allowFail: true });
    if (runRes.ok) ok('indexer kicked off (initial run)');
}

// Grant the Search MSI 'Cognitive Services OpenAI User' on an existing
// AIServices account that's hosting our embedding model. Used when the
// user supplies s.ragEmbeddings.endpoint (e.g. reusing their voice-live
// account because tenant policy blocks creating a fresh one).
//
// We resolve the existing account by parsing its hostname, then try to
// find it via `az cognitiveservices account list`. The current az
// session must have read access to the account's subscription.
function grantSearchAccessToExistingEmbeddings(s, names) {
    step('Granting Search access to existing embeddings account');
    const endpoint = s.ragEmbeddings.endpoint;
    // https://<accountName>.cognitiveservices.azure.com  -> <accountName>
    const m = endpoint.match(/^https?:\/\/([^.]+)\./);
    if (!m) {
        warn(`Could not parse account name from endpoint ${endpoint}. Grant manually:`);
        log('    az role assignment create --assignee <search-msi-principal> \\');
        log('        --role "Cognitive Services OpenAI User" --scope <account-resource-id>');
        return;
    }
    const accountName = m[1];

    // Find the account anywhere in any subscription the user can see.
    // `az cognitiveservices account list` only lists current sub by
    // default, but the user is already on the right sub here (we set it
    // above), and AIServices accounts are typically in the same sub.
    const accts = azJson(['cognitiveservices', 'account', 'list']);
    const acct = (accts || []).find(a => a.name === accountName);
    if (!acct) {
        warn(`Could not find AIServices account '${accountName}' in subscription ${s.azure.subscriptionId}.`);
        warn('If it lives in another subscription, grant manually:');
        log('    az role assignment create --assignee <search-msi-principalId> \\');
        log('        --role "Cognitive Services OpenAI User" --scope <account-resource-id>');
        return;
    }

    // Get the search service's MSI principal ID (System-Assigned).
    const search = azJson([
        'search', 'service', 'show',
        '--name', names.searchServiceName,
        '--resource-group', s.azure.resourceGroup,
    ]);
    const principalId = search && search.identity && search.identity.principalId;
    if (!principalId) {
        warn(`Search service ${names.searchServiceName} has no system-assigned identity yet. Skipping role grant.`);
        return;
    }

    const res = az([
        'role', 'assignment', 'create',
        '--assignee-object-id', principalId,
        '--assignee-principal-type', 'ServicePrincipal',
        '--role', 'Cognitive Services OpenAI User',
        '--scope', acct.id,
    ], { allowFail: true });
    if (res.ok) {
        ok(`granted Search MSI Cognitive Services OpenAI User on ${accountName}`);
    } else if (/already exists|RoleAssignmentExists/i.test((res.stderr || res.stdout || ''))) {
        ok(`role already assigned on ${accountName}`);
    } else {
        warn(`Could not grant role: ${(res.stderr || res.stdout || '').trim().split('\n')[0]}`);
        warn('  The indexer\'s embedding skill will fail with 401 until granted.');
    }
}

// ---------- entra spa redirect ----------
function ensureSpaRedirectUri(clientId, appUrl) {
    step('Registering redirect URI in Entra app');
    const desired = appUrl.replace(/\/$/, '') + '/';
    const apps = azJson(['ad', 'app', 'list', '--filter', `appId eq '${clientId}'`]);
    if (!apps || apps.length === 0) {
        warn(`Could not find Entra app ${clientId}. Add this URI manually:`);
        log(`    ${desired}`);
        return;
    }
    const app = apps[0];
    const existing = (app.spa && app.spa.redirectUris) || [];
    if (existing.includes(desired) || existing.includes(desired.replace(/\/$/, ''))) {
        ok(`redirect URI already registered: ${desired}`);
        return;
    }
    const merged = [...existing, desired];
    const tmp = path.join(os.tmpdir(), `entra-spa-${Date.now()}.json`);
    fs.writeFileSync(tmp, JSON.stringify({ spa: { redirectUris: merged } }));
    const res = az([
        'rest',
        '--method', 'PATCH',
        '--uri', `https://graph.microsoft.com/v1.0/applications/${app.id}`,
        '--headers', 'Content-Type=application/json',
        '--body', `@${tmp}`,
    ], { allowFail: true });
    try { fs.unlinkSync(tmp); } catch {}
    if (!res.ok) {
        warn(`Could not auto-update Entra redirect URIs (likely missing permissions).`);
        if (res.stderr) log(`    ${res.stderr.trim().split('\n').slice(0, 3).join('\n    ')}`);
        warn(`Add manually under Authentication → SPA platform: ${desired}`);
        return;
    }
    ok(`added redirect URI: ${desired}`);
}

// ---------- Search Shared Private Link helpers (network mode + RAG) ----------

// Approve pending PE connections on the target account that originated
// from the AI Search service's Shared Private Link. PEs land on the
// target in "Pending" state; the indexer can't reach the target until
// the connection is approved.
//
// Filter precision: the AI Search bicep sets a unique requestMessage
// (agent-template-spl-<searchName>/<groupId>) that surfaces on the
// target as privateLinkServiceConnectionState.description. We only
// approve connections whose description starts with our marker prefix
// to avoid touching unrelated PE connections an admin may have set up
// on the same Storage / Embeddings account.
//
// Idempotent: connections that are already Approved are skipped, so
// re-running ./deploy after a partial run is safe.
function approveSplPendingConnections({ targetResourceId, markerPrefix, label }) {
    // The pending PE connection appears on the target side
    // synchronously when Bicep finishes creating the SPL, but ARM
    // list-cache propagation can lag for a few seconds. Retry a few
    // times to absorb that window before giving up.
    const maxAttempts = 4;
    let connections = null;
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
        const listRes = az([
            'network', 'private-endpoint-connection', 'list',
            '--id', targetResourceId,
        ], { allowFail: true });
        if (!listRes.ok) {
            warn(`Could not list PE connections on ${label} (${targetResourceId}). Indexer setup may fail.`);
            if (listRes.stderr) log(`    ${listRes.stderr.trim().split('\n').slice(0, 3).join('\n    ')}`);
            return;
        }
        try { connections = JSON.parse(listRes.stdout); } catch {
            warn(`Unparseable PE connection list for ${label}`);
            return;
        }
        // `az network private-endpoint-connection list` returns the
        // connection state nested under `.properties` (ARM shape). The
        // top-level fields (id, name, type, resourceGroup) are
        // promoted but everything else stays under properties.
        const hasMatch = Array.isArray(connections) && connections.some(c => {
            const s = (c.properties && c.properties.privateLinkServiceConnectionState) || {};
            return (s.description || '').startsWith(markerPrefix);
        });
        if (hasMatch) break;
        if (attempt < maxAttempts) {
            log(`    waiting for ${label} PE connection to propagate (attempt ${attempt}/${maxAttempts})...`);
            // Synchronous sleep via spawnSync since this fn isn't async.
            spawnSync(process.platform === 'win32' ? 'powershell' : 'sh',
                process.platform === 'win32'
                    ? ['-NoProfile', '-Command', 'Start-Sleep -Seconds 5']
                    : ['-c', 'sleep 5'],
                { stdio: 'ignore' });
        }
    }
    if (!Array.isArray(connections) || connections.length === 0) {
        warn(`No PE connections found on ${label} after retries — the Search SPL may not have propagated. Skipping approval; the indexer will retry at runtime.`);
        return;
    }
    let approvedAny = false;
    for (const conn of connections) {
        const state = (conn.properties && conn.properties.privateLinkServiceConnectionState) || {};
        const status = state.status || '';
        const description = state.description || '';
        if (!description.startsWith(markerPrefix)) continue;
        if (status === 'Approved') {
            log(`    [skip] ${label} connection ${conn.name} already Approved`);
            continue;
        }
        if (status !== 'Pending') {
            warn(`${label} connection ${conn.name} is in unexpected state '${status}', leaving untouched.`);
            continue;
        }
        const approveRes = az([
            'network', 'private-endpoint-connection', 'approve',
            '--id', conn.id,
            '--description', `Approved by agent template wizard (${markerPrefix})`,
        ], { allowFail: true });
        if (approveRes.ok) {
            ok(`approved ${label} connection ${conn.name}`);
            approvedAny = true;
        } else {
            warn(`Failed to approve ${label} connection ${conn.name}: ${(approveRes.stderr || '').trim().split('\n').slice(0, 2).join(' | ')}`);
        }
    }
    if (!approvedAny) {
        log(`    no pending connections matched on ${label} (already approved or not yet visible)`);
    }
}

// Poll the AI Search SPL resource until it reports succeeded + approved.
// The SPL provisioning state transitions: Updating -> (target approves)
// -> Succeeded. status transitions: Pending -> Approved. We need BOTH
// for the indexer's private execution environment to use the link.
async function pollSplApproved({ subscriptionId, resourceGroup, searchServiceName, splName, label, timeoutMs = 5 * 60 * 1000 }) {
    const start = Date.now();
    const uri = `https://management.azure.com/subscriptions/${subscriptionId}/resourceGroups/${resourceGroup}/providers/Microsoft.Search/searchServices/${searchServiceName}/sharedPrivateLinkResources/${splName}?api-version=2024-03-01-preview`;
    let lastState = '';
    let lastStatus = '';
    while (Date.now() - start < timeoutMs) {
        const res = az([
            'rest', '--method', 'GET',
            '--uri', uri,
        ], { allowFail: true });
        if (res.ok) {
            try {
                const data = JSON.parse(res.stdout);
                const props = data.properties || {};
                lastState = props.provisioningState || '';
                lastStatus = props.status || '';
                if (lastState === 'Succeeded' && lastStatus === 'Approved') {
                    ok(`${label} SPL approved (provisioning: ${lastState}, status: ${lastStatus})`);
                    return true;
                }
                if (lastState === 'Failed') {
                    warn(`${label} SPL provisioning failed: ${props.statusDescription || 'unknown'}`);
                    return false;
                }
            } catch {
                // fall through and retry
            }
        }
        await new Promise(resolve => setTimeout(resolve, 5000));
    }
    warn(`${label} SPL not in (Succeeded, Approved) after ${Math.round(timeoutMs / 1000)}s. Last state: provisioning=${lastState}, status=${lastStatus}. The indexer may fail until the SPL approval propagates; you can re-run ./deploy --search-only later.`);
    return false;
}

// ---------- main ----------
async function main() {
    log('');
    log('================================================================');
    log('  Agent Template — Deploy');
    log('================================================================');

    const s = readSettings();
    validateSettings(s);

    const authMode = s.voiceLive.authMode || 'key';
    const apiKey = process.env.AZURE_VOICELIVE_API_KEY || '';
    // --search-only does not touch the container app or its secrets, so
    // we don't need the voice key. Same for any other path that doesn't
    // end up writing the key into bicep. And in MI mode the key is
    // never used at runtime (backend reads AZURE_VOICELIVE_AUTH_MODE and
    // calls DefaultAzureCredential instead).
    if (authMode === 'key' && !apiKey && !FLAG_SEARCH_ONLY) {
        die('AZURE_VOICELIVE_API_KEY is not set.\n' +
            '  Easiest: copy .env.example to .env and paste your key in there.\n' +
            (IS_WIN
                ? '  Or in PowerShell:  $env:AZURE_VOICELIVE_API_KEY = "<your-key>"'
                : '  Or in bash:        export AZURE_VOICELIVE_API_KEY="<your-key>"'));
    }
    if (authMode === 'key' && apiKey && apiKey.length < 20) {
        die('AZURE_VOICELIVE_API_KEY looks too short.');
    }

    const templateVersion = readVersionFile();
    const gitSha = getGitShortSha();
    const localChanges = isWorktreeDirty();
    warnIfDirtyWorktree(gitSha);
    const names = deriveNames(s, { templateVersion, gitSha });
    log(`  Project:        ${s.projectName}`);
    log(`  Template ver:   ${templateVersion} (${gitSha})`);
    log(`  Subscription:   ${s.azure.subscriptionId}`);
    log(`  Resource group: ${s.azure.resourceGroup}`);
    log(`  Location:       ${s.azure.location}`);
    log(`  ACR:            ${names.acrName}`);

    let appUrl = s.deploy && s.deploy.appUrl;
    // Bicep outputs from pass 1 + pass 2. Capturing pass 1 lets us run
    // configureSearchIndexer BEFORE pass 2 in network mode; pass 2
    // outputs are persisted on s.deploy so --skip-infra reruns can still
    // find the embeddings endpoint.
    let outputsPass1 = null;
    let outputsPass2 = null;
    // Image reference passed to Bicep. Bare form (Bicep prepends the
    // ACR login server) — typically "<repo>@<digest>" for revision
    // pinning, or "<repo>:<tag>" as a fallback when digest resolution fails.
    let imageRefForBicep = names.imageName;
    let deployedImageTag = names.imageTag;
    // Digest portion of the resolved image reference ("sha256:..."). Passed
    // to Bicep as a separate param so the Container App can surface it on
    // /api/health as the immutable byte identity of the deployed image.
    let imageDigest = '';

    if (FLAG_SKIP_BUILD && !FLAG_SEARCH_ONLY) {
        // Reuse the image pinned on the last successful deploy. Current
        // HEAD may not match the last-built commit, so deriving a fresh
        // tag here would fail digest lookup.
        const cached = s.deploy && s.deploy.lastImageRef;
        if (!cached) {
            die('--skip-build requires a previous successful deploy (no cached image reference in settings.json). ' +
                'Drop --skip-build to build a fresh image, or run a full ./deploy once first.');
        }
        imageRefForBicep = cached;
        deployedImageTag = (s.deploy && s.deploy.lastImageTag) || null;
        const atIdx = cached.indexOf('@');
        if (atIdx !== -1) imageDigest = cached.slice(atIdx + 1);
    }

    log(`  Image:          ${names.acrName}.azurecr.io/${imageRefForBicep}`);
    log(`  Voice Live:     ${s.voiceLive.mode === 'create' ? names.voiceLiveAccount : s.voiceLive.endpoint} (${authMode === 'mi' ? 'managed identity' : 'api key'})`);
    log(`  Features:       ${Object.entries(s.features || {}).filter(([,v]) => v).map(([k]) => k).join(', ') || '(none)'}`);

    if (FLAG_SKIP_BUILD && !FLAG_SEARCH_ONLY) {
        ok('--skip-build: reusing pinned image (no rebuild)');
    }

    checkPrereqs();
    ensureSubscription(s.azure.subscriptionId);

    if (!FLAG_SKIP_BUILD && !FLAG_SEARCH_ONLY) {
        // Fail local customization, Python dependency, and app build checks
        // before creating or updating Azure resources.
        renderSystemPrompt(s);
        runAppPreflight(s);
    }

    ensureResourceGroup(s.azure.resourceGroup, s.azure.location);

    // Identify the principal running this deploy. We grant it Storage
    // Blob Data Contributor on the RAG storage account so the user can
    // upload PDFs via Storage Explorer / portal — the account has
    // shared-key access disabled so without an RBAC data-plane role the
    // user gets "This request is not authorized to perform this
    // operation using this permission" the moment they try to drop a
    // file in. Best-effort: if AAD lookup fails (some tenants restrict
    // it) we just skip the role grant with a warning.
    const deployer = resolveDeployer();
    if (deployer.objectId) {
        log(`  Deployer:       ${deployer.userName || deployer.objectId} (${deployer.principalType})`);
    } else if (s.features && s.features.rag) {
        warn('Could not resolve current user object id. RAG storage role grant will be skipped — you may need to grant yourself "Storage Blob Data Contributor" on the storage account manually before uploading via Storage Explorer.');
    }

    // --search-only: skip everything except the data-plane Search
    // pipeline. Requires a previous successful deploy (we read the
    // embeddings endpoint from settings.json).
    if (FLAG_SEARCH_ONLY) {
        if (!s.features || !s.features.rag) {
            die('--search-only requires features.rag enabled in settings.json.');
        }
        if (s.network && s.network.enabled) {
            die('--search-only is not supported when network mode is enabled.\n' +
                '  Search is firewalled to private endpoints, so the deployer cannot\n' +
                '  reach its REST API directly. Re-run ./deploy without flags — it\n' +
                '  will temporarily open Search during the inter-pass window and\n' +
                '  reconfigure the indexer for you.');
        }
        const cached = (s.deploy && s.deploy.ragEmbeddings) || {};
        if (!cached.endpoint || !cached.deployment) {
            die('--search-only requires a previous successful deploy (no embeddings endpoint cached). Run ./deploy first.');
        }
        configureSearchIndexer(s, names, {
            endpoint:   cached.endpoint,
            deployment: cached.deployment,
            model:      cached.model || 'text-embedding-3-large',
        });
        log('');
        log('================================================================');
        log('  Search pipeline reconfigured.');
        log('  Watch indexer status:');
        log(`    az search indexer show-status --service-name ${names.searchServiceName} --name ${names.searchIndexerName} --resource-group ${s.azure.resourceGroup}`);
        log('================================================================');
        return;
    }

    function buildBicepParams(deployApp, opts = {}) {
        const { acrLockdown = false, searchLockdown = false } = opts;
        // Network mode: route Container App egress through private
        // endpoints. `acrLockdown` and `searchLockdown` are pass-2-only
        // — pass 1 leaves ACR fully publicly accessible (so `az acr build`
        // can push images) and Search PNA=Enabled+IP-rule (so
        // configureSearchIndexer can PUT from the deployer's laptop).
        // Pass 2 flips both to publicNetworkAccess: Disabled with their
        // PEs / SPLs handling the actual traffic.
        const networkConfig = {
            enabled: !!(s.network && s.network.enabled),
            addressSpace: (s.network && s.network.addressSpace) || '10.20.0.0/16',
            subnets: {
                aca: (s.network && s.network.subnets && s.network.subnets.aca) || '10.20.0.0/27',
                privateEndpoints: (s.network && s.network.subnets && s.network.subnets.privateEndpoints) || '10.20.1.0/24',
            },
            deployerIpAddress: (s.network && s.network.deployerIpAddress) || '',
        };
        return [
            `projectName=${s.projectName}`,
            `location=${s.azure.location}`,
            `ragLocation=${s.azure.ragLocation || ''}`,
            `stage=${s.azure.stage || 'dev'}`,
            `acrName=${names.acrName}`,
            `envName=${names.envName}`,
            `logAnalyticsName=${names.logAnalyticsName}`,
            `appIdentityName=${names.appIdentityName}`,
            `imageName=${imageRefForBicep}`,
            `imageDigest=${imageDigest}`,
            `msalClientId=${s.msal.clientId}`,
            `msalTenantId=${s.msal.tenantId}`,
            `voiceLiveMode=${s.voiceLive.mode}`,
            `voiceLiveAccountName=${s.voiceLive.mode === 'create' ? names.voiceLiveAccount : ''}`,
            `existingVoiceLiveEndpoint=${s.voiceLive.mode === 'existing' ? s.voiceLive.endpoint : ''}`,
            `voiceLiveModel=${s.voiceLive.model || 'gpt-realtime'}`,
            // Coerce empty to the safe default so the CLI param can't
            // override the bicep default back to '' and re-trigger the
            // Sweden Central DeploymentModelNotSupported error.
            `voiceLiveModelVersion=${(s.voiceLive && s.voiceLive.modelVersion) || '2025-08-28'}`,
            `voiceLiveApiKey=${apiKey}`,
            `voiceLiveAuthMode=${authMode}`,
            `existingVoiceLiveAccountName=${s.voiceLive.mode === 'existing' ? (s.voiceLive.accountName || '') : ''}`,
            `existingVoiceLiveResourceGroup=${s.voiceLive.mode === 'existing' ? (s.voiceLive.resourceGroup || '') : ''}`,
            `features=${JSON.stringify(s.features || {})}`,
            `customDomainName=${(s.customDomain && s.customDomain.name) || ''}`,
            `customDomainCertName=${(s.customDomain && s.customDomain.certName) || ''}`,
            `voiceLiveVoice=${(s.agent && s.agent.voice) || 'en-US-Andrew:DragonHDLatestNeural'}`,
            `personalVoiceProfileId=${s.features && s.features.customVoice ? ((s.customVoice && s.customVoice.profileId) || '') : ''}`,
            `searchServiceName=${s.features && s.features.rag ? names.searchServiceName : ''}`,
            `ragStorageAccountName=${s.features && s.features.rag ? names.ragStorageAccount : ''}`,
            `ragContainerName=${names.ragContainerName}`,
            `ragEmbeddingsAccountName=${s.features && s.features.rag ? names.ragEmbeddingsAccount : ''}`,
            `ragEmbeddingsExistingEndpoint=${(s.ragEmbeddings && s.ragEmbeddings.endpoint) || ''}`,
            `ragEmbeddingsExistingDeployment=${(s.ragEmbeddings && s.ragEmbeddings.deployment) || ''}`,
            `ragEmbeddingModel=${(s.ragEmbeddings && s.ragEmbeddings.model) || 'text-embedding-3-large'}`,
            `deployerObjectId=${deployer.objectId || ''}`,
            `deployerPrincipalType=${deployer.principalType || 'User'}`,
            `deployApp=${deployApp}`,
            `agentGreeting=${(s.agent && s.agent.greeting) || ''}`,
            `cosmosAccountName=${s.features && s.features.transcripts ? names.cosmosAccountName : ''}`,
            `cosmosDatabaseName=${(s.transcripts && s.transcripts.databaseName) || 'transcripts'}`,
            `cosmosContainerName=${(s.transcripts && s.transcripts.containerName) || 'sessions'}`,
            `cosmosDefaultTtlSeconds=${s.features && s.features.transcripts ? cosmosTtlSeconds(s) : 2592000}`,
            `transcriptSalt=${(s.transcripts && s.transcripts.salt) || ''}`,
            `networkConfig=${JSON.stringify(networkConfig)}`,
            `acrLockdown=${acrLockdown}`,
            `searchLockdown=${searchLockdown}`,
        ];
    }

    // Validate network config before we start spending money. The
    // deployer IP must be a real IPv4 because ai-search.bicep +
    // rag-storage.bicep embed it into network ACL rules; an empty
    // value would lock the deployer out of the data plane.
    if (s.network && s.network.enabled) {
        const ip = (s.network && s.network.deployerIpAddress) || '';
        if (!/^(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)$/.test(ip)) {
            die(`Network mode is enabled but settings.network.deployerIpAddress is missing or invalid (got: "${ip}"). Re-run ./setup to detect your public IP, or set it manually in settings.json.`);
        }
    }

    const networkMode = !!(s.network && s.network.enabled);
    const ragEnabled = !!(s.features && s.features.rag);

    if (!FLAG_SKIP_INFRA) {
        // Pass 1: provision ACR + env + identity + (optional) Voice Live
        // and AI Search. Skip the Container App so we don't try to pull
        // an image that doesn't exist in ACR yet. In network mode, ACR
        // stays fully publicly accessible (acrLockdown=false) so the
        // subsequent `az acr build` works; Search stays PNA=Enabled+IP-rule
        // (searchLockdown=false) so we can configure the indexer.
        log('    (pass 1/2) provisioning supporting infrastructure...');
        outputsPass1 = submitDeployment({
            name: `${s.projectName}-infra-${Date.now()}`,
            resourceGroup: s.azure.resourceGroup,
            templateFile: MAIN_BICEP,
            params: buildBicepParams(false, { acrLockdown: false, searchLockdown: false }),
        });
    }

    buildImage(s, names, { templateVersion, gitSha, localChanges });

    if (!FLAG_SKIP_BUILD) {
        // Resolve the immutable digest for the image we just built. Pinning
        // the Container App revision to <repo>@<digest> means rollback is
        // just a redeploy of a previous digest — no rebuild required.
        const resolved = resolveImageDigest(names.acrName, names.imageRepo, names.imageTag);
        if (resolved) {
            // Strip the ACR login server; Bicep prepends it back. The
            // imageName param contract is bare "<repo>:<tag>" or
            // "<repo>@<digest>", no login-server prefix.
            imageRefForBicep = resolved.replace(`${names.acrName}.azurecr.io/`, '');
            const atIdx = imageRefForBicep.indexOf('@');
            if (atIdx !== -1) imageDigest = imageRefForBicep.slice(atIdx + 1);
            ok(`image digest resolved: ${resolved}`);
        } else {
            // Build just succeeded but the manifest query failed. Fall
            // back to the tag form so the deploy still works — log loudly
            // because rollback by digest won't be available for this rev.
            warn(`Could not resolve image digest for ${names.imageName}; falling back to tag form. Rollback by digest will not be available for this revision.`);
        }
    }

    // PR 3 network mode + RAG: between passes, approve the Search-
    // managed Shared Private Link connections on Storage + Embeddings
    // and configure the indexer while Search is still publicly
    // reachable (firewalled to deployer IP). Pass 2 will then flip
    // Search PNA to Disabled. This ordering is necessary because the
    // deployer's REST PUTs to /datasources, /indexes, /skillsets, and
    // /indexers can't traverse the private endpoint from outside the
    // VNet.
    if (!FLAG_SKIP_INFRA && networkMode && ragEnabled) {
        step('Approving Search Shared Private Links (network mode)');
        const markerPrefix = `agent-template-spl-${names.searchServiceName}`;
        const subscriptionId = s.azure.subscriptionId;
        const rg = s.azure.resourceGroup;
        const storageId = `/subscriptions/${subscriptionId}/resourceGroups/${rg}/providers/Microsoft.Storage/storageAccounts/${names.ragStorageAccount}`;
        approveSplPendingConnections({
            targetResourceId: storageId,
            markerPrefix,
            label: 'Storage',
        });
        // Embeddings — only when we own the account (existing-endpoint
        // path is blocked upstream in the wizard for network mode).
        if (!s.ragEmbeddings || !s.ragEmbeddings.endpoint) {
            const embeddingsId = `/subscriptions/${subscriptionId}/resourceGroups/${rg}/providers/Microsoft.CognitiveServices/accounts/${names.ragEmbeddingsAccount}`;
            approveSplPendingConnections({
                targetResourceId: embeddingsId,
                markerPrefix,
                label: 'Embeddings',
            });
        }

        step('Waiting for SPL approval to propagate to Search');
        await pollSplApproved({
            subscriptionId,
            resourceGroup: rg,
            searchServiceName: names.searchServiceName,
            splName: 'spl-rag-storage',
            label: 'Storage',
        });
        if (!s.ragEmbeddings || !s.ragEmbeddings.endpoint) {
            await pollSplApproved({
                subscriptionId,
                resourceGroup: rg,
                searchServiceName: names.searchServiceName,
                splName: 'spl-rag-embeddings',
                label: 'Embeddings',
            });
        }

        // Configure the indexer with executionEnvironment=private so it
        // runs in Search's private fleet and uses the SPLs to reach
        // Storage + Embeddings.
        const cached = (s.deploy && s.deploy.ragEmbeddings) || {};
        const embeddings = {
            endpoint:   (outputsPass1 && outputsPass1.ragEmbeddingsEndpoint)   || cached.endpoint   || '',
            deployment: (outputsPass1 && outputsPass1.ragEmbeddingsDeployment) || cached.deployment || '',
            model:      (outputsPass1 && outputsPass1.ragEmbeddingsModel)      || cached.model      || 'text-embedding-3-large',
        };
        if (s.ragEmbeddings && s.ragEmbeddings.endpoint) {
            grantSearchAccessToExistingEmbeddings(s, names);
        }
        configureSearchIndexer(s, names, embeddings, { networkMode: true });
    }

    if (!FLAG_SKIP_INFRA) {
        // Pass 2: now that the image exists in ACR, create/update the
        // Container App that references it. In network mode, also
        // flip ACR + Search to publicNetworkAccess: Disabled — the PEs
        // (and Search-managed SPLs) are already wired so pulls /
        // indexer execution go through private paths.
        const lockdown = networkMode;
        log(lockdown
            ? '    (pass 2/2) deploying Container App + locking down ACR' + (ragEnabled ? ' + Search...' : '...')
            : '    (pass 2/2) deploying Container App...');
        const outputs = submitDeployment({
            name: `${s.projectName}-app-${Date.now()}`,
            resourceGroup: s.azure.resourceGroup,
            templateFile: MAIN_BICEP,
            params: buildBicepParams(true, {
                acrLockdown: lockdown,
                searchLockdown: lockdown && ragEnabled,
            }),
        });
        outputsPass2 = outputs;
        appUrl = outputs.appUrl;
        if (!appUrl) die('Bicep did not return appUrl.');

        // Nudge a new revision when re-deploying. We pin to the
        // digest-resolved reference so Container Apps pulls exactly the
        // image we just built (and so rolling back is "redeploy the
        // previous digest"). --revision-suffix gives the revision a
        // readable name; the digest itself is what guarantees fresh bytes.
        if (!FLAG_SKIP_BUILD) {
            step('Rolling new revision');
            const revSuffix = `v${Date.now().toString(36)}`;
            az([
                'containerapp', 'update',
                '--name', s.projectName,
                '--resource-group', s.azure.resourceGroup,
                '--image', `${names.acrName}.azurecr.io/${imageRefForBicep}`,
                '--revision-suffix', revSuffix,
            ]);
            ok(`revision ${revSuffix} updated`);
        }
    } else {
        warn('--skip-infra set, skipping bicep deploys');
        if (!appUrl) die('--skip-infra requires a previous successful deploy (no appUrl in settings).');
        if (!FLAG_SKIP_BUILD) {
            step('Rolling new revision');
            const revSuffix = `v${Date.now().toString(36)}`;
            az([
                'containerapp', 'update',
                '--name', s.projectName,
                '--resource-group', s.azure.resourceGroup,
                '--image', `${names.acrName}.azurecr.io/${imageRefForBicep}`,
                '--revision-suffix', revSuffix,
            ]);
            ok(`revision ${revSuffix} updated`);
        }
    }

    ensureSpaRedirectUri(s.msal.clientId, appUrl);

    if (ragEnabled && !networkMode) {
        // Non-network mode: Search is publicly reachable so we can
        // configure the indexer AFTER pass 2 as we always have. In
        // network mode this step already ran between passes (above)
        // because pass 2 locks Search down.
        const cached = (s.deploy && s.deploy.ragEmbeddings) || {};
        const embeddings = {
            endpoint:   (outputsPass2 && outputsPass2.ragEmbeddingsEndpoint)   || cached.endpoint   || '',
            deployment: (outputsPass2 && outputsPass2.ragEmbeddingsDeployment) || cached.deployment || '',
            model:      (outputsPass2 && outputsPass2.ragEmbeddingsModel)      || cached.model      || 'text-embedding-3-large',
        };

        // If the user provided an existing AIServices account for
        // embeddings (because their tenant blocks creating new ones),
        // bicep didn't grant the role assignment. Do it here via az CLI
        // since the existing account may live in a different RG/sub.
        if (s.ragEmbeddings && s.ragEmbeddings.endpoint) {
            grantSearchAccessToExistingEmbeddings(s, names);
        }

        configureSearchIndexer(s, names, embeddings);
    }

    s.deploy = s.deploy || {};
    s.deploy.lastDeployedAt = new Date().toISOString();
    s.deploy.appUrl = appUrl;
    // Cache the image reference that just shipped so --skip-build can
    // redeploy infra against the same image bytes on later commits.
    s.deploy.lastImageRef = imageRefForBicep;
    s.deploy.lastImageTag = deployedImageTag;
    if (s.features && s.features.rag) {
        s.deploy.ragContainerUrl =
            `https://${names.ragStorageAccount}.blob.core.windows.net/${names.ragContainerName}`;
        s.deploy.searchEndpoint = `https://${names.searchServiceName}.search.windows.net`;
        s.deploy.searchServiceName = names.searchServiceName;
        s.deploy.searchIndexName = names.searchIndexName;
        s.deploy.searchSemanticConfig = names.searchSemanticConfig;
        if (outputsPass2) {
            s.deploy.ragEmbeddings = {
                endpoint:   outputsPass2.ragEmbeddingsEndpoint   || '',
                deployment: outputsPass2.ragEmbeddingsDeployment || '',
                model:      outputsPass2.ragEmbeddingsModel      || '',
            };
        }
    }
    if (s.features && s.features.transcripts) {
        s.deploy.cosmosAccountName = (outputsPass2 && outputsPass2.cosmosAccountName) || names.cosmosAccountName;
        s.deploy.cosmosEndpoint = (outputsPass2 && outputsPass2.cosmosEndpoint)
            || `https://${names.cosmosAccountName}.documents.azure.com:443/`;
        s.deploy.cosmosDatabaseName = (outputsPass2 && outputsPass2.cosmosDatabaseName) || s.transcripts.databaseName;
        s.deploy.cosmosContainerName = (outputsPass2 && outputsPass2.cosmosContainerName) || s.transcripts.containerName;
    }
    writeSettings(s);

    log('');
    log('================================================================');
    log(`  Deployed: ${appUrl}`);
    log(`  Open:     ${appUrl} in your browser`);
    if (s.features && s.features.rag) {
        const blobUrl    = `https://${names.ragStorageAccount}.blob.core.windows.net/${names.ragContainerName}`;
        const portalUrl  = `https://portal.azure.com/#@/resource/subscriptions/${s.azure.subscriptionId}/resourceGroups/${s.azure.resourceGroup}/providers/Microsoft.Storage/storageAccounts/${names.ragStorageAccount}/containersList`;
        log('');
        log('  RAG enabled — upload knowledge base docs:');
        log(`    Storage account:  ${names.ragStorageAccount}`);
        log(`    Container:        ${names.ragContainerName}`);
        log(`    Blob URL:         ${blobUrl}`);
        log('');
        log('    Upload via:');
        log('      1. Azure Portal (drag & drop in browser):');
        log(`           ${portalUrl}`);
        log('      2. Azure Storage Explorer (desktop app):');
        log('           Download:  https://aka.ms/storageexplorer');
        log('           Then: File > Connect > Blob container > use the Blob URL above');
        log('');
        log('    Indexer runs every 5 minutes — new docs become searchable automatically.');
    }
    if (s.features && s.features.transcripts) {
        const acct = (s.deploy && s.deploy.cosmosAccountName) || names.cosmosAccountName;
        const dataExplorer = `https://portal.azure.com/#@/resource/subscriptions/${s.azure.subscriptionId}/resourceGroups/${s.azure.resourceGroup}/providers/Microsoft.DocumentDB/databaseAccounts/${acct}/dataExplorer`;
        const ttl = (s.transcripts && Number.isFinite(s.transcripts.retentionDays)) ? s.transcripts.retentionDays : 30;
        log('');
        log('  Transcript saving enabled — sessions land in Cosmos DB:');
        log(`    Account:          ${acct}`);
        log(`    Database:         ${s.transcripts.databaseName}`);
        log(`    Container:        ${s.transcripts.containerName}`);
        log(`    Retention:        ${ttl === 0 ? 'forever' : `${ttl} days`}`);
        log('    Pseudonymization: SHA-256(upn + per-deployment salt), 16 chars');
        log('');
        log('    Inspect with Data Explorer:');
        log(`      ${dataExplorer}`);
    }
    log('================================================================');
    log('');
}

main().catch(err => die(err && err.stack ? err.stack : String(err)));
