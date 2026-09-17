with open("app/mcp/KrakenMCPBridge.py", "r") as f:
    lines = f.readlines()

new_lines = []
skip = False
for line in lines:
    if "def execute(self, tool_name: str, args: Dict[str, Any]," in line:
        new_lines.append(line)
        skip = True
        continue
    if skip and "settings_token: Optional[str] = None) -> Dict[str, Any]:" in line:
        new_lines.append(line)
        new_lines.append("        if QUARANTINED:\n")
        new_lines.append("            return {\"ok\": False, \"error_code\": \"MCP_QUARANTINED\"}\n")
        skip = False
        continue
    if not skip:
        # Don't append if it's the duplicated stuff we injected
        if "if QUARANTINED:" in line: continue
        if 'return {"ok": False, "error_code": "MCP_QUARANTINED"}' in line: continue
        if 'settings_token: Optional[str] = None) -> Dict[str, Any]:' in line and "def execute" not in "".join(new_lines[-3:]): 
            continue
        new_lines.append(line)

with open("app/mcp/KrakenMCPBridge.py", "w") as f:
    f.writelines(new_lines)
