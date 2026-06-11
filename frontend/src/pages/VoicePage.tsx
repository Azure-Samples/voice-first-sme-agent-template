import { useState, useRef, useCallback, useEffect } from "react";
import { useMsal, useIsAuthenticated } from "@azure/msal-react";
import { InteractionStatus } from "@azure/msal-browser";
import { loginRequest } from "../config/auth";
import { FeedbackButton } from "../components/FeedbackButton";
import { AppFooter } from "../components/AppFooter";
import { devLog } from "../utils/devLog";
import {
  Button,
  Input,
  Text,
  MessageBar,
  MessageBarTitle,
  MessageBarBody,
  MessageBarActions,
  Avatar,
  Link,
} from "@fluentui/react-components";
import {
  Mic24Filled,
  MicOff24Filled,
  DoorArrowLeft24Filled,
  Subtitles24Regular,
  Alert24Regular,
  Dismiss24Regular,
  Send24Regular,
  SignOut24Regular,
} from "@fluentui/react-icons";

const SAMPLE_RATE = 24000;
const CHUNK_SIZE = 2400;

type TranscriptEntry = {
  role: "user" | "agent" | "system";
  text: string;
  streaming?: boolean;
  // True on a user entry whose audio is still being transcribed
  // server-side. The slot is reserved at speech_started; the text is
  // filled in when input_audio_transcription.completed arrives (which
  // can lag the agent's reply by 1–2s).
  transcribing?: boolean;
  // Voice Live item_id from speech_started — used to match the right
  // placeholder when multiple are pending (rapid turns).
  itemId?: string;
  responseId?: string;
};

type CallState = "idle" | "connecting" | "connected" | "error";
type VoiceStatus = "ready" | "listening" | "speaking" | "searching" | "error";

export default function VoicePage() {
  const { instance, accounts, inProgress } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const [authError, setAuthError] = useState<string | null>(null);

  // Auto-redirect to login if not authenticated and no interaction in progress.
  // If loginRedirect itself rejects (bad authority, missing clientId, etc.), we
  // surface the error in the unauthenticated UI instead of looping forever.
  useEffect(() => {
    if (!isAuthenticated && inProgress === InteractionStatus.None && !authError) {
      instance.loginRedirect(loginRequest).catch((err: unknown) => {
        // eslint-disable-next-line no-console
        console.error("[MSAL] loginRedirect failed:", err);
        const e = err as { errorCode?: string; errorMessage?: string; message?: string };
        setAuthError(e?.errorCode || e?.errorMessage || e?.message || String(err));
      });
    }
  }, [isAuthenticated, inProgress, instance, authError]);

  const [callState, setCallState] = useState<CallState>("idle");
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus>("ready");
  const [statusText, setStatusText] = useState("Ready");
  const [micEnabled, setMicEnabled] = useState(true);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [textInput, setTextInput] = useState("");
  const [voiceName] = useState("en-US-Andrew:DragonHDLatestNeural");
  const [temperature] = useState(0.8);
  const [vadEagerness] = useState("medium");
  const [noiseSuppression] = useState("azure_deep_noise_suppression");
  const [showBanner, setShowBanner] = useState(false);
  const [showTranscript, setShowTranscript] = useState(false);
  const [showUserMenu, setShowUserMenu] = useState(false);

  // Close user menu when clicking outside
  useEffect(() => {
    if (!showUserMenu) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest(".user-menu-container")) {
        setShowUserMenu(false);
      }
    };
    // Delay to avoid catching the opening click
    requestAnimationFrame(() => {
      document.addEventListener("click", handler);
    });
    return () => document.removeEventListener("click", handler);
  }, [showUserMenu]);

  const wsRef = useRef<WebSocket | null>(null);
  const intentionalCloseRef = useRef(false);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const playCtxRef = useRef<AudioContext | null>(null);
  const nextPlayTimeRef = useRef(0);
  const scheduledRef = useRef<AudioBufferSourceNode[]>([]);
  const agentTextRef = useRef("");
  const pendingCallsRef = useRef<
    Record<string, { name: string; call_id: string; arguments: string }>
  >({});
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);

  // Filler & nudge state
  const fillerClipsRef = useRef<{ phrase: string; pcm: ArrayBuffer }[]>([]);
  const fillerLastIndexRef = useRef(-1);
  const nudgeClipsRef = useRef<{ phrase: string; pcm: ArrayBuffer }[]>([]);
  const nudgeLastIndexRef = useRef(-1);
  const nudgeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const sessionInitRef = useRef(false);
  const NUDGE_DELAY_MS = 15000;

  // Prefetched data refs — loaded on page mount so start() is instant
  const prefetchedConfigRef = useRef<{ wss_url: string; voice_name?: string; personal_voice_profile_id?: string } | null>(null);
  const prefetchedPromptRef = useRef<string | null>(null);
  const prefetchedGreetingRef = useRef<{ phrase: string; audio_b64: string } | null>(null);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcript]);

  const getToken = useCallback(async (): Promise<string | null> => {
    if (!isAuthenticated || !accounts[0]) return null;
    try {
      const res = await instance.acquireTokenSilent({
        ...loginRequest,
        account: accounts[0],
      });
      return res.accessToken;
    } catch {
      return null;
    }
  }, [instance, accounts, isAuthenticated]);

  function authHeaders(token: string | null): Record<string, string> {
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  // Prefetch voice-config, system-prompt, greeting, fillers, nudges on page load
  useEffect(() => {
    if (!isAuthenticated) return;
    (async () => {
      const token = await getToken();
      if (!token) return;
      sessionStorage.setItem("_voice_token", token);
      const hdrs = authHeaders(token);
      // Fire all fetches in parallel
      const [configRes, promptRes, greetingRes, fillersRes, nudgesRes] = await Promise.all([
        fetch("/api/voice-config", { headers: hdrs }).catch(() => null),
        fetch("/api/system-prompt", { headers: hdrs }).catch(() => null),
        fetch("/api/greeting", { headers: hdrs }).catch(() => null),
        fetch("/api/fillers", { headers: hdrs }).catch(() => null),
        fetch("/api/nudges", { headers: hdrs }).catch(() => null),
      ]);
      if (configRes?.ok) prefetchedConfigRef.current = await configRes.json();
      if (promptRes?.ok) {
        const data = await promptRes.json();
        prefetchedPromptRef.current = data.prompt;
      }
      if (greetingRes?.ok) prefetchedGreetingRef.current = await greetingRes.json();
      if (fillersRes?.ok) {
        const clips = await fillersRes.json();
        fillerClipsRef.current = clips.map((c: { phrase: string; audio_b64: string }) => ({
          phrase: c.phrase, pcm: base64ToArrayBuffer(c.audio_b64),
        }));
      }
      if (nudgesRes?.ok) {
        const clips = await nudgesRes.json();
        nudgeClipsRef.current = clips.map((c: { phrase: string; audio_b64: string }) => ({
          phrase: c.phrase, pcm: base64ToArrayBuffer(c.audio_b64),
        }));
      }
    })();
  }, [isAuthenticated, getToken]);

  // ── Audio helpers ──

  function float32ToPcm16(f: Float32Array): Int16Array {
    const p = new Int16Array(f.length);
    for (let i = 0; i < f.length; i++) {
      const s = Math.max(-1, Math.min(1, f[i]));
      p[i] = s < 0 ? s * 32768 : s * 32767;
    }
    return p;
  }

  function arrayBufferToBase64(buf: ArrayBuffer): string {
    const b = new Uint8Array(buf);
    let s = "";
    for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]);
    return btoa(s);
  }

  function base64ToArrayBuffer(b64: string): ArrayBuffer {
    const b = atob(b64);
    const a = new Uint8Array(b.length);
    for (let i = 0; i < b.length; i++) a[i] = b.charCodeAt(i);
    return a.buffer;
  }

  function scheduleAudio(arrayBuffer: ArrayBuffer) {
    const playCtx = playCtxRef.current;
    if (!playCtx) return;
    const pcm16 = new Int16Array(arrayBuffer);
    const float32 = new Float32Array(pcm16.length);
    for (let i = 0; i < pcm16.length; i++) float32[i] = pcm16[i] / 32768;
    const buffer = playCtx.createBuffer(1, float32.length, SAMPLE_RATE);
    buffer.getChannelData(0).set(float32);
    const src = playCtx.createBufferSource();
    src.buffer = buffer;
    src.connect(playCtx.destination);
    const now = playCtx.currentTime;
    if (nextPlayTimeRef.current < now) nextPlayTimeRef.current = now + 0.005;
    src.start(nextPlayTimeRef.current);
    nextPlayTimeRef.current += buffer.duration;
    scheduledRef.current.push(src);
    src.onended = () => {
      scheduledRef.current = scheduledRef.current.filter((s) => s !== src);
    };
  }

  function clearPlayback() {
    for (const src of scheduledRef.current) {
      try {
        src.onended = null;
        src.stop();
      } catch {
        /* already stopped */
      }
    }
    scheduledRef.current = [];
    nextPlayTimeRef.current = 0;
  }

  function playRandomFiller() {
    const clips = fillerClipsRef.current;
    if (clips.length === 0 || !playCtxRef.current) return;
    let idx = Math.floor(Math.random() * (clips.length - 1));
    if (idx >= fillerLastIndexRef.current) idx++;
    fillerLastIndexRef.current = idx;
    scheduleAudio(clips[idx].pcm);
  }

  function startNudgeTimer() {
    clearNudgeTimer();
    const bufferedMs = playCtxRef.current
      ? Math.max(0, (nextPlayTimeRef.current - playCtxRef.current.currentTime) * 1000)
      : 0;
    nudgeTimerRef.current = setTimeout(function onNudge() {
      const clips = nudgeClipsRef.current;
      if (!playCtxRef.current || clips.length === 0) return;
      let idx = Math.floor(Math.random() * (clips.length - 1));
      if (idx >= nudgeLastIndexRef.current) idx++;
      nudgeLastIndexRef.current = idx;
      scheduleAudio(clips[idx].pcm);
      nudgeTimerRef.current = setTimeout(onNudge, NUDGE_DELAY_MS);
    }, NUDGE_DELAY_MS + bufferedMs);
  }

  function clearNudgeTimer() {
    if (nudgeTimerRef.current) {
      clearTimeout(nudgeTimerRef.current);
      nudgeTimerRef.current = null;
    }
  }

  function buildSessionConfig(instructions?: string) {
    const personalVoiceProfileId = prefetchedConfigRef.current?.personal_voice_profile_id || "";
    const configuredVoiceName = prefetchedConfigRef.current?.voice_name || voiceName;
    const voiceConfig = personalVoiceProfileId
      ? {
          name: personalVoiceProfileId,
          type: "azure-personal",
          model: "DragonLatestNeural",
          temperature,
        }
      : { name: configuredVoiceName, type: "azure-standard", temperature };
    const session: Record<string, unknown> = {
      input_audio_sampling_rate: SAMPLE_RATE,
      turn_detection: {
        type: "azure_semantic_vad",
        threshold: 0.3,
        prefix_padding_ms: 500,
        silence_duration_ms: 500,
        speech_duration_ms: 80,
        remove_filler_words: true,
        interrupt_response: true,
        auto_truncate: true,
        eagerness: vadEagerness,
      },
      input_audio_echo_cancellation: { type: "server_echo_cancellation" },
      input_audio_transcription: { model: "azure-speech", language: "en" },
      voice: voiceConfig,
      tools: [
        {
          type: "function",
          name: "search_knowledge_base",
          description: "Search the knowledge base for relevant information to support the coaching conversation.",
          parameters: {
            type: "object",
            properties: { query: { type: "string", description: "Search query" } },
            required: ["query"],
          },
        },
      ],
      tool_choice: "auto",
    };
    if (instructions) session.instructions = instructions;
    if (noiseSuppression !== "none") {
      session.input_audio_noise_reduction = { type: noiseSuppression };
    }
    return session;
  }

  // Push settings to Azure when changed mid-session
  const settingsKeyRef = useRef("");
  useEffect(() => {
    const key = `${voiceName}|${temperature}|${vadEagerness}|${noiseSuppression}`;
    if (callState === "connected" && settingsKeyRef.current && settingsKeyRef.current !== key) {
      wsSend({ type: "session.update", session: buildSessionConfig() });
    }
    settingsKeyRef.current = key;
  }, [voiceName, temperature, vadEagerness, noiseSuppression, callState]);

  function wsSend(obj: unknown) {
    if (wsRef.current?.readyState === WebSocket.OPEN)
      wsRef.current.send(JSON.stringify(obj));
  }

  // ── Function calling ──

  async function executeFunctionCall(callInfo: {
    name: string;
    call_id: string;
    arguments: string;
  }) {
    const { name, call_id } = callInfo;
    let args: Record<string, string>;
    try {
      args = JSON.parse(callInfo.arguments);
    } catch {
      args = {};
    }

    if (name === "search_knowledge_base") {
      setTimeout(() => playRandomFiller(), 500);
      try {
        const token = await getToken();
        const resp = await fetch("/api/search", {
          method: "POST",
          headers: { "Content-Type": "application/json", ...authHeaders(token) },
          body: JSON.stringify({ query: args.query || "", top_k: 3 }),
        });
        const data = await resp.json();
        let output = `Search query: ${args.query}\n\n`;
        if (data.results?.length > 0) {
          data.results.forEach(
            (r: { title: string; content: string }, i: number) => {
              output += `## Result ${i + 1}: ${r.title}\n${r.content}\n\n`;
            },
          );
        } else {
          output += "No relevant results found.\n";
        }
        wsSend({
          type: "conversation.item.create",
          item: { type: "function_call_output", call_id, output },
        });
        wsSend({ type: "response.create" });
      } catch (err) {
        console.error("Search failed:", err);
        wsSend({
          type: "conversation.item.create",
          item: {
            type: "function_call_output",
            call_id,
            output: "Search failed.",
          },
        });
        wsSend({ type: "response.create" });
      }
    } else {
      wsSend({
        type: "conversation.item.create",
        item: {
          type: "function_call_output",
          call_id,
          output: `Unknown function: ${name}`,
        },
      });
      wsSend({ type: "response.create" });
    }
    delete pendingCallsRef.current[call_id];
  }

  // ── WebSocket event handler ──

  const handleEvent = useCallback(
    (event: {
      type?: string;
      delta?: string;
      transcript?: string;
      item?: { type?: string; call_id?: string; name?: string; id?: string };
      call_id?: string;
      arguments?: string;
      error?: { message?: string };
      item_id?: string;
      response_id?: string;
    }) => {
      const t = event.type || "";
      devLog("Voice event:", t, event);

      if (t === "session.created") {
        // Use prefetched prompt or fetch fresh
        if (prefetchedPromptRef.current) {
          wsSend({ type: "session.update", session: buildSessionConfig(prefetchedPromptRef.current) });
        } else {
          fetch("/api/system-prompt", {
            headers: authHeaders(sessionStorage.getItem("_voice_token")),
          })
            .then((r) => r.json())
            .then((data) => {
              wsSend({ type: "session.update", session: buildSessionConfig(data.prompt) });
            });
        }
      } else if (t === "session.updated") {
        // Only do init (mic, greeting, clip loading) on the first session.updated
        if (!sessionInitRef.current) {
          sessionInitRef.current = true;
          workletRef.current!.port.onmessage = (e) => {
            if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN)
              return;
            wsSend({
              type: "input_audio_buffer.append",
              audio: arrayBufferToBase64(float32ToPcm16(e.data).buffer as ArrayBuffer),
            });
          };
          setCallState("connected");
          setVoiceStatus("speaking");
          setStatusText("Agent speaking...");
          // Filler & nudge clips already prefetched — fetch only if missing
          if (fillerClipsRef.current.length === 0 || nudgeClipsRef.current.length === 0) {
            const hdrs = authHeaders(sessionStorage.getItem("_voice_token"));
            if (fillerClipsRef.current.length === 0) {
              fetch("/api/fillers", { headers: hdrs })
                .then((r) => r.json())
                .then((clips) => {
                  fillerClipsRef.current = clips.map((c: { phrase: string; audio_b64: string }) => ({
                    phrase: c.phrase, pcm: base64ToArrayBuffer(c.audio_b64),
                  }));
                })
                .catch(() => {});
            }
            if (nudgeClipsRef.current.length === 0) {
              fetch("/api/nudges", { headers: hdrs })
                .then((r) => r.json())
                .then((clips) => {
                  nudgeClipsRef.current = clips.map((c: { phrase: string; audio_b64: string }) => ({
                    phrase: c.phrase, pcm: base64ToArrayBuffer(c.audio_b64),
                  }));
                })
                .catch(() => {});
            }
          }
          // Play pre-generated greeting instantly
          const clip = prefetchedGreetingRef.current;
          if (clip?.audio_b64) {
            scheduleAudio(base64ToArrayBuffer(clip.audio_b64));
            setTranscript((prev) => [...prev, { role: "agent", text: clip.phrase }]);
            wsSend({
              type: "conversation.item.create",
              item: {
                type: "message",
                role: "assistant",
                content: [{ type: "text", text: clip.phrase }],
              },
            });
            startNudgeTimer();
            setVoiceStatus("ready");
            setStatusText("Ready");
          } else {
            // Fallback: fetch greeting if not prefetched
            const hdrs = authHeaders(sessionStorage.getItem("_voice_token"));
            fetch("/api/greeting", { headers: hdrs })
              .then((r) => r.json())
              .then((g) => {
                if (g.audio_b64) {
                  scheduleAudio(base64ToArrayBuffer(g.audio_b64));
                  setTranscript((prev) => [...prev, { role: "agent", text: g.phrase }]);
                  wsSend({
                    type: "conversation.item.create",
                    item: {
                      type: "message",
                      role: "assistant",
                      content: [{ type: "text", text: g.phrase }],
                    },
                  });
                  startNudgeTimer();
                } else {
                  wsSend({ type: "response.create" });
                }
                setVoiceStatus("ready");
                setStatusText("Ready");
              })
              .catch(() => {
                wsSend({ type: "response.create" });
                setVoiceStatus("ready");
                setStatusText("Ready");
              });
          }
        }
      } else if (t === "response.audio.delta") {
        clearNudgeTimer();
        scheduleAudio(base64ToArrayBuffer(event.delta!));
        setVoiceStatus("speaking");
        setStatusText("Speaking...");
      } else if (t === "response.audio_transcript.delta") {
        const delta = event.delta || "";
        const responseId = event.response_id;
        agentTextRef.current += delta;
        setTranscript((prev) => {
          // Match by Voice Live response_id
          let idx = -1;
          if (responseId) {
            for (let i = prev.length - 1; i >= 0; i--) {
              if (
                prev[i].role === "agent" &&
                prev[i].responseId === responseId
              ) {
                idx = i;
                break;
              }
            }
          }
          // Defensive fallback for events that arrive without a
          // response_id (older servers / unexpected payloads): use the
          // legacy "most recent streaming agent bubble" heuristic.
          if (idx < 0) {
            for (let i = prev.length - 1; i >= 0; i--) {
              if (prev[i].role === "agent" && prev[i].streaming) {
                idx = i;
                break;
              }
            }
          }
          if (idx >= 0) {
            const next = prev.slice();
            next[idx] = {
              ...next[idx],
              text: (next[idx].text || "") + delta,
              // Backfill responseId if this bubble was created before
              // we knew it (e.g. matched only by the streaming flag).
              responseId: next[idx].responseId || responseId,
            };
            return next;
          }
          // No in-flight bubble — start a new one with just this delta.
          // Don't seed from agentTextRef: it can carry stale text from
          // a previous response that wasn't finalized cleanly.
          return [
            ...prev,
            { role: "agent", text: delta, streaming: true, responseId },
          ];
        });
      } else if (t === "response.audio_transcript.done") {
        const final = event.transcript;
        const responseId = event.response_id;
        setTranscript((prev) => {
          let idx = -1;
          if (responseId) {
            for (let i = prev.length - 1; i >= 0; i--) {
              if (
                prev[i].role === "agent" &&
                prev[i].responseId === responseId
              ) {
                idx = i;
                break;
              }
            }
          }
          if (idx < 0) {
            for (let i = prev.length - 1; i >= 0; i--) {
              if (prev[i].role === "agent" && prev[i].streaming) {
                idx = i;
                break;
              }
            }
          }
          if (idx >= 0) {
            const next = prev.slice();
            const fallback = next[idx].text || "";
            next[idx] = {
              ...next[idx],
              role: "agent",
              text: final || fallback,
              streaming: false,
              responseId: next[idx].responseId || responseId,
            };
            return next;
          }
          if (final) {
            return [
              ...prev,
              { role: "agent", text: final, responseId },
            ];
          }
          return prev;
        });
        agentTextRef.current = "";
      } else if (
        t === "conversation.item.input_audio_transcription.delta"
      ) {
        // Partial STT result. Fill the placeholder incrementally so
        // the user's bubble shows their words as they speak instead of
        // sitting on "…" until completion arrives 1–2s later.
        const itemId: string | undefined = event.item_id;
        setTranscript((prev) => {
          let idx = -1;
          if (itemId) {
            idx = prev.findIndex(
              (e) => e.transcribing && e.itemId === itemId,
            );
          }
          if (idx < 0) {
            idx = prev.findIndex((e) => e.role === "user" && e.transcribing);
          }
          if (idx < 0) return prev;
          // Voice Live's payload may carry the new chunk in `delta` or
          // the accumulated text in `transcript`. Handle both.
          const current = prev[idx].text === "…" ? "" : (prev[idx].text || "");
          const newText = event.delta
            ? current + event.delta
            : (event.transcript || current);
          if (!newText) return prev;
          const next = prev.slice();
          next[idx] = { ...next[idx], text: newText };
          return next;
        });
      } else if (
        t === "conversation.item.input_audio_transcription.completed"
      ) {
        const userText = (event.transcript || "").trim();
        const itemId: string | undefined = event.item_id;
        setTranscript((prev) => {
          // Locate the placeholder: first by item_id, then FIFO fallback.
          let idx = -1;
          if (itemId) {
            idx = prev.findIndex(
              (e) => e.transcribing && e.itemId === itemId,
            );
          }
          if (idx < 0) {
            idx = prev.findIndex((e) => e.role === "user" && e.transcribing);
          }
          if (!userText) {
            // Empty transcription — drop the placeholder so we don't
            // leave a stale "…" bubble.
            if (idx < 0) return prev;
            return [...prev.slice(0, idx), ...prev.slice(idx + 1)];
          }
          if (idx >= 0) {
            const next = prev.slice();
            next[idx] = { role: "user", text: userText };
            return next;
          }
          // No placeholder — speech_started never fired (azure_semantic_vad
          // can skip it). Insert the user message BEFORE the most
          // recent streaming agent bubble so chronological order is
          // preserved instead of dumping the user line after the agent's
          // reply.
          let insertAt = prev.length;
          for (let i = prev.length - 1; i >= 0; i--) {
            if (prev[i].role === "agent" && prev[i].streaming) {
              insertAt = i;
              break;
            }
          }
          return [
            ...prev.slice(0, insertAt),
            { role: "user", text: userText },
            ...prev.slice(insertAt),
          ];
        });
      } else if (
        t === "conversation.item.input_audio_transcription.failed"
      ) {
        const itemId: string | undefined = event.item_id;
        setTranscript((prev) => {
          let idx = -1;
          if (itemId) {
            idx = prev.findIndex(
              (e) => e.transcribing && e.itemId === itemId,
            );
          }
          if (idx < 0) {
            idx = prev.findIndex((e) => e.role === "user" && e.transcribing);
          }
          if (idx < 0) return prev;
          return [...prev.slice(0, idx), ...prev.slice(idx + 1)];
        });
      } else if (t === "input_audio_buffer.speech_started") {
        clearNudgeTimer();
        setVoiceStatus("listening");
        setStatusText("Listening...");
        clearPlayback();
        agentTextRef.current = "";
        const itemId: string | undefined = event.item_id;
        // Reserve the user's chronological slot immediately. Server-side
        // STT can complete 1–2s later, well after the agent has started
        // replying — without this placeholder the user message would be
        // appended out of order and the agent bubble would split in two.
        setTranscript((prev) => {
          // Barge-in cleanup: finalize any trailing streaming agent
          // bubble so its blinking cursor stops once the user cuts in.
          let next = prev;
          const last = next[next.length - 1];
          if (last && last.role === "agent" && last.streaming) {
            next = [...next.slice(0, -1), { ...last, streaming: false }];
          }
          // De-dupe: ignore duplicate speech_started events for the
          // same item_id.
          if (
            itemId &&
            next.some((e) => e.transcribing && e.itemId === itemId)
          ) {
            return next;
          }
          return [
            ...next,
            { role: "user", text: "…", transcribing: true, itemId },
          ];
        });
      } else if (t === "response.audio.done") {
        // Delay status change until scheduled audio finishes playing
        const playCtx = playCtxRef.current;
        if (playCtx) {
          const remainingMs = Math.max(0, (nextPlayTimeRef.current - playCtx.currentTime) * 1000);
          setTimeout(() => {
            // Only switch if still in speaking state (user may have interrupted)
            if (wsRef.current) {
              setVoiceStatus("ready");
              setStatusText("Ready");
              startNudgeTimer();
            }
          }, remainingMs + 150); // +150ms buffer for scheduling jitter
        } else {
          setVoiceStatus("ready");
          setStatusText("Ready");
          startNudgeTimer();
        }
      } else if (
        t === "response.output_item.added" &&
        event.item?.type === "function_call"
      ) {
        const item = event.item;
        pendingCallsRef.current[item.call_id!] = {
          name: item.name!,
          call_id: item.call_id!,
          arguments: "",
        };
        setVoiceStatus("searching");
        setStatusText("Searching knowledge base...");
        setTranscript((prev) => [
          ...prev,
          { role: "system", text: "Searching knowledge base..." },
        ]);
      } else if (t === "response.function_call_arguments.delta") {
        if (pendingCallsRef.current[event.call_id!]) {
          pendingCallsRef.current[event.call_id!].arguments +=
            event.delta || "";
        }
      } else if (t === "response.function_call_arguments.done") {
        const pending = pendingCallsRef.current[event.call_id!];
        if (pending) {
          pending.arguments = event.arguments || pending.arguments;
          executeFunctionCall(pending);
        }
      } else if (t === "error") {
        console.error("Azure error:", event);
        setVoiceStatus("error");
        setStatusText("Error: " + (event.error?.message || "unknown"));
      }
    },
    [getToken],
  );

  // ── Start / stop / mic toggle ──

  const cleanupConnection = useCallback(() => {
    pendingCallsRef.current = {};
    agentTextRef.current = "";
    clearNudgeTimer();
    fillerLastIndexRef.current = -1;
    nudgeLastIndexRef.current = -1;
    sessionInitRef.current = false;
    const ws = wsRef.current;
    wsRef.current = null;
    if (ws && ws.readyState !== WebSocket.CLOSED) {
      try {
        ws.close();
      } catch {
        /* */
      }
    }
    if (workletRef.current) {
      workletRef.current.port.onmessage = null;
      workletRef.current.disconnect();
      workletRef.current = null;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close();
      audioCtxRef.current = null;
    }
    clearPlayback();
    if (playCtxRef.current) {
      playCtxRef.current.close();
      playCtxRef.current = null;
    }
    if (micStreamRef.current) {
      micStreamRef.current.getTracks().forEach((t) => t.stop());
      micStreamRef.current = null;
    }
  }, []);

  const start = useCallback(async () => {
    try {
      intentionalCloseRef.current = false;
      setCallState("connecting");
      setStatusText("Connecting...");
      const token = await getToken();
      if (token) sessionStorage.setItem("_voice_token", token);

      // Use prefetched config or fetch fresh
      let config = prefetchedConfigRef.current;
      if (!config) {
        const resp = await fetch("/api/voice-config", {
          headers: authHeaders(token),
        });
        config = await resp.json();
      }

      const micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: SAMPLE_RATE,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      micStreamRef.current = micStream;

      const audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
      audioCtxRef.current = audioCtx;

      const processorCode = `
        class CaptureProcessor extends AudioWorkletProcessor {
          constructor() { super(); this.ring = new Float32Array(4800); this.writePos = 0; this.available = 0; }
          process(inputs) {
            const ch = inputs[0]?.[0];
            if (!ch) return true;
            for (let i = 0; i < ch.length; i++) {
              this.ring[this.writePos] = ch[i];
              this.writePos = (this.writePos + 1) % this.ring.length;
              this.available++;
            }
            while (this.available >= ${CHUNK_SIZE}) {
              const readPos = (this.writePos - this.available + this.ring.length) % this.ring.length;
              const chunk = new Float32Array(${CHUNK_SIZE});
              for (let i = 0; i < ${CHUNK_SIZE}; i++) chunk[i] = this.ring[(readPos + i) % this.ring.length];
              this.available -= ${CHUNK_SIZE};
              this.port.postMessage(chunk);
            }
            return true;
          }
        }
        registerProcessor('capture-processor', CaptureProcessor);
      `;
      const blob = new Blob([processorCode], {
        type: "application/javascript",
      });
      const blobUrl = URL.createObjectURL(blob);
      await audioCtx.audioWorklet.addModule(blobUrl);
      URL.revokeObjectURL(blobUrl);

      const workletNode = new AudioWorkletNode(audioCtx, "capture-processor");
      workletRef.current = workletNode;
      audioCtx.createMediaStreamSource(micStream).connect(workletNode);

      playCtxRef.current = new AudioContext({ sampleRate: SAMPLE_RATE });
      nextPlayTimeRef.current = 0;

      // Connect to backend WS proxy via single-use ticket exchange.
      // We avoid putting the long-lived MSAL JWT in the WS query string
      // (which leaks to URL-logging intermediaries); instead the backend
      // mints an opaque, single-use, short-TTL ticket that's redeemed on
      // ws-open and immediately invalidated. On the rare 4001 caused by
      // a replica mismatch (ACA sticky sessions usually prevents this),
      // we retry once with a fresh ticket.
      const wsProto = location.protocol === "https:" ? "wss:" : "ws:";

      const openVoiceWs = async (): Promise<WebSocket> => {
        const tResp = await fetch("/api/voice/ticket", {
          method: "POST",
          headers: authHeaders(token),
        });
        if (!tResp.ok) {
          throw new Error(`voice ticket request failed: ${tResp.status}`);
        }
        const { ticket } = (await tResp.json()) as { ticket: string };
        const wsUrl = `${wsProto}//${location.host}${config!.wss_url}&ticket=${encodeURIComponent(ticket)}`;
        return new WebSocket(wsUrl);
      };

      const attachWsHandlers = (sock: WebSocket, allowRetry: boolean) => {
        sock.onopen = () => devLog("Azure WS connected" + (allowRetry ? "" : " (retry)"));
        sock.onmessage = (e) => handleEvent(JSON.parse(e.data));
        sock.onerror = (e) => {
          console.error("Azure WS error:", e);
          setVoiceStatus("error");
          setStatusText("Connection error");
        };
        sock.onclose = async (e) => {
          devLog("Azure WS closed:", e.code, e.reason);
          const wasCurrent = wsRef.current === sock;
          const wasIntentional = intentionalCloseRef.current;
          if (wasCurrent && !wasIntentional && e.code === 4001 && allowRetry) {
            devLog("Voice WS rejected (4001) — retrying once with fresh ticket");
            try {
              const retryWs = await openVoiceWs();
              wsRef.current = retryWs;
              attachWsHandlers(retryWs, false);
              return;
            } catch (err) {
              console.error("Voice WS retry failed:", err);
            }
          }
          cleanupConnection();
          if (!wasCurrent || wasIntentional) {
            intentionalCloseRef.current = false;
            return;
          }
          setCallState("error");
          setVoiceStatus("error");
          setStatusText(`Connection closed (${e.code}${e.reason ? `: ${e.reason}` : ""})`);
        };
      };

      const ws = await openVoiceWs();
      wsRef.current = ws;
      attachWsHandlers(ws, true);
    } catch (err) {
      console.error(err);
      cleanupConnection();
      setCallState("error");
      setVoiceStatus("error");
      setStatusText("Error: " + (err as Error).message);
    }
  }, [cleanupConnection, getToken, handleEvent]);

  const stop = useCallback(() => {
    intentionalCloseRef.current = true;
    setCallState("idle");
    setVoiceStatus("ready");
    setStatusText("Ready");
    setMicEnabled(true);
    cleanupConnection();
  }, [cleanupConnection]);

  const toggleMic = useCallback(() => {
    if (!micStreamRef.current) return;
    const next = !micEnabled;
    setMicEnabled(next);
    micStreamRef.current.getAudioTracks().forEach((t) => (t.enabled = next));
  }, [micEnabled]);

  const sendText = useCallback(() => {
    const msg = textInput.trim();
    if (!msg || callState !== "connected") return;
    wsSend({
      type: "conversation.item.create",
      item: {
        type: "message",
        role: "user",
        content: [{ type: "input_text", text: msg }],
      },
    });
    wsSend({ type: "response.create" });
    setTranscript((prev) => [...prev, { role: "user", text: msg }]);
    setTextInput("");
  }, [textInput, callState]);

  const login = () => instance.loginRedirect(loginRequest);
  const logout = () => {
    const account = accounts[0];
    if (account) {
      instance.clearCache();
    }
    sessionStorage.clear();
    window.location.reload();
  };

  // Don't render the full app until auth is resolved — but never return null,
  // since a silent failure here is what causes the dreaded blank white page.
  // Show a status / error UI instead so the user (and we) can see what's wrong.
  if (!isAuthenticated) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100vh",
          padding: 24,
          gap: 20,
          textAlign: "center",
        }}
      >
        <Text size={600} weight="semibold">
          {import.meta.env.VITE_APP_TITLE || "Voice Agent"}
        </Text>
        {authError ? (
          <>
            <MessageBar intent="error" style={{ maxWidth: 640 }}>
              <MessageBarBody>
                <MessageBarTitle>Sign-in failed</MessageBarTitle>
                <div>{authError}</div>
                <div style={{ marginTop: 8, fontSize: 12, opacity: 0.85 }}>
                  Check that <code>VITE_MSAL_CLIENT_ID</code> and <code>VITE_MSAL_TENANT_ID</code> are set in <code>frontend/.env</code>.
                  If you're loading <code>/tribe</code> or <code>/login</code>, you also need <code>VITE_MSAL_TRIBE_CLIENT_ID</code> and <code>VITE_MSAL_TRIBE_TENANT_ID</code>.
                </div>
              </MessageBarBody>
            </MessageBar>
            <Button
              appearance="primary"
              onClick={() => {
                setAuthError(null);
                instance.loginRedirect(loginRequest).catch((err: unknown) => {
                  const e = err as { errorCode?: string; errorMessage?: string; message?: string };
                  setAuthError(e?.errorCode || e?.errorMessage || e?.message || String(err));
                });
              }}
            >
              Retry sign-in
            </Button>
          </>
        ) : (
          <>
            <Text size={300}>Signing you in…</Text>
            <Button
              appearance="subtle"
              onClick={() => instance.loginRedirect(loginRequest).catch((err: unknown) => {
                const e = err as { errorCode?: string; errorMessage?: string; message?: string };
                setAuthError(e?.errorCode || e?.errorMessage || e?.message || String(err));
              })}
            >
              Sign in with Microsoft
            </Button>
          </>
        )}
      </div>
    );
  }

  // ── Landing page (idle state) ──
  if (callState === "idle") {
    return (
      <>
        <header className="app-header">
          <Text weight="semibold" size={400} className="logo-text">
            {import.meta.env.VITE_APP_TITLE || "Voice Agent"}
          </Text>
          <div className="header-right">
            {/* Seismic toggle hidden for now
            <Switch
              checked={seismicEnabled}
              onChange={(_, data) => setSeismicEnabled(data.checked)}
              label={`Seismic content (preview) ${seismicEnabled ? "on" : "off"}`}
            />
            */}
            {/* Help button hidden for now
            <Tooltip content="Help" relationship="label">
              <Button
                appearance="subtle"
                icon={<QuestionCircle24Regular />}
                size="small"
              />
            </Tooltip>
            */}
            <FeedbackButton />
            {isAuthenticated && accounts[0] ? (
              <div className="user-menu-container" style={{ position: "relative" }}>
                <button
                  type="button"
                  onClick={() => setShowUserMenu((v) => !v)}
                  style={{ background: "none", border: "none", padding: 0, cursor: "pointer" }}
                >
                  <Avatar
                    name={accounts[0].name}
                    size={32}
                    badge={{ status: "available" }}
                  />
                </button>
                {showUserMenu && (
                  <div
                    style={{
                      position: "absolute",
                      top: "100%",
                      right: 0,
                      marginTop: 4,
                      background: "var(--colorNeutralBackground1)",
                      border: "1px solid var(--colorNeutralStroke1)",
                      borderRadius: 8,
                      boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
                      zIndex: 1000,
                      minWidth: 140,
                      padding: "4px 0",
                    }}
                  >
                    <button
                      type="button"
                      onClick={logout}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        width: "100%",
                        padding: "8px 12px",
                        background: "none",
                        border: "none",
                        cursor: "pointer",
                        fontSize: 14,
                        color: "var(--colorNeutralForeground1)",
                      }}
                      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--colorNeutralBackground1Hover)")}
                      onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
                    >
                      <SignOut24Regular /> Sign out
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <Button appearance="subtle" size="small" onClick={login}>
                Sign in
              </Button>
            )}
          </div>
        </header>

        {showBanner && (
          <MessageBar intent="info" className="info-banner">
            <MessageBarBody>
              <MessageBarTitle>Descriptive title</MessageBarTitle>
              Message providing information to the user with actionable insights.{" "}
              <Link href="#">Link</Link>
            </MessageBarBody>
            <MessageBarActions
              containerAction={
                <Button
                  appearance="transparent"
                  icon={<Dismiss24Regular />}
                  onClick={() => setShowBanner(false)}
                />
              }
            >
              <Button appearance="outline" size="small">
                Action
              </Button>
              <Button appearance="outline" size="small">
                Action
              </Button>
            </MessageBarActions>
          </MessageBar>
        )}

        <main className="landing-page">
          <div className="landing-content">
            <Text as="h1" size={900} weight="semibold" className="landing-title">
              {import.meta.env.VITE_APP_TITLE || "Voice Agent"}
            </Text>

            <Text as="h2" size={400} weight="semibold" className="landing-section-title">
              Overview
            </Text>

            <Text as="p" size={400} className="landing-text">
              {import.meta.env.VITE_APP_DESCRIPTION || "Voice Agent is an AI-powered coaching assistant built on Azure AI Foundry. It provides interactive, real-time guidance through natural voice conversations."}
            </Text>
            <Text as="p" size={400} className="landing-text">
              {import.meta.env.VITE_APP_DESCRIPTION_2 || "Ask questions about Azure AI Foundry, get help building AI solutions, or explore platform capabilities through a personalized coaching experience."}
            </Text>

            <Text as="p" weight="bold" size={400} className="landing-warning">
              This is an internal demo.
            </Text>

            <Text as="p" size={400} className="landing-text">
              This agent is for demonstration purposes only.
            </Text>

            <Button
              appearance="primary"
              shape="circular"
              size="large"
              className="talk-btn"
              onClick={isAuthenticated ? start : login}
              disabled={callState as string === "connecting"}
            >
              {import.meta.env.VITE_APP_BUTTON_LABEL || "Talk to Voice Agent"}
            </Button>

            <Text size={200} className="disclaimer">
              AI-generated content may be incorrect
            </Text>
          </div>
        </main>

        <AppFooter />
      </>
    );
  }

  // ── Conversation page (connecting/connected state) ──
  return (
    <div className="conversation-bg">
      <header className="app-header conversation-header">
        <Text weight="semibold" size={400} className="logo-text">
          {import.meta.env.VITE_APP_TITLE || "Voice Agent"}<span className="header-separator">| Conversation</span>
        </Text>
        <div className="header-right">
          <FeedbackButton />
          {isAuthenticated && accounts[0] && (
            <div className="user-menu-container" style={{ position: "relative" }}>
              <button
                type="button"
                onClick={() => setShowUserMenu((v) => !v)}
                style={{ background: "none", border: "none", padding: 0, cursor: "pointer" }}
              >
                <Avatar
                  name={accounts[0].name}
                  size={32}
                  badge={{ status: "available" }}
                />
              </button>
              {showUserMenu && (
                <div
                  style={{
                    position: "absolute",
                    top: "100%",
                    right: 0,
                    marginTop: 4,
                    background: "var(--colorNeutralBackground1)",
                    border: "1px solid var(--colorNeutralStroke1)",
                    borderRadius: 8,
                    boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
                    zIndex: 1000,
                    minWidth: 140,
                    padding: "4px 0",
                  }}
                >
                  <button
                    type="button"
                    onClick={logout}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      width: "100%",
                      padding: "8px 12px",
                      background: "none",
                      border: "none",
                      cursor: "pointer",
                      fontSize: 14,
                      color: "var(--colorNeutralForeground1)",
                    }}
                    onMouseEnter={(e) => (e.currentTarget.style.background = "var(--colorNeutralBackground1Hover)")}
                    onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
                  >
                    <SignOut24Regular /> Sign out
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </header>

      <div className="app-body">
        <main className="conversation-page">
          <div className="conversation-status">
            <Text size={600} weight="regular" className="status-text">
              {voiceStatus === "listening" || voiceStatus === "ready"
                ? "I'm listening"
                : voiceStatus === "speaking"
                  ? "Responding"
                  : voiceStatus === "searching"
                    ? "Searching knowledge base..."
                    : statusText}
            </Text>
            {voiceStatus !== "listening" && voiceStatus !== "ready" && (
              <div className={`audio-viz ${voiceStatus === "speaking" ? "speaking" : "idle"}`}>
                <span className="viz-shape shape-1" />
                <span className="viz-shape shape-2" />
                <span className="viz-shape shape-3" />
                <span className="viz-shape shape-4" />
                <span className="viz-shape shape-5" />
                <span className="viz-shape shape-6" />
                <span className="viz-shape shape-7" />
              </div>
            )}
          </div>

          <div className="control-bar">
            <div className="ctrl-left">
              <button
                type="button"
                className={`ctrl-icon-bare mic-btn ${!micEnabled ? "muted" : ""}`}
                onClick={toggleMic}
              >
                {micEnabled ? <Mic24Filled /> : <MicOff24Filled />}
                <span className="ctrl-label mic-label">{micEnabled ? "Mic on" : "Mic off"}</span>
              </button>
              <button
                type="button"
                className="ctrl-icon-bare leave-btn"
                onClick={stop}
              >
                <DoorArrowLeft24Filled />
                <span className="ctrl-label leave-label">Leave</span>
              </button>
            </div>

            <span className="ctrl-divider" />

            <div className="ctrl-mid">
              <Input
                placeholder="Write message"
                value={textInput}
                onChange={(_, data) => setTextInput(data.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") sendText();
                }}
                className="chat-input"
                contentAfter={
                  <Button
                    appearance="transparent"
                    icon={<Send24Regular />}
                    size="small"
                    onClick={sendText}
                    disabled={!textInput.trim()}
                  />
                }
              />
              <Text className="ctrl-disclaimer">
                AI-generated content may be incorrect
              </Text>
            </div>

            <div className="ctrl-right">
              <button type="button" className="ctrl-icon-bare" title="Subtitles" onClick={() => setShowTranscript(prev => !prev)}>
                <Subtitles24Regular />
              </button>
              <button type="button" className="ctrl-icon-bare" title="Notifications">
                <Alert24Regular />
              </button>
            </div>
          </div>
        </main>

        {/* Transcript panel */}
        <div className={`transcript-panel${showTranscript ? ' open' : ''}`}>
          <div className="transcript-header">
            <span className="transcript-title">Transcript</span>
            <button type="button" className="transcript-close" onClick={() => setShowTranscript(false)}>
              <Dismiss24Regular />
            </button>
          </div>
          <div className="transcript-body">
            {transcript.map((entry, i) => (
              <div key={i} className="t-group">
                <div className="t-speaker-row">
                  <Text
                    weight="semibold"
                    size={300}
                    className={`t-speaker ${entry.role}`}
                  >
                    {entry.role === "user"
                      ? (accounts[0]?.name?.split(" ")[0] || "You")
                      : entry.role === "agent"
                        ? (import.meta.env.VITE_APP_TITLE || "Voice Agent")
                        : "System"}
                  </Text>
                  {entry.role === "agent" && (
                    <span className="ai-badge">AI-generated content may be incorrect</span>
                  )}
                </div>
                <Text
                  size={300}
                  className={`t-text ${entry.role} ${entry.streaming ? "streaming" : ""} ${entry.transcribing ? "transcribing" : ""}`}
                >
                  {entry.text}
                </Text>
              </div>
            ))}
            {transcript.length === 0 && (
              <Text size={300} style={{ color: "#a0a0a0" }}>
                Transcript will appear here once the conversation starts.
              </Text>
            )}
            <div ref={transcriptEndRef} />
          </div>
        </div>
      </div>

      <AppFooter className="app-footer conversation-footer" />
    </div>
  );
}
