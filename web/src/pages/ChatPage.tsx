import { Button } from "@nous-research/ui/ui/components/button";
import {
  Check,
  Copy,
  Cpu,
  Loader2,
  MessageSquare,
  Paperclip,
  Plus,
  RefreshCw,
  RotateCcw,
  Send,
  Square,
  Trash2,
  X,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { useSearchParams } from "react-router-dom";

import { Markdown } from "@/components/Markdown";
import { ModelPickerDialog } from "@/components/ModelPickerDialog";
import { usePageHeader } from "@/contexts/usePageHeader";
import {
  api,
  type ModelOptionsResponse,
  type SessionMessage,
  type WebChatAttachment,
  type WebChatSendRequest,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { PluginSlot } from "@/plugins";

interface SelectedModel {
  provider: string;
  model: string;
}

interface FailedRequest {
  text: string;
  attachments: WebChatAttachment[];
}

function messageText(message: SessionMessage): string {
  if (typeof message.content === "string") return message.content;
  if (message.content == null) return "";
  return String(message.content);
}

function compactSessionLabel(sessionId: string | null): string {
  if (!sessionId) return "New chat";
  if (sessionId.length <= 18) return sessionId;
  return `${sessionId.slice(0, 10)}...${sessionId.slice(-6)}`;
}

function latestAssistantText(messages: SessionMessage[]): string {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (msg.role === "assistant") return messageText(msg);
  }
  return "";
}

function latestUserText(messages: SessionMessage[]): string {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (msg.role === "user") return messageText(msg);
  }
  return "";
}

function initialModelFromOptions(payload: ModelOptionsResponse): SelectedModel | null {
  const providers = payload.providers ?? [];
  const configuredProvider = providers.find(
    (provider) =>
      provider.slug === payload.provider &&
      (provider.models ?? []).includes(String(payload.model ?? "")),
  );
  if (configuredProvider && payload.model) {
    return { provider: configuredProvider.slug, model: String(payload.model) };
  }
  const fallback = providers.find((provider) => (provider.models ?? []).length > 0);
  const model = fallback?.models?.[0];
  return fallback && model ? { provider: fallback.slug, model } : null;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatError(err: unknown): string {
  if (!(err instanceof Error)) return "Chat request failed";
  try {
    const jsonStart = err.message.indexOf("{");
    if (jsonStart >= 0) {
      const parsed = JSON.parse(err.message.slice(jsonStart)) as { detail?: string };
      if (parsed.detail) return parsed.detail;
    }
  } catch {
    /* keep original message */
  }
  return err.message;
}

export default function ChatPage({ isActive = true }: { isActive?: boolean }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const resumeParam = searchParams.get("resume");
  const [sessionId, setSessionId] = useState<string | null>(resumeParam);
  const [messages, setMessages] = useState<SessionMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copyState, setCopyState] = useState<"idle" | "copied">("idle");
  const [selectedModel, setSelectedModel] = useState<SelectedModel | null>(null);
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [attachments, setAttachments] = useState<WebChatAttachment[]>([]);
  const [lastFailedRequest, setLastFailedRequest] = useState<FailedRequest | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const copyResetRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const activeRequestSessionRef = useRef<string | null>(null);
  const { setEnd } = usePageHeader();

  const visibleMessages = useMemo(
    () => messages.filter((message) => message.role === "user" || message.role === "assistant"),
    [messages],
  );

  const assistantText = useMemo(() => latestAssistantText(messages), [messages]);
  const sessionLabel = useMemo(() => compactSessionLabel(sessionId), [sessionId]);
  const modelLabel = selectedModel
    ? `${selectedModel.model} (${selectedModel.provider})`
    : "No model selected";
  const canSend = !sending && !uploading && (input.trim().length > 0 || attachments.length > 0);

  const copyText = useCallback(async (text: string) => {
    if (!text) return;
    await navigator.clipboard.writeText(text);
    setCopyState("copied");
    if (copyResetRef.current) clearTimeout(copyResetRef.current);
    copyResetRef.current = setTimeout(() => setCopyState("idle"), 1400);
  }, []);

  const ensureSession = useCallback(async (): Promise<string> => {
    if (sessionId) return sessionId;
    const created = await api.createWebChatSession();
    setSessionId(created.session_id);
    return created.session_id;
  }, [sessionId]);

  const stopGeneration = useCallback(() => {
    const activeSession = activeRequestSessionRef.current ?? sessionId;
    abortRef.current?.abort();
    setSending(false);
    if (activeSession) {
      void api.cancelWebChatGeneration(activeSession).catch((err: unknown) => {
        setError(formatError(err));
      });
    }
  }, [sessionId]);

  const resetChat = useCallback(() => {
    stopGeneration();
    setSearchParams(new URLSearchParams(), { replace: true });
    setSessionId(null);
    setMessages([]);
    setError(null);
    setInput("");
    setAttachments([]);
    setLastFailedRequest(null);
    inputRef.current?.focus();
  }, [setSearchParams, stopGeneration]);

  const uploadFiles = useCallback(
    async (files: FileList | File[]) => {
      const selectedFiles = Array.from(files);
      if (selectedFiles.length === 0) return;
      setError(null);
      setUploading(true);
      try {
        const activeSession = await ensureSession();
        const uploaded: WebChatAttachment[] = [];
        for (const file of selectedFiles) {
          uploaded.push(await api.uploadWebChatAttachment(activeSession, file));
        }
        setAttachments((current) => [...current, ...uploaded]);
      } catch (err) {
        setError(formatError(err));
      } finally {
        setUploading(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
    },
    [ensureSession],
  );

  const submitMessage = useCallback(
    async (overrideText?: string, overrideAttachments?: WebChatAttachment[]) => {
      const pendingAttachments = overrideAttachments ?? attachments;
      let text = (overrideText ?? input).trim();
      if (!text && pendingAttachments.length > 0) {
        text = "Please review the attached files.";
      }
      if (!text || sending) return;

      const optimisticText =
        pendingAttachments.length > 0
          ? `${text}\n\n${pendingAttachments
              .map((attachment) => `[attached: ${attachment.filename}]`)
              .join("\n")}`
          : text;

      setError(null);
      setSending(true);
      setLastFailedRequest(null);
      setInput("");
      setMessages((current) => [...current, { role: "user", content: optimisticText }]);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const activeSessionId = await ensureSession();
        activeRequestSessionRef.current = activeSessionId;
        const request: WebChatSendRequest = {
          message: text,
          attachment_ids: pendingAttachments.map((attachment) => attachment.id),
        };
        if (selectedModel) {
          request.provider = selectedModel.provider;
          request.model = selectedModel.model;
        }
        const result = await api.sendWebChatMessage(activeSessionId, request, {
          signal: controller.signal,
        });
        setSessionId(result.session_id);
        setMessages(result.messages);
        setAttachments([]);
        const next = new URLSearchParams(searchParams);
        next.set("resume", result.session_id);
        setSearchParams(next, { replace: true });
      } catch (err) {
        const stopped = controller.signal.aborted;
        setError(stopped ? "Generation stopped." : formatError(err));
        setLastFailedRequest({ text, attachments: pendingAttachments });
        setInput(text);
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null;
          activeRequestSessionRef.current = null;
        }
        setSending(false);
        inputRef.current?.focus();
      }
    },
    [attachments, ensureSession, input, searchParams, selectedModel, sending, setSearchParams],
  );

  useEffect(() => {
    let cancelled = false;
    api
      .getModelOptions()
      .then((payload) => {
        if (!cancelled) setSelectedModel(initialModelFromOptions(payload));
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(formatError(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!resumeParam) {
      let cancelled = false;
      Promise.resolve().then(() => {
        if (cancelled) return;
        setSessionId(null);
        setMessages([]);
        setError(null);
      });
      return () => {
        cancelled = true;
      };
    }

    let cancelled = false;
    const timer = window.setTimeout(() => {
      if (cancelled) return;
      setLoading(true);
      setError(null);

      api
        .getSessionLatestDescendant(resumeParam)
        .then((res) => {
          if (cancelled) return resumeParam;
          const resolved = res.session_id || resumeParam;
          if (resolved !== resumeParam) {
            const next = new URLSearchParams(searchParams);
            next.set("resume", resolved);
            setSearchParams(next, { replace: true });
          }
          return resolved;
        })
        .then((resolved) => api.getSessionMessages(resolved))
        .then((res) => {
          if (cancelled) return;
          setSessionId(res.session_id);
          setMessages(res.messages);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setError(formatError(err));
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 0);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [resumeParam, searchParams, setSearchParams]);

  useEffect(() => {
    if (!isActive) return;
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, sending, isActive]);

  useEffect(() => {
    if (!isActive) return;
    setEnd(
      <div className="flex items-center gap-2">
        <Button type="button" size="sm" ghost onClick={resetChat} title="New chat" aria-label="New chat">
          <Plus className="h-4 w-4" />
        </Button>
        <Button
          type="button"
          size="sm"
          ghost
          disabled={!assistantText}
          onClick={() => void copyText(assistantText)}
          title="Copy last response"
          aria-label="Copy last response"
        >
          {copyState === "copied" ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
        </Button>
        {sending ? (
          <Button type="button" size="sm" onClick={stopGeneration} title="Stop generation" aria-label="Stop generation">
            <Square className="h-4 w-4" />
          </Button>
        ) : (
          <Button
            type="button"
            size="sm"
            disabled={!canSend}
            onClick={() => void submitMessage()}
            title="Send message"
            aria-label="Send message"
          >
            <Send className="h-4 w-4" />
          </Button>
        )}
      </div>,
    );
    return () => setEnd(null);
  }, [
    assistantText,
    canSend,
    copyState,
    copyText,
    isActive,
    resetChat,
    sending,
    setEnd,
    stopGeneration,
    submitMessage,
  ]);

  useEffect(() => {
    return () => {
      if (copyResetRef.current) clearTimeout(copyResetRef.current);
      abortRef.current?.abort();
    };
  }, []);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submitMessage();
  }

  function onComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitMessage();
    }
  }

  const retryFailed = () => {
    if (!lastFailedRequest) return;
    void submitMessage(lastFailedRequest.text, lastFailedRequest.attachments);
  };

  const regenerateLast = () => {
    const text = latestUserText(messages).trim();
    if (text) void submitMessage(text, []);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PluginSlot name="chat:top" />
      <div className="flex min-h-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-3">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center border border-border bg-background/60">
              <MessageSquare className="h-4 w-4 text-foreground" />
            </div>
            <div className="min-w-0">
              <h2 className="truncate text-base font-semibold text-foreground">Chat</h2>
              <p className="truncate text-xs text-muted-foreground">{sessionLabel}</p>
            </div>
          </div>
          <div className="flex min-w-0 flex-wrap items-center justify-end gap-2">
            <button
              type="button"
              onClick={() => setModelPickerOpen(true)}
              className="flex min-w-0 max-w-[24rem] items-center gap-2 border border-border bg-background/70 px-3 py-2 text-left text-xs text-muted-foreground hover:text-foreground"
              title="Switch model"
            >
              <Cpu className="h-4 w-4 shrink-0" />
              <span className="truncate">{modelLabel}</span>
            </button>
            {copyState === "copied" ? <span className="text-xs text-muted-foreground">Copied</span> : null}
            {loading ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
          </div>
        </header>

        <main className="min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6">
          {visibleMessages.length === 0 && !loading ? (
            <div className="mx-auto flex min-h-[45vh] max-w-2xl flex-col justify-center gap-4 text-center">
              <div className="mx-auto flex h-12 w-12 items-center justify-center border border-border bg-background/60">
                <MessageSquare className="h-5 w-5" />
              </div>
              <div className="space-y-2">
                <h3 className="text-lg font-semibold text-foreground">Start a chat</h3>
              </div>
            </div>
          ) : null}

          <div className="mx-auto flex w-full max-w-4xl flex-col gap-4">
            {visibleMessages.map((message, index) => {
              const isUser = message.role === "user";
              const text = messageText(message);
              return (
                <article
                  key={`${message.role}-${index}`}
                  className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}
                >
                  <div
                    className={cn(
                      "group/message max-w-[min(42rem,92%)] border px-4 py-3 text-sm leading-relaxed shadow-sm",
                      isUser
                        ? "border-foreground/20 bg-foreground text-background"
                        : "border-border bg-background/70 text-foreground",
                    )}
                  >
                    {isUser ? (
                      <p className="whitespace-pre-wrap break-words">{text}</p>
                    ) : (
                      <Markdown content={text || "(empty response)"} />
                    )}
                    <div
                      className={cn(
                        "mt-2 flex justify-end opacity-0 transition-opacity group-hover/message:opacity-100",
                        isUser ? "text-background/70" : "text-muted-foreground",
                      )}
                    >
                      <button
                        type="button"
                        onClick={() => void copyText(text)}
                        className="inline-flex items-center gap-1 text-xs hover:underline"
                      >
                        <Copy className="h-3.5 w-3.5" />
                        Copy
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
            {sending ? (
              <article className="flex justify-start">
                <div className="flex items-center gap-3 border border-border bg-background/70 px-4 py-3 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span>Thinking</span>
                  <button
                    type="button"
                    onClick={stopGeneration}
                    className="inline-flex items-center gap-1 text-xs text-foreground hover:underline"
                  >
                    <Square className="h-3.5 w-3.5" />
                    Stop
                  </button>
                </div>
              </article>
            ) : null}
            <div ref={endRef} />
          </div>
        </main>

        {error ? (
          <div className="mx-4 mb-3 flex flex-wrap items-start gap-3 border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive sm:mx-6">
            <span className="min-w-0 flex-1 break-words">{error}</span>
            {lastFailedRequest ? (
              <button type="button" className="inline-flex items-center gap-1 hover:text-foreground" onClick={retryFailed}>
                <RotateCcw className="h-4 w-4" />
                Retry
              </button>
            ) : null}
            <button
              type="button"
              className="shrink-0 text-destructive hover:text-foreground"
              onClick={() => setError(null)}
              aria-label="Dismiss error"
              title="Dismiss error"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        ) : null}

        <form onSubmit={onSubmit} className="border-t border-border bg-background/80 px-4 py-3 sm:px-6">
          <div className="mx-auto flex max-w-4xl flex-col gap-2">
            {attachments.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {attachments.map((attachment) => (
                  <div
                    key={attachment.id}
                    className="inline-flex max-w-full items-center gap-2 border border-border bg-background/70 px-2 py-1 text-xs text-muted-foreground"
                  >
                    <Paperclip className="h-3.5 w-3.5 shrink-0" />
                    <span className="truncate">
                      {attachment.filename} ({formatBytes(attachment.size)})
                    </span>
                    <button
                      type="button"
                      onClick={() =>
                        setAttachments((current) =>
                          current.filter((item) => item.id !== attachment.id),
                        )
                      }
                      aria-label={`Remove ${attachment.filename}`}
                      title="Remove attachment"
                      className="text-muted-foreground hover:text-foreground"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            ) : null}

            <div className="flex items-end gap-2">
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(event) => {
                  if (event.currentTarget.files) void uploadFiles(event.currentTarget.files);
                }}
              />
              <Button
                type="button"
                ghost
                className="h-11 shrink-0"
                disabled={sending || uploading}
                onClick={() => fileInputRef.current?.click()}
                title="Attach files"
                aria-label="Attach files"
              >
                {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Paperclip className="h-4 w-4" />}
              </Button>
              <textarea
                ref={inputRef}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={onComposerKeyDown}
                rows={1}
                placeholder="Message Hermes"
                className="max-h-48 min-h-11 flex-1 resize-y border border-border bg-background px-3 py-2 text-sm leading-relaxed text-foreground placeholder:text-muted-foreground focus-visible:border-foreground/30 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-foreground/25"
                disabled={sending}
              />
              <Button
                type="button"
                ghost
                className="h-11 shrink-0"
                disabled={sending || visibleMessages.length === 0}
                onClick={regenerateLast}
                title="Regenerate last response"
                aria-label="Regenerate last response"
              >
                <RefreshCw className="h-4 w-4" />
              </Button>
              <Button
                type="button"
                ghost
                className="h-11 shrink-0"
                disabled={sending || (visibleMessages.length === 0 && attachments.length === 0 && !input.trim())}
                onClick={() => {
                  setInput("");
                  setAttachments([]);
                  setError(null);
                }}
                title="Clear composer"
                aria-label="Clear composer"
              >
                <Trash2 className="h-4 w-4" />
              </Button>
              {sending ? (
                <Button type="button" className="h-11 shrink-0" onClick={stopGeneration} title="Stop generation">
                  <Square className="h-4 w-4" />
                </Button>
              ) : (
                <Button
                  type="submit"
                  disabled={!canSend}
                  className="h-11 shrink-0"
                  title="Send message"
                  aria-label="Send message"
                >
                  <Send className="h-4 w-4" />
                </Button>
              )}
            </div>
          </div>
        </form>
      </div>
      {modelPickerOpen ? (
        <ModelPickerDialog
          title="Switch Chat Model"
          loader={api.getModelOptions}
          onApply={async ({ provider, model, persistGlobal }) => {
            if (persistGlobal) {
              await api.setModelAssignment({ scope: "main", provider, model });
            }
            setSelectedModel({ provider, model });
            setModelPickerOpen(false);
          }}
          onClose={() => setModelPickerOpen(false)}
        />
      ) : null}
      <PluginSlot name="chat:bottom" />
    </div>
  );
}
