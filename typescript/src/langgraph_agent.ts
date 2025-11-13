/**
 * LangGraph A2A conversational agent.
 *
 * Supports the A2A protocol with messages input for conversational interactions.
 */

import { ChatOpenAI } from "@langchain/openai";
import { Annotation, StateGraph } from "@langchain/langgraph";
import { type BaseMessage, SystemMessage, AIMessage } from "@langchain/core/messages";
import "dotenv/config";

/**
 * Input state for the agent.
 *
 * Defines the initial structure for A2A conversational messages.
 */
const MessagesState = Annotation.Root({
  messages: Annotation<BaseMessage[]>({
    reducer: (x, y) => x.concat(y),
    default: () => [],
  }),
});

type MessagesStateType = typeof MessagesState.State;

/**
 * Process conversational messages and returns output using OpenAI.
 */
async function callModel(state: MessagesStateType): Promise<Partial<MessagesStateType>> {
  // Initialize OpenAI client
  const model = new ChatOpenAI({
    model: "gpt-4o-mini",
    temperature: 0.7,
    maxTokens: 100,
    apiKey: process.env.OPENAI_API_KEY,
  });

  // Get the latest user message
  const latestMessage = state.messages[state.messages.length - 1];
  const userContent = latestMessage?.content?.toString() || "No message content";

  try {
    // Create messages for OpenAI API
    const messages = [
      new SystemMessage(
        "You are a helpful conversational agent. Keep responses brief and engaging."
      ),
      ...state.messages,
    ];

    // Make OpenAI API call
    const response = await model.invoke(messages);

    return {
      messages: [response],
    };
  } catch (error) {
    const errorMessage = error instanceof Error ? error.message : String(error);
    const truncatedError = errorMessage.substring(0, 50);

    return {
      messages: [
        new AIMessage(
          `I received your message but had trouble processing it. Error: ${truncatedError}...`
        ),
      ],
    };
  }
}

// Define the graph
export const graph = new StateGraph(MessagesState)
  .addNode("callModel", callModel)
  .addEdge("__start__", "callModel")
  .addEdge("callModel", "__end__")
  .compile();
