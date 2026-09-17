with open("sigma/strategies/dynamic_pine_provisioner.py", "r") as f:
    code = f.read()
    
# replace the weird pattern with a simple string
code = code.replace(
    'pattern = f\'"\\\' + "{req.strategy_id}_{action}_{seq:02d}_" + str(time) + \\\'"\'',
    'pattern = f"{req.strategy_id}_{action}_{seq:02d}_{{{{timenow}}}}"'
)

with open("sigma/strategies/dynamic_pine_provisioner.py", "w") as f:
    f.write(code)
