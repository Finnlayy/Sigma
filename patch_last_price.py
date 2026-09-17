import re
with open("tests/test_kraken_single_book.py", "r") as f:
    content = f.read()

content = re.sub(
    r'lambda self, sym: 0\.0',
    r'lambda self, sym: 77210.9664',
    content
)

with open("tests/test_kraken_single_book.py", "w") as f:
    f.write(content)
