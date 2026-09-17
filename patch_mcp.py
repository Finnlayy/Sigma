import re
with open("app/mcp/KrakenMCPBridge.py", "r") as f:
    content = f.read()

content = re.sub(
    r'"passkeyIntercept": "armed",',
    '"passkeyIntercept": "armed",\n            "available": not QUARANTINED,\n            "quarantined": QUARANTINED,',
    content
)

with open("app/mcp/KrakenMCPBridge.py", "w") as f:
    f.write(content)
