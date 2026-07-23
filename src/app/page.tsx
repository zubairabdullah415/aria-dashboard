"use client";

/**
 * Aria — Customer-Facing Chat Widget
 * LiftUp SaaS · AI Reservation Concierge
 * ────────────────────────────────────────────────────────────────────────────
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChatHeader } from "@/components/aria/chat-header";
import { ChatBubble } from "@/components/aria/chat-bubble";
import { TypingIndicator } from "@/components/aria/typing-indicator";
import { ChatInput } from "@/components/aria/chat-input";
import {
  getInitialGreeting,
  getSessionToken,
  resetSession,
} from "@/lib/aria/client";
import { ariaConfig } from "@/lib/aria/config";
import type { ChatMessage } from "@/lib/aria/types";

export default function AriaWidgetPage() {
  const [sessionToken, setSessionToken] = useState<string>("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isTyping, setIsTyping] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // ── Boot: load (or create) session token + seed greeting ─────────────────
  useEffect(() => {
    const token = getSessionToken();
    setSessionToken(token);
    setMessages([getInitialGreeting(token)]);
  }, []);

  // ── Auto-scroll to bottom ───────────────────────────────────────────────
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, isTyping]);

  const lastAssistantId = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "assistant") return messages[i].id;
    }
    return null;
  }, [messages]);

  // ── Main Chat Logic (Integrated) ─────────────────────────────────────────
  const handleSendMessage = useCallback(
    async (userText: string) => {
      if (!userText.trim() || isTyping) return;
      setError(null);

      // 1. Show user message immediately
      const newMessages = [...messages, { id: Date.now().toString(), role: 'user', content: userText, timestamp: Date.now() }];
      setMessages(newMessages);
      setIsTyping(true);

      try {
        const response = await fetch(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/widget/chat`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Api-Key': process.env.NEXT_PUBLIC_RESTAURANT_API_KEY || "",
            'X-Session-Token': sessionToken
          },
          body: JSON.stringify({ message: userText }),
        });

        const data = await response.json();

        if (response.ok) {
          if (data.session_token) setSessionToken(data.session_token);

          // 2. Show Aria's reply
          setMessages([...newMessages, { id: Date.now().toString() + "aria", role: 'assistant', content: data.reply, timestamp: Date.now() }]);

          if (data.booking_complete) {
            alert(`Success! Confirmation Code: ${data.confirmation_code}`);
          }
        } else {
          throw new Error(data.detail || "Connection error");
        }
      } catch (err) {
        setError("Sorry, I am having trouble connecting right now.");
      } finally {
        setIsTyping(false);
      }
    },
    [sessionToken, isTyping, messages]
  );

  const handleReset = useCallback(() => {
    const token = resetSession();
    setSessionToken(token);
    setMessages([getInitialGreeting(token)]);
    setError(null);
  }, []);

  return (
    <div className="min-h-[100dvh] flex flex-col bg-background">
      <ChatHeader />

      <main
        ref={scrollRef}
        className="flex-1 overflow-y-auto scroll-thin"
        role="log"
        aria-live="polite"
      >
        <div className="mx-auto max-w-2xl px-3 py-4 sm:px-4 sm:py-6 space-y-4">
          {messages.map((m) => (
            <ChatBubble
              key={m.id}
              message={m}
              onQuickReplyPick={handleSendMessage}
              isLastAssistant={m.id === lastAssistantId}
            />
          ))}

          {isTyping && <TypingIndicator />}

          {error && (
            <div className="mx-auto max-w-md rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive text-center">
              {error}
            </div>
          )}
          <div ref={bottomRef} className="h-1" aria-hidden="true" />
        </div>
      </main>

      <ChatInput onSend={handleSendMessage} disabled={isTyping} />

      {ariaConfig.demoMode && (
        <button
          onClick={handleReset}
          className="fixed bottom-24 right-3 z-40 rounded-full bg-card/95 border border-border shadow-md px-3 py-1.5 text-[11px] font-medium text-muted-foreground"
        >
          Demo Mode · Reset
        </button>
      )}
    </div>
  );
}