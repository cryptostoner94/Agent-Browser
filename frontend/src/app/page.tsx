"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  Bot,
  Braces,
  ChevronRight,
  Cloud,
  Code2,
  Database,
  File,
  Folder,
  Gauge,
  Globe2,
  HardDrive,
  Loader2,
  Play,
  RefreshCcw,
  ShieldCheck,
  Sparkles,
  TerminalSquare,
  Wand2
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type RootName = "local" | "icloud" | "exports";

type FileNode = {
  name: string;
  path: string;
  kind: "file" | "directory" | "symlink" | "other";
  size: number;
  modified_at: number;
  readonly: boolean;
  children?: FileNode[] | null;
};

type Metrics = {
  uptime_seconds: number;
  token_totals: {
    prompt_tokens_estimate: number;
    completion_tokens_estimate: number;
    requests: number;
    failures: number;
  };
  filesystem: Record<string, { path: string; exists: boolean; readable: boolean; writable: boolean }>;
  skyvern: { configured: boolean; compliance_mode: boolean; allowed_domains: string[] };
  self_evolution: { enabled: boolean; patch_dir: string; allowed_roots: string[] };
  llm_usage: Record<string, { provider: string; model: string; requests: number; failures: number; average_latency_ms: number }>;
};

type PatchEvent = {
  timestamp: number;
  status: string;
  file_path: string;
  backup_path: string;
  message: string;
  diff: string;
};

const gatewayUrl = process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8088";
const wsUrl = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8088/ws/events";
const browserStreamUrl = process.env.NEXT_PUBLIC_BROWSER_STREAM_URL || process.env.NEXT_PUBLIC_VNC_URL || "";

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes)) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatTime(epochSeconds: number) {
  if (!epochSeconds) return "never";
  return new Date(epochSeconds * 1000).toLocaleString();
}

async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${gatewayUrl}${path}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<T>;
}

async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${gatewayUrl}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<T>;
}

function StatCard({
  icon,
  label,
  value,
  sub
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub: string;
}) {
  return (
    <div className="rounded-3xl border border-white/10 bg-white/[0.035] p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="rounded-2xl border border-cyan-300/20 bg-cyan-300/10 p-2 text-cyan-100">{icon}</div>
        <div className="h-1.5 w-16 origin-right animate-pulseLine rounded-full bg-gradient-to-r from-cyan-300 to-violet-400" />
      </div>
      <div className="text-2xl font-semibold text-white">{value}</div>
      <div className="mt-1 text-xs uppercase tracking-[0.22em] text-slate-400">{label}</div>
      <div className="mt-2 text-xs text-slate-500">{sub}</div>
    </div>
  );
}

function FileTreeNode({
  node,
  onSelect,
  depth = 0
}: {
  node: FileNode;
  onSelect: (node: FileNode) => void;
  depth?: number;
}) {
  const isDir = node.kind === "directory";
  return (
    <div>
      <button
        onClick={() => onSelect(node)}
        className="flex w-full items-center gap-2 rounded-xl px-2 py-1.5 text-left text-sm text-slate-200 transition hover:bg-white/10"
        style={{ paddingLeft: `${8 + depth * 14}px` }}
      >
        {isDir ? <Folder className="h-4 w-4 text-cyan-200" /> : <File className="h-4 w-4 text-violet-200" />}
        <span className="min-w-0 flex-1 truncate">{node.name || "/"}</span>
        <span className="text-[10px] text-slate-500">{isDir ? "dir" : formatBytes(node.size)}</span>
      </button>
      {isDir && node.children?.map((child) => (
        <FileTreeNode key={`${child.path}-${child.name}`} node={child} onSelect={onSelect} depth={depth + 1} />
      ))}
    </div>
  );
}

export default function Page() {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [root, setRoot] = useState<RootName>("local");
  const [currentPath, setCurrentPath] = useState("");
  const [tree, setTree] = useState<FileNode | null>(null);
  const [selectedFile, setSelectedFile] = useState<{ path: string; text: string; binary: boolean } | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [patches, setPatches] = useState<PatchEvent[]>([]);
  const [prompt, setPrompt] = useState("Analyze the selected workspace status and propose the safest next action.");
  const [agentAnswer, setAgentAnswer] = useState("");
  const [browserUrl, setBrowserUrl] = useState("https://news.ycombinator.com");
  const [browserGoal, setBrowserGoal] = useState("Open the page and extract the top visible story title.");
  const [busy, setBusy] = useState<string>("");

  const refreshMetrics = useCallback(async () => {
    const data = await apiGet<Metrics>("/api/metrics");
    setMetrics(data);
  }, []);

  const refreshTree = useCallback(async () => {
    const encoded = encodeURIComponent(currentPath);
    const data = await apiGet<FileNode>(`/api/files/tree?root=${root}&path=${encoded}&depth=2`);
    setTree(data);
  }, [currentPath, root]);

  const refreshLogs = useCallback(async () => {
    const data = await apiGet<{ lines: string[] }>("/api/logs?limit=80");
    setLogs(data.lines);
  }, []);

  const refreshPatches = useCallback(async () => {
    const data = await apiGet<{ events: PatchEvent[] }>("/api/evolution/events?limit=10");
    setPatches(data.events);
  }, []);

  const refreshAll = useCallback(async () => {
    setBusy("refresh");
    try {
      await Promise.all([refreshMetrics(), refreshTree(), refreshLogs(), refreshPatches()]);
    } finally {
      setBusy("");
    }
  }, [refreshMetrics, refreshTree, refreshLogs, refreshPatches]);

  useEffect(() => {
    refreshAll().catch((error) => setLogs((old) => [`refresh error: ${String(error)}`, ...old]));
  }, [refreshAll]);

  useEffect(() => {
    const socket = new WebSocket(wsUrl);
    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "metrics") {
          setMetrics(payload.metrics);
        }
      } catch {
        return;
      }
    };
    socket.onerror = () => setLogs((old) => [`websocket error at ${new Date().toLocaleTimeString()}`, ...old.slice(-79)]);
    return () => socket.close();
  }, []);

  const rootButtons = useMemo(
    () => [
      { id: "local" as const, label: "Mac Local", icon: <HardDrive className="h-4 w-4" /> },
      { id: "icloud" as const, label: "iCloud Drive", icon: <Cloud className="h-4 w-4" /> },
      { id: "exports" as const, label: "Exports", icon: <Database className="h-4 w-4" /> }
    ],
    []
  );

  async function selectNode(node: FileNode) {
    if (node.kind === "directory") {
      setCurrentPath(node.path);
      setSelectedFile(null);
      return;
    }
    if (node.kind === "file") {
      setBusy("read");
      try {
        const result = await apiPost<{ path: string; text: string; binary: boolean }>("/api/files/read", {
          root,
          path: node.path
        });
        setSelectedFile(result);
      } finally {
        setBusy("");
      }
    }
  }

  async function runAgent() {
    setBusy("agent");
    try {
      const context = selectedFile?.text
        ? `${prompt}\n\nSelected file: ${selectedFile.path}\n\n${selectedFile.text.slice(0, 8000)}`
        : prompt;
      const result = await apiPost<{ text: string }>("/api/agent/query", {
        prompt: context,
        task_type: selectedFile ? "analysis" : "general",
        requires_json: false
      });
      setAgentAnswer(result.text);
      await refreshMetrics();
      await refreshLogs();
    } finally {
      setBusy("");
    }
  }

  async function runBrowserTask() {
    setBusy("browser");
    try {
      const result = await apiPost<{ status: string; result: Record<string, unknown>; saved_export: Record<string, unknown> }>(
        "/api/browser/task",
        {
          url: browserUrl,
          goal: browserGoal,
          data_extraction_goal: browserGoal,
          max_steps: 20,
          wait: true
        }
      );
      setAgentAnswer(JSON.stringify(result, null, 2));
      await refreshAll();
    } finally {
      setBusy("");
    }
  }

  async function runEvolution() {
    setBusy("evolve");
    try {
      const result = await apiPost<PatchEvent>("/api/evolution/run", {
        error_context: logs.slice(-20).join("\n")
      });
      setPatches((old) => [result, ...old].slice(0, 10));
      await refreshAll();
    } finally {
      setBusy("");
    }
  }

  const promptTokens = metrics?.token_totals.prompt_tokens_estimate ?? 0;
  const completionTokens = metrics?.token_totals.completion_tokens_estimate ?? 0;
  const apiRequests = metrics?.token_totals.requests ?? 0;
  const apiFailures = metrics?.token_totals.failures ?? 0;

  return (
    <main className="min-h-screen overflow-hidden bg-abyss bg-radial text-slate-100">
      <div className="pointer-events-none fixed inset-0 bg-grid bg-[length:34px_34px] opacity-50" />
      <div className="pointer-events-none fixed left-1/2 top-10 h-64 w-64 -translate-x-1/2 animate-drift rounded-full bg-cyan-400/10 blur-3xl" />

      <section className="relative mx-auto flex max-w-[1800px] flex-col gap-5 px-4 py-5 sm:px-6 lg:px-8">
        <header className="glass-panel neon-border flex flex-col gap-4 rounded-3xl p-5 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <Badge className="border-lime-300/30 bg-lime-300/10 text-lime-100">
                <ShieldCheck className="mr-1 h-3.5 w-3.5" />
                Guarded Production Mesh
              </Badge>
              <Badge>
                <Sparkles className="mr-1 h-3.5 w-3.5" />
                NexusOS-Ultra
              </Badge>
            </div>
            <h1 className="mt-4 text-3xl font-semibold tracking-tight text-white md:text-5xl">
              AI Workspace <span className="text-cyan-200">+</span> Cloud Browser Command Deck
            </h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-400">
              Local Mac and iCloud file mesh, Gemini/Groq routing, Skyvern remote browser tasks, live metrics,
              and controlled self-evolution with patch visibility.
            </p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Button onClick={refreshAll} disabled={Boolean(busy)}>
              {busy === "refresh" ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCcw className="h-4 w-4" />}
              Sync Deck
            </Button>
            <Button variant="ghost" onClick={runEvolution} disabled={Boolean(busy)}>
              {busy === "evolve" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />}
              Run Self-Check
            </Button>
          </div>
        </header>

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <StatCard icon={<Gauge className="h-5 w-5" />} label="API Requests" value={String(apiRequests)} sub={`${apiFailures} failures recorded`} />
          <StatCard icon={<Braces className="h-5 w-5" />} label="Prompt Tokens" value={promptTokens.toLocaleString()} sub="estimated routing usage" />
          <StatCard icon={<Activity className="h-5 w-5" />} label="Completion Tokens" value={completionTokens.toLocaleString()} sub="estimated response usage" />
          <StatCard
            icon={<Globe2 className="h-5 w-5" />}
            label="Skyvern"
            value={metrics?.skyvern.configured ? "Live" : "Key Needed"}
            sub={metrics?.skyvern.compliance_mode ? "compliance mode active" : "compliance mode blocked"}
          />
        </div>

        <div className="grid gap-5 xl:grid-cols-[420px_minmax(0,1fr)_460px]">
          <Card className="min-h-[720px]">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Folder className="h-5 w-5 text-cyan-200" />
                macOS Filesystem Mesh
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="mb-4 grid grid-cols-3 gap-2">
                {rootButtons.map((button) => (
                  <button
                    key={button.id}
                    onClick={() => {
                      setRoot(button.id);
                      setCurrentPath("");
                      setSelectedFile(null);
                    }}
                    className={`flex items-center justify-center gap-2 rounded-2xl border px-3 py-2 text-xs transition ${
                      root === button.id
                        ? "border-cyan-300/50 bg-cyan-300/15 text-cyan-100"
                        : "border-white/10 bg-white/5 text-slate-300 hover:bg-white/10"
                    }`}
                  >
                    {button.icon}
                    {button.label}
                  </button>
                ))}
              </div>

              <div className="mb-3 rounded-2xl border border-white/10 bg-black/20 p-3 text-xs text-slate-400">
                <div className="flex items-center gap-2">
                  <ChevronRight className="h-4 w-4 text-cyan-200" />
                  <span className="truncate">{currentPath || "/"}</span>
                </div>
              </div>

              <div className="cyber-scrollbar h-[390px] overflow-auto rounded-2xl border border-white/10 bg-black/20 p-2">
                {tree ? (
                  <FileTreeNode node={tree} onSelect={selectNode} />
                ) : (
                  <div className="flex h-full items-center justify-center text-sm text-slate-500">No tree loaded.</div>
                )}
              </div>

              <div className="mt-4 rounded-2xl border border-white/10 bg-black/20 p-3">
                <div className="mb-2 flex items-center justify-between">
                  <span className="text-sm font-medium text-white">Selected file</span>
                  {busy === "read" && <Loader2 className="h-4 w-4 animate-spin text-cyan-200" />}
                </div>
                <pre className="cyber-scrollbar max-h-[190px] overflow-auto whitespace-pre-wrap text-xs leading-5 text-slate-300">
                  {selectedFile
                    ? selectedFile.binary
                      ? `${selectedFile.path}\nBinary file preview blocked.`
                      : `${selectedFile.path}\n\n${selectedFile.text.slice(0, 5000)}`
                    : "Select a file to preview readable text."}
                </pre>
              </div>
            </CardContent>
          </Card>

          <div className="flex flex-col gap-5">
            <Card className="min-h-[430px]">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Globe2 className="h-5 w-5 text-violet-200" />
                  Visual Browser Stream
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="relative h-[300px] overflow-hidden rounded-3xl border border-cyan-300/20 bg-black/50">
                  {browserStreamUrl ? (
                    <iframe title="Browser stream" src={browserStreamUrl} className="h-full w-full" />
                  ) : (
                    <div className="flex h-full flex-col items-center justify-center gap-3 bg-[radial-gradient(circle_at_center,rgba(41,243,255,.16),transparent_38%)] text-center">
                      <div className="rounded-full border border-cyan-300/25 bg-cyan-300/10 p-4 shadow-glow">
                        <TerminalSquare className="h-10 w-10 text-cyan-100" />
                      </div>
                      <div className="text-lg font-semibold text-white">Stream slot ready</div>
                      <div className="max-w-md text-sm text-slate-400">
                        Set NEXT_PUBLIC_BROWSER_STREAM_URL or NEXT_PUBLIC_VNC_URL in .env to embed a live remote browser view.
                      </div>
                    </div>
                  )}
                  <div className="absolute left-4 top-4 rounded-full border border-lime-300/30 bg-lime-300/10 px-3 py-1 text-xs text-lime-100">
                    Authorized tasks only
                  </div>
                </div>

                <div className="mt-4 grid gap-3 lg:grid-cols-[1fr_1fr_auto]">
                  <input
                    value={browserUrl}
                    onChange={(event) => setBrowserUrl(event.target.value)}
                    className="rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-white outline-none ring-cyan-300/30 focus:ring-2"
                    placeholder="https://example.com"
                  />
                  <input
                    value={browserGoal}
                    onChange={(event) => setBrowserGoal(event.target.value)}
                    className="rounded-2xl border border-white/10 bg-white/5 px-4 py-3 text-sm text-white outline-none ring-cyan-300/30 focus:ring-2"
                    placeholder="Describe the browser task"
                  />
                  <Button onClick={runBrowserTask} disabled={Boolean(busy)}>
                    {busy === "browser" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                    Dispatch
                  </Button>
                </div>
              </CardContent>
            </Card>

            <Card className="min-h-[390px]">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Bot className="h-5 w-5 text-cyan-200" />
                  Cognitive Reasoning Layer
                </CardTitle>
              </CardHeader>
              <CardContent>
                <textarea
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                  className="h-28 w-full rounded-3xl border border-white/10 bg-black/30 p-4 text-sm leading-6 text-white outline-none ring-cyan-300/30 focus:ring-2"
                />
                <div className="mt-3 flex flex-wrap items-center gap-3">
                  <Button onClick={runAgent} disabled={Boolean(busy)}>
                    {busy === "agent" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                    Run Agent
                  </Button>
                  <Badge className="border-violet-300/30 bg-violet-300/10 text-violet-100">
                    Gemini heavy context / Groq fast loop
                  </Badge>
                </div>
                <pre className="cyber-scrollbar mt-4 max-h-[200px] overflow-auto whitespace-pre-wrap rounded-2xl border border-white/10 bg-black/30 p-4 text-xs leading-5 text-slate-300">
                  {agentAnswer || "Agent output will appear here."}
                </pre>
              </CardContent>
            </Card>
          </div>

          <div className="flex flex-col gap-5">
            <Card className="min-h-[350px]">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Activity className="h-5 w-5 text-lime-200" />
                  Metric Log Window
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-2 gap-3 text-xs">
                  {metrics?.llm_usage &&
                    Object.entries(metrics.llm_usage).map(([name, route]) => (
                      <div key={name} className="rounded-2xl border border-white/10 bg-black/20 p-3">
                        <div className="font-medium text-white">{name}</div>
                        <div className="mt-1 truncate text-slate-400">{route.model}</div>
                        <div className="mt-2 text-cyan-100">{route.requests} req / {route.average_latency_ms} ms avg</div>
                      </div>
                    ))}
                </div>
                <pre className="cyber-scrollbar mt-4 h-[155px] overflow-auto whitespace-pre-wrap rounded-2xl border border-white/10 bg-black/30 p-3 text-[11px] leading-5 text-slate-400">
                  {logs.length ? logs.join("\n") : "No gateway log events yet."}
                </pre>
              </CardContent>
            </Card>

            <Card className="min-h-[350px]">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Code2 className="h-5 w-5 text-rose-200" />
                  Self-Evolution Git Diff
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="mb-3 flex items-center gap-2">
                  <Badge className={metrics?.self_evolution.enabled ? "border-lime-300/30 bg-lime-300/10 text-lime-100" : "border-amber-300/30 bg-amber-300/10 text-amber-100"}>
                    {metrics?.self_evolution.enabled ? "Enabled" : "Disabled by default"}
                  </Badge>
                  <span className="text-xs text-slate-500">Patch writes are project-scoped and backed up.</span>
                </div>
                <div className="cyber-scrollbar h-[245px] overflow-auto rounded-2xl border border-white/10 bg-black/30 p-3">
                  {patches.length ? (
                    patches.map((patch) => (
                      <div key={`${patch.timestamp}-${patch.file_path}`} className="mb-4 rounded-2xl border border-white/10 bg-white/[0.03] p-3">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="text-sm font-medium text-white">{patch.status}</span>
                          <span className="text-[11px] text-slate-500">{formatTime(patch.timestamp)}</span>
                        </div>
                        <div className="mt-1 break-all text-xs text-cyan-200">{patch.file_path || "no file patched"}</div>
                        <div className="mt-2 text-xs text-slate-400">{patch.message}</div>
                        <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap rounded-xl bg-black/40 p-2 text-[11px] leading-4 text-slate-300">
                          {patch.diff || "No diff recorded."}
                        </pre>
                      </div>
                    ))
                  ) : (
                    <div className="flex h-full items-center justify-center text-sm text-slate-500">
                      Patch events will appear here after a self-check.
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Database className="h-5 w-5 text-violet-200" />
                  Mount Status
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-2 text-xs">
                  {metrics?.filesystem &&
                    Object.entries(metrics.filesystem).map(([name, status]) => (
                      <div key={name} className="rounded-2xl border border-white/10 bg-black/20 p-3">
                        <div className="flex items-center justify-between">
                          <span className="font-medium text-white">{name}</span>
                          <span className={status.exists && status.readable ? "text-lime-200" : "text-rose-200"}>
                            {status.exists && status.readable ? "online" : "check access"}
                          </span>
                        </div>
                        <div className="mt-1 break-all text-slate-500">{status.path}</div>
                      </div>
                    ))}
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
      </section>
    </main>
  );
}
