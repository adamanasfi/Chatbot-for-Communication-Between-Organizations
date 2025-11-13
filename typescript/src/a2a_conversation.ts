#!/usr/bin/env node
/**
 * Agent-to-Agent conversation simulation using LangGraph A2A protocol.
 */

import "dotenv/config";

interface A2AMessage {
  jsonrpc: string;
  id: string;
  method: string;
  params: {
    message: {
      role: string;
      parts: Array<{ kind: string; text: string }>;
    };
    messageId: string;
    thread: { threadId: string };
  };
}

interface A2AResponse {
  result: {
    artifacts: Array<{
      parts: Array<{ text: string }>;
    }>;
  };
}

/**
 * Send a message to an agent and return the response text.
 */
async function sendMessage(
  port: number,
  assistantId: string,
  text: string
): Promise<string> {
  const url = `http://127.0.0.1:${port}/a2a/${assistantId}`;
  const payload: A2AMessage = {
    jsonrpc: "2.0",
    id: "",
    method: "message/send",
    params: {
      message: {
        role: "user",
        parts: [{ kind: "text", text }],
      },
      messageId: "",
      thread: { threadId: "" },
    },
  };

  try {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      const text = await response.text();
      console.error(`Response error from port ${port}: ${response.status} - ${text}`);
      return `Error from port ${port}: ${response.status}`;
    }

    const result = (await response.json()) as A2AResponse;
    return result.result.artifacts[0].parts[0].text;
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : String(error);
    console.error(`Error sending message to port ${port}:`, errorMessage);
    return `Error from port ${port}: ${errorMessage}`;
  }
}

/**
 * Simulate a conversation between two agents.
 */
async function simulateConversation(): Promise<void> {
  const agentAId = process.env.AGENT_A_ID;
  const agentBId = process.env.AGENT_B_ID;

  if (!agentAId || !agentBId) {
    console.error("Set AGENT_A_ID and AGENT_B_ID environment variables");
    process.exit(1);
  }

  let message = "Hello! Let's have a conversation.";

  for (let i = 0; i < 3; i++) {
    console.log(`--- Round ${i + 1} ---`);

    // Agent A responds
    message = await sendMessage(2024, agentAId, message);
    console.log(`🔵 Agent A: ${message}`);

    // Agent B responds
    message = await sendMessage(2025, agentBId, message);
    console.log(`🔴 Agent B: ${message}`);
    console.log();
  }
}

// Run the simulation
simulateConversation().catch((error) => {
  console.error("Error running conversation:", error);
  process.exit(1);
});
