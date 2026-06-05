import { Button } from "@nous-research/ui/ui/components/button";
import { Copy, Loader2, MessageSquare, Plus, Send, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useSearchParams } from "react-router-dom";

import { Markdown } from "@/components/Markdown";
import { usePageHeader } from "@/contexts/usePageHeader";
import { api, type SessionMessage } from "@/lib/api";
import { cn } from "@/lib/utils";
import { PluginSlot } from "@/plugins";

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
    if (msg.role === "assistant") {
      return messageText(msg);
    }
  }
  return "";
}

export default function ChatPage({ isActive = true }: { isActive?: boolean }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const resumeParam = searchParams.get("resume");
  const [sessionId, setSessionId] = useState<string | null>(resumeParam);
  const [messages, setMessages] = useState<SessionMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copyState, setCopyState] = useState<"idle" | "copied">("idle");
  const endRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const copyResetRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { setEnd } = usePageHeader();

  const visibleMessages = useMemo(
    () => messages.filter((message) => message.role === "user" || message.role === "assistant"),
    [messages],
  );

  const assistantText = useMemo(() => latestAssistantText(messages), [messages]);
  const sessionLabel = useMemo(() => compactSessionLabel(sessionId), [sessionId]);

  const resetChat = useCallback(() => {
    setSearchParams(new URLSearchParams(), { replace: true });
    setSessionId(null);
    setMessages([]);
    setError(null);
    setInput("");
    inputRef.current?.focus();
  }, [setSearchParams]);

  const ensureSession = useCallback(async (): Promise<string> => {
    if (sessionId) return sessionId;
    const created = await api.createWebChatSession();
    setSessionId(created.session_id);
    return created.session_id;
  }, [sessionId]);

  const copyLastAssistant = useCallback(async () => {
    if (!assistantText) return;
    await navigator.clipboard.writeText(assistantText);
    setCopyState("copied");
    if (copyResetRef.current) clearTimeout(copyResetRef.current);
    copyResetRef.current = setTimeout(() => setCopyState("idle"), 1400);
  }, [assistantText]);

  const submitMessage = useCallback(async () => {
    const text = input.trim();
    if (!text || sending) return;

    setError(null);
    setSending(true);
    setInput("");
    setMessages((current) => [...current, { role: "user", content: text }]);

    try {
      const activeSessionId = await ensureSession();
      const result = await api.sendWebChatMessage(activeSessionId, text);
      setSessionId(result.session_id);
      setMessages(result.messages);
      const next = new URLSearchParams(searchParams);
      next.set("resume", result.session_id);
      setSearchParams(next, { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chat request failed");
      setInput(text);
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  }, [ensureSession, input, searchParams, sending, setSearchParams]);

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
          setError(err instanceof Error ? err.message : "Failed to load chat session");
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
    const sendDisabled = sending || !input.trim();
    setEnd(
      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="sm"
          ghost
          onClick={resetChat}
          title="New chat"
          aria-label="New chat"
        >
          <Plus className="h-4 w-4" />
        </Button>
        <Button
          type="button"
          size="sm"
          ghost
          disabled={!assistantText}
          onClick={() => void copyLastAssistant()}
          title="Copy last response"
          aria-label="Copy last response"
        >
          <Copy className="h-4 w-4" />
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={sendDisabled}
          onClick={() => void submitMessage()}
          title="Send message"
          aria-label="Send message"
        >
          {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
        </Button>
      </div>,
    );
    return () => setEnd(null);
  }, [assistantText, copyLastAssistant, input, isActive, resetChat, sending, setEnd, submitMessage]);

  useEffect(() => {
    return () => {
      if (copyResetRef.current) clearTimeout(copyResetRef.current);
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
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            {copyState === "copied" ? <span>Copied</span> : null}
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
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
                <p className="text-sm text-muted-foreground">
                  Ask a question or continue a task from here.
                </p>
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
                      "max-w-[min(42rem,92%)] border px-4 py-3 text-sm leading-relaxed shadow-sm",
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
                  </div>
                </article>
              );
            })}
            {sending ? (
              <article className="flex justify-start">
                <div className="flex items-center gap-2 border border-border bg-background/70 px-4 py-3 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span>Thinking</span>
                </div>
              </article>
            ) : null}
            <div ref={endRef} />
          </div>
        </main>

        {error ? (
          <div className="mx-4 mb-3 flex items-start gap-3 border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive sm:mx-6">
            <span className="min-w-0 flex-1 break-words">{error}</span>
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
          <div className="mx-auto flex max-w-4xl items-end gap-2">
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
              type="submit"
              disabled={sending || !input.trim()}
              className="h-11 shrink-0"
              title="Send message"
              aria-label="Send message"
            >
              {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            </Button>
          </div>
        </form>
      </div>
      <PluginSlot name="chat:bottom" />
    </div>
  );
}
