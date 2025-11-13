# LangGraph A2A Conversational Agent (TypeScript)

This project demonstrates an Agent-to-Agent (A2A) conversation using LangGraph with the A2A protocol in TypeScript.

## Setup

1. **Install dependencies:**
   ```bash
   cd typescript
   npm install
   ```

2. **Configure environment variables:**
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and add your `OPENAI_API_KEY`.

## Running the Agents

1. **Start the first agent server:**
   ```bash
   npx @langchain/langgraph-cli dev --port 2024
   ```
   Copy the `assistant_id` from the output.

2. **In another terminal, start the second agent server:**
   ```bash
   npx @langchain/langgraph-cli dev --port 2025
   ```
   Copy the `assistant_id` from this output as well.

3. **Configure the assistant IDs:**
   Add the following to your `.env` file:
   ```
   AGENT_A_ID=<assistant_id_from_port_2024>
   AGENT_B_ID=<assistant_id_from_port_2025>
   ```

4. **Run the conversation simulation:**
   ```bash
   npm run conversation
   ```

This will simulate a conversation between the two agents, with each agent responding to the other's messages.

## Architecture

The implementation consists of two main components:

### 1. LangGraph Agent ([src/langgraph_agent.ts](src/langgraph_agent.ts))
- Defines a conversational agent using LangGraph's StateGraph
- Uses OpenAI's GPT-4o-mini for responses
- Maintains message history in state
- Configured for brief, engaging responses (max 100 tokens)

### 2. A2A Conversation Orchestrator ([src/a2a_conversation.ts](src/a2a_conversation.ts))
- Sends messages between two agent instances using the A2A protocol
- Uses JSON-RPC 2.0 format for agent communication
- Orchestrates 3 rounds of back-and-forth conversation
- Displays conversation with colored output for each agent

## Project Structure

```
typescript/
├── src/
│   ├── langgraph_agent.ts       # Agent definition and logic
│   └── a2a_conversation.ts      # Conversation orchestrator
├── package.json                 # Dependencies and scripts
├── tsconfig.json                # TypeScript configuration
├── .env.example                 # Environment template
└── README.md                    # This file
```

## Scripts

- `npm run dev` - Watch mode for development
- `npm run build` - Build TypeScript to JavaScript
- `npm run conversation` - Run the A2A conversation simulation

## Notes

- Each agent runs as a separate LangGraph server on different ports (2024 and 2025)
- Both agents use the same logic (GPT-4o-mini) but maintain independent state
- The A2A protocol enables standardized communication between agents
- Responses are kept brief (100 token limit) for concise conversations
