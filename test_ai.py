import os, sys
from openai import OpenAI

key = os.getenv("OPENAI_API_KEY")
print("HAS_KEY:", bool(key), "LEN:", len(key) if key else 0, "PREFIX:", (key[:7] if key else ""))
if not key:
    print("ERROR: OPENAI_API_KEY mangler. Kjør: source ~/.zshrc")
    sys.exit(1)

try:
    client = OpenAI(api_key=key)
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":"Svar kun: OK"}],
        temperature=0.0,
    )
    print("RESPONSE:", r.choices[0].message.content)
except Exception as e:
    print("EXC_TYPE:", type(e).__name__)
    print("EXC_MSG:", str(e))
    sys.exit(1)
