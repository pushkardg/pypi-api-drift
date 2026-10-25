import pandas as pd
from flask import escape
from urllib3.util.retry import Retry

orders = pd.read_csv("orders.csv", error_bad_lines=False)
df = pd.DataFrame({"id": [1, 2]})
df = df.append({"id": 3}, ignore_index=True)
retry = Retry(total=3, method_whitelist=["GET"])
