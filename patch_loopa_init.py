with open("app/execution/LoopAPipeline.py", "r") as f:
    lines = f.readlines()

new_lines = []
in_init = False
for line in lines:
    new_lines.append(line)
    if "def __init__" in line:
        in_init = True
    if in_init and "self.rejected = 0" in line:
        new_lines.append("        self._daily_notional_day = ''\n")
        new_lines.append("        self._daily_notional = {'spot': 0.0, 'futures': 0.0}\n")
        in_init = False

with open("app/execution/LoopAPipeline.py", "w") as f:
    f.writelines(new_lines)
