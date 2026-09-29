import re
with open(".jules/nexus.md", "r") as f:
    text = f.read()

# Remove the duplicate entry for 2025-05-18
text = re.sub(r'## 2025-05-18.*?(?=##|\Z)', '', text, flags=re.DOTALL)
with open(".jules/nexus.md", "w") as f:
    f.write(text.strip() + "\n")
