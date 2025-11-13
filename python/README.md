# LangGraph A2A Conversational Agent

This project demonstrates an Agent-to-Agent (A2A) conversation using LangGraph with the A2A protocol.

## Setup

1. **Create a virtual environment:**
   ```bash
   python -m venv venv
   ```

2. **Activate the virtual environment:**
   ```bash
   source venv/bin/activate  # On macOS/Linux
   # or
   venv\Scripts\activate  # On Windows
   ```

3. **Copy the environment example file and configure:**
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and add your required variables (e.g., `OPENAI_API_KEY`).

4. **Install dependencies:**
   ```bash
   pip install -r python/requirements.txt
   ```

## Running the Agents

1. **Start the first agent server:**
   ```bash
   cd python
   langgraph dev --port 2024
   ```
   Copy the `assistant_id` from the output.

2. **In another terminal, start the second agent server:**
   ```bash
   cd python
   langgraph dev --port 2025
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
   python a2a_conversation.py
   ```

This will simulate a conversation between the two agents, with each agent responding to the other's messages.

