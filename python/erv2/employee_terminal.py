import asyncio
import os

import httpx


EMPLOYEE_CHAT_URL = os.getenv(
    "ER_EMPLOYEE_CHAT_URL", "http://127.0.0.1:2026/employee/chat"
)
THREAD_ID = os.getenv("ER_EMPLOYEE_THREAD_ID", "er-employee-1")


async def main() -> None:
    print("ED employee terminal. Type 'exit' to quit.\n")
    async with httpx.AsyncClient(timeout=60.0) as client:
        while True:
            text = input("ED Director> ").strip()
            if text.lower() in {"exit", "quit"}:
                break
            if not text:
                continue

            payload = {"text": text, "thread_id": THREAD_ID}
            try:
                resp = await client.post(EMPLOYEE_CHAT_URL, json=payload)
                resp.raise_for_status()
                data = resp.json()
                print(f"ER agent> {data.get('reply', '(no reply)')}\n")
            except Exception as e:
                print(f"[ERROR] {type(e).__name__}: {e}\n")


if __name__ == "__main__":
    asyncio.run(main())
