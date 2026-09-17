import sys

with open("sigma/strategies/dynamic_pine_provisioner.py", "r") as f:
    code = f.read()

import re
old_func = re.search(r"def _build_pine_alert.*?return json_str.replace\(\"'\", \"\\\\\\'\"\)", code, re.DOTALL).group(0)

new_func = """def _build_pine_alert(req: ProvisionRequest, action: str, seq: int, reason: str = "") -> str:
    \"\"\"Builds a Schema A JSON string for a specific alert, replacing dynamic Pine vars.\"\"\"
    base_payload = build_alert_payload(
        req.strategy_id, req.webhook_secret, execution_mode="kraken_paper", fixed_leverage=req.fixed_leverage
    )
    base_payload["action"] = action
    base_payload["symbol"] = req.symbol
    json_str = json.dumps(base_payload)
    
    # We replace "{{strategy.order.id}}" with:
    pattern = f'"\\' + "{req.strategy_id}_{action}_{seq:02d}_" + str(time) + \\'"'
    json_str = json_str.replace('"{{strategy.order.id}}"', pattern)
    
    return json_str"""

code = code.replace(old_func, new_func)
with open("sigma/strategies/dynamic_pine_provisioner.py", "w") as f:
    f.write(code)
