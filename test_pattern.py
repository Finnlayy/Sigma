import json

base_payload = {"id": "{{strategy.order.id}}"}
json_str = json.dumps(base_payload)

# we want the final alert_message string in Pine to be:
# '{"id": "strat_1_BUY_01_" + str(time) + "'}'

# We replace "{{strategy.order.id}}" with:
pattern = f'"\' + "{1}_{2}_{3:02d}_" + str(time) + \'"'
json_str = json_str.replace('"{{strategy.order.id}}"', pattern)
print(f"alert_message='{json_str}'")
