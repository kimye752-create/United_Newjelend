"""Direct test: Perplexity API for NZ pharma news."""
import httpx
import asyncio
import sys
import os

sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv(".env", override=True)
px_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
print("Key:", px_key[:12], "...")

from datetime import date
today = date.today().strftime("%Y-%m-%d")

payload = {
    "model": "sonar-pro",
    "messages": [
        {
            "role": "system",
            "content": (
                "You are a New Zealand (NZ) pharmaceutical market news analyst. "
                "Search for RECENT (2024-2025) news about New Zealand only. "
                "Do NOT include news about Singapore, Australia, or any other country. "
                "Return ONLY a JSON array. All titles in Korean (한국어)."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Today is {today}. "
                "Find latest 2024-2025 New Zealand pharmaceutical news about: "
                "PHARMAC funding changes, Medsafe approvals, Health New Zealand procurement, "
                "NZ medicine supply issues, NZ drug pricing policy. "
                "Return JSON array: "
                '[{"title": "Korean title", "source": "publisher", "date": "YYYY-MM", "link": "url"}]'
            ),
        },
    ],
    "max_tokens": 900,
    "temperature": 0.1,
}

async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            "https://api.perplexity.ai/chat/completions",
            headers={
                "Authorization": f"Bearer {px_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        r.raise_for_status()
        data = r.json()

    content = data["choices"][0]["message"]["content"]
    print("\n=== RAW RESPONSE ===")
    print(content[:3000])

asyncio.run(main())
